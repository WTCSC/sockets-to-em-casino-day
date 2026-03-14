"""
Virtual Poker Game - Client
Run AFTER server.py: python client.py
"""

import socket
import json
import threading
import sys

# ─────────────────────────────────────────────
#  DISPLAY HELPERS
# ─────────────────────────────────────────────

def fmt_hand(cards):
    if not cards:
        return ""
    w = max(len(c) for c in cards) + 4
    bar = "─" * (w + 2)
    lines = [f"  ┌{bar}┐", f"  │{'  YOUR HAND':^{w+2}}│"]
    for c in cards:
        lines.append(f"  │  {c:<{w}}│")
    lines.append(f"  └{bar}┘")
    return "\n".join(lines)

def fmt_community(cards, stage=""):
    if not cards:
        return ""
    label = f"  Community ({stage}):" if stage else "  Community:"
    return label + "\n" + "\n".join(f"    [{c}]" for c in cards)

def fmt_standings(chips_dict):
    if not chips_dict:
        return ""
    lines = ["\n  ┌── Chip Standings " + "─"*20 + "┐"]
    for name, chips in chips_dict.items():
        lines.append(f"  │  {name:<18} {chips:>7} chips  │")
    lines.append("  └" + "─"*38 + "┘")
    return "\n".join(lines)

def fmt_lobby(data):
    players = data.get("players", [])
    lines = [
        "",
        "  ╔══════════════════════════════════════╗",
        "  ║            LOBBY STATUS              ║",
       f"  ║  Host    : {data.get('host','?'):<27}║",
       f"  ║  Players : {len(players)}/{data.get('max',6):<27}║",
       f"  ║  Chips   : {data.get('chips',1000):<27}║",
       f"  ║  Rounds  : {data.get('rounds',3):<27}║",
        "  ║  In lobby: " + ", ".join(players)[:27].ljust(27) + "║",
        "  ╚══════════════════════════════════════╝",
    ]
    return "\n".join(lines)

# ─────────────────────────────────────────────
#  SHARED STATE
# ─────────────────────────────────────────────

my_chips    = [1000]
my_hand     = [[]]        # current hole cards (display strings)
turn_event  = threading.Event()
pending     = [None]      # stores YOUR_TURN payload
host_event  = threading.Event()
pending_host= [None]      # stores HOST_DECISION payload
is_host     = [False]
done        = [False]

# ─────────────────────────────────────────────
#  MESSAGE HANDLER (runs in listener thread)
# ─────────────────────────────────────────────

def handle_message(msg, sock):
    mtype = msg.get("type")

    if mtype in ("INFO", "WAITING"):
        print(msg.get("msg", ""))

    elif mtype == "LOBBY_STATUS":
        print(fmt_lobby(msg))

    elif mtype == "HOST_LOBBY":
        is_host[0] = True
        print(msg.get("msg", ""))
        host_event.set()   # unblock main thread so it can accept lobby commands

    elif mtype == "HAND":
        my_chips[0]  = msg.get("chips", my_chips[0])
        my_hand[0]   = msg.get("display", [])
        print(fmt_hand(my_hand[0]))
        print(f"  Chips: {my_chips[0]}")

    elif mtype == "COMMUNITY":
        print(fmt_community(msg.get("display", []), msg.get("stage", "")))

    elif mtype == "TURN":
        name = msg.get("player", "?")
        print(f"\n  ► {name}'s turn   (Pot: {msg.get('pot',0)}  "
              f"Current bet: {msg.get('current_bet',0)})")

    elif mtype == "YOUR_TURN":
        my_chips[0] = msg.get("chips", my_chips[0])
        my_hand[0]  = msg.get("hand", my_hand[0])
        pending[0]  = msg
        turn_event.set()

    elif mtype == "SHOWDOWN":
        print("\n  ══ SHOWDOWN ══")
        print(fmt_hand(msg.get("hand", [])))
        if msg.get("community"):
            print(fmt_community(msg.get("community", []), "Final"))
        print(f"  Best hand: {msg.get('best','?')}")

    elif mtype == "REVEAL":
        print("\n  ══ ALL HANDS REVEALED ══")
        for entry in msg.get("hands", []):
            print(f"  {entry['name']:<18} {entry['best']}  {entry['hand']}")

    elif mtype == "WINNER":
        print("\n  ★ ★ ★  WINNER  ★ ★ ★")
        pot    = msg.get("pot", 0)
        player = msg.get("player", "?")
        hand   = msg.get("hand", "")
        if msg.get("split"):
            print(f"  Split pot ({pot})!  Winners: {player}  ({hand})")
        else:
            reason = msg.get("reason", "")
            if reason:
                print(f"  {player} wins {pot} chips! ({reason})")
            else:
                print(f"  {player} wins {pot} chips with {hand}!")
        chips = msg.get("chips", {})
        if chips:
            print(fmt_standings(chips))

    elif mtype == "STANDINGS":
        print(fmt_standings(msg.get("chips", {})))

    elif mtype == "ERROR":
        print(f"\n  [!] {msg.get('msg','')}")

    elif mtype == "HOST_DECISION":
        pending_host[0] = msg
        host_event.set()

    elif mtype == "KICKED":
        print(f"\n  [!!!] {msg.get('msg','')}")
        done[0] = True
        turn_event.set()
        host_event.set()
        sock.close()
        sys.exit(1)

    elif mtype == "GAME_OVER":
        print("\n  ════════════════════════════════")
        print("          G A M E   O V E R")
        print("  ════════════════════════════════")
        for i, (name, chips) in enumerate(msg.get("standings", []), 1):
            medals = {1:"🥇",2:"🥈",3:"🥉"}
            m = medals.get(i, f"#{i}")
            print(f"  {m}  {name:<18} {chips:>7} chips")
        print()

    elif mtype == "SERVER_CLOSING":
        print(f"\n  {msg.get('msg','Server closed.')}")
        done[0] = True
        turn_event.set()
        host_event.set()

# ─────────────────────────────────────────────
#  LISTENER THREAD
# ─────────────────────────────────────────────

def listener(sock):
    buf = ""
    while not done[0]:
        try:
            data = sock.recv(4096).decode()
            if not data:
                print("\n  [!] Server disconnected.")
                done[0] = True
                turn_event.set()
                host_event.set()
                break
            buf += data
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if line:
                    try:
                        handle_message(json.loads(line), sock)
                    except json.JSONDecodeError:
                        pass
        except (ConnectionResetError, OSError):
            if not done[0]:
                print("\n  [!] Lost connection to server.")
            done[0] = True
            turn_event.set()
            host_event.set()
            break

# ─────────────────────────────────────────────
#  INPUT: GAME ACTION
# ─────────────────────────────────────────────

def get_action(turn_data):
    chips       = turn_data.get("chips", my_chips[0])
    pot         = turn_data.get("pot", 0)
    stage       = turn_data.get("stage", "")
    call_amount = turn_data.get("call_amount", 0)
    current_bet = turn_data.get("current_bet", 0)
    hand        = turn_data.get("hand", my_hand[0])

    # Always reprint hand
    print(fmt_hand(hand))
    community = turn_data.get("community", [])
    if community:
        print(fmt_community(community, stage))

    print(f"\n  Chips: {chips}   Pot: {pot}   Stage: {stage}")
    if call_amount > 0:
        print(f"  To call: {call_amount}  (current bet: {current_bet})")
        print("  Options: CALL  |  RAISE <amount>  |  FOLD  |  QUIT")
    else:
        print("  Options: CHECK  |  BET <amount>  |  FOLD  |  QUIT")

    while True:
        try:
            raw = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            return {"action": "QUIT"}

        if not raw:
            continue

        upper = raw.upper()

        if upper == "QUIT":
            return {"action": "QUIT"}
        if upper == "FOLD":
            return {"action": "FOLD"}
        if upper == "CHECK":
            return {"action": "CHECK"}
        if upper == "CALL":
            return {"action": "CALL"}

        for verb in ("BET", "RAISE"):
            if upper.startswith(verb):
                parts = upper.split()
                if len(parts) != 2:
                    print(f"  Usage: {verb} <amount>  e.g. {verb} 50")
                    break
                try:
                    amount = int(parts[1])
                except ValueError:
                    print(f"  '{parts[1]}' is not a valid number.")
                    break
                if amount <= 0:
                    print("  Amount must be positive.")
                    break
                if amount > chips:
                    print(f"  You only have {chips} chips.")
                    break
                return {"action": verb, "amount": amount}
            # only match if upper actually starts with verb
            else:
                continue
            break  # inner break hit — re-prompt outer loop
        else:
            print(f"  Unknown: '{raw}'. Try CALL, BET, RAISE, CHECK, FOLD, or QUIT.")

# ─────────────────────────────────────────────
#  INPUT: LOBBY COMMANDS (host only)
# ─────────────────────────────────────────────

def get_lobby_command():
    print("\n  Lobby commands: PLAYERS <n> | CHIPS <n> | ROUNDS <n> | START")
    while True:
        try:
            raw = input("  host> ").strip()
        except (EOFError, KeyboardInterrupt):
            return {"cmd": "END", "value": ""}
        if not raw:
            continue
        parts = raw.split(None, 1)
        cmd   = parts[0].upper()
        value = parts[1] if len(parts) > 1 else ""
        return {"cmd": cmd, "value": value}

# ─────────────────────────────────────────────
#  INPUT: HOST POST-GAME DECISION
# ─────────────────────────────────────────────

def get_host_decision():
    print("\n  Type PLAY AGAIN to start a new game, or END to close.")
    while True:
        try:
            raw = input("  host> ").strip().upper()
        except (EOFError, KeyboardInterrupt):
            return {"type": "HOST_DECISION_RESPONSE", "cmd": "END"}
        if raw in ("PLAY AGAIN", "END"):
            return {"type": "HOST_DECISION_RESPONSE", "cmd": raw}
        print("  Type PLAY AGAIN or END.")

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    # ── CHANGE THIS to the server's LAN IP ─────────────────────────────────
    HOST = "100.104.207.98"   # <── server's IP (server.py prints it on startup)
    PORT = 5555

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)

    try:
        sock.connect((HOST, PORT))
        sock.settimeout(None)
    except ConnectionRefusedError:
        print(f"\n  [ERROR] Server not found at {HOST}:{PORT}.")
        print("  Make sure server.py is running first.")
        sys.exit(1)
    except socket.timeout:
        print(f"\n  [ERROR] Connection timed out. Check HOST ({HOST}) and PORT ({PORT}).")
        sys.exit(1)
    except OSError as e:
        print(f"\n  [ERROR] Could not connect: {e}")
        sys.exit(1)

    print(f"\n  Connected to {HOST}:{PORT}")

    # ── Name handshake ──────────────────────────────────────────────────────
    buf = ""
    sock.settimeout(15)
    try:
        while True:
            chunk = sock.recv(256).decode()
            buf += chunk
            if "\n" in buf:
                line, buf = buf.split("\n", 1)
                msg = json.loads(line.strip())
                if msg.get("type") == "NAME_REQUEST":
                    break
    except Exception:
        pass
    sock.settimeout(None)

    while True:
        try:
            name = input("  Enter your name: ").strip()[:20]
        except (EOFError, KeyboardInterrupt):
            name = "Player"
        if name:
            break
    sock.sendall((json.dumps({"name": name}) + "\n").encode())
    print(f"\n  Welcome, {name}! Type QUIT at any time to leave.\n")

    # ── Start listener ──────────────────────────────────────────────────────
    t = threading.Thread(target=listener, args=(sock,), daemon=True)
    t.start()

    game_started = [False]

    # ── Main event loop ─────────────────────────────────────────────────────
    while not done[0] and t.is_alive():

        # ── HOST LOBBY PHASE ─────────────────────────────────────────────
        if is_host[0] and not game_started[0]:
            host_event.wait()
            host_event.clear()
            if done[0]:
                break
            if pending_host[0] is not None:
                pass  # HOST_DECISION arrived early; fall to decision block
            else:
                cmd = get_lobby_command()
                try:
                    sock.sendall((json.dumps(cmd) + "\n").encode())
                except OSError:
                    break
                if cmd.get("cmd") == "START":
                    game_started[0] = True
                continue

        # ── HOST POST-GAME DECISION ──────────────────────────────────────
        if pending_host[0] is not None:
            pending_host[0] = None
            dec = get_host_decision()
            try:
                sock.sendall((json.dumps(dec) + "\n").encode())
            except OSError:
                break
            if dec.get("cmd") == "END":
                break
            continue

        # ── GAME TURN ────────────────────────────────────────────────────
        # Poll so host_event can also interrupt this wait
        signaled = turn_event.wait(timeout=0.5)
        if done[0]:
            break
        if host_event.is_set() and pending_host[0] is not None:
            host_event.clear()
            continue   # re-enter → HOST POST-GAME block
        if not signaled:
            continue

        turn_event.clear()
        data = pending[0]
        pending[0] = None

        if data is None:
            continue

        action = get_action(data)
        if action["action"] == "QUIT":
            try:
                sock.sendall((json.dumps(action) + "\n").encode())
            except Exception:
                pass
            print("\n  Thanks for playing! Goodbye.")
            sock.close()
            sys.exit(0)

        try:
            sock.sendall((json.dumps(action) + "\n").encode())
        except OSError:
            print("  [!] Failed to send. Disconnected?")
            break

    print("\n  Connection closed.")
    try:
        sock.close()
    except Exception:
        pass

if __name__ == "__main__":
    main()