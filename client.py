"""
Virtual Poker Game - Client
Run AFTER server.py is running: python client.py
"""

import socket
import json
import threading
import sys

#  CARD FORMATTER

def format_hand(cards):
    """Pretty-print a list of card names in a box."""
    if not cards:
        return "(no cards)"
    lines = []
    lines.append("  ┌" + "─" * 24 + "┐")
    lines.append("  │  YOUR HAND:              │")
    for card in cards:
        lines.append(f"  │   [{card:<20}]  │")
    lines.append("  └" + "─" * 24 + "┘")
    return "\n".join(lines)

def format_community(cards, stage=""):
    if not cards:
        return ""
    lines = []
    label = f"  COMMUNITY ({stage}):" if stage else "  COMMUNITY:"
    lines.append(label)
    for card in cards:
        lines.append(f"    >> {card}")
    return "\n".join(lines)

def format_standings(chips_dict):
    lines = ["\n  ┌─── CHIP STANDINGS ──────────┐"]
    for name, chips in chips_dict.items():
        lines.append(f"  │  {name:<16} {chips:>6} chips │")
    lines.append("  └─────────────────────────────┘")
    return "\n".join(lines)

#  INPUT VALIDATOR

def get_action(chips, pot, stage):
    """Prompt player for an action, validate input, return JSON-ready dict."""
    print(f"\n  Your chips: {chips}  |  Pot: {pot}  |  Stage: {stage}")
    print("  Actions:  BET <amount>  |  CHECK  |  FOLD  |  QUIT")

    while True:
        try:
            raw = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            return {"action": "QUIT"}

        if not raw:
            print("  Please enter an action.")
            continue

        upper = raw.upper()

        if upper == "QUIT":
            return {"action": "QUIT"}

        if upper == "FOLD":
            return {"action": "FOLD"}

        if upper == "CHECK":
            return {"action": "CHECK"}

        if upper.startswith("BET"):
            parts = upper.split()
            if len(parts) != 2:
                print("  Usage: BET <amount>  e.g. BET 50")
                continue
            # Input validation – catch non-numeric bet
            try:
                amount = int(parts[1])
            except ValueError:
                print(f"  '{parts[1]}' is not a valid number. Please enter a whole number.")
                continue
            if amount <= 0:
                print("  Bet must be a positive number.")
                continue
            if amount > chips:
                print(f"  You only have {chips} chips. Bet less or type FOLD.")
                continue
            return {"action": "BET", "amount": amount}

        print(f"  Unknown action '{raw}'. Try: BET <n>, CHECK, FOLD, or QUIT")

#  MESSAGE HANDLER

# Shared state between listener thread and main thread
my_chips = [1000]   # mutable wrapper so thread can update it
turn_event = threading.Event()
pending_turn = [None]  # stores YOUR_TURN payload


def handle_message(msg, sock):
    mtype = msg.get("type")

    if mtype == "WAITING":
        print(f"\n  {msg.get('msg', '')}")

    elif mtype == "INFO":
        print(msg.get("msg", ""))

    elif mtype == "HAND":
        my_chips[0] = msg.get("chips", my_chips[0])
        print(format_hand(msg.get("display", [])))
        print(f"  Chips: {my_chips[0]}")

    elif mtype == "COMMUNITY":
        print(format_community(msg.get("display", []), msg.get("stage", "")))

    elif mtype == "TURN":
        player = msg.get("player", "?")
        print(f"\n  >> {player}'s turn  (Pot: {msg.get('pot', 0)})")

    elif mtype == "YOUR_TURN":
        # Signal main thread to gather input
        pending_turn[0] = msg
        turn_event.set()

    elif mtype == "SHOWDOWN":
        print("\n  ══ SHOWDOWN ══")
        print(f"  Your hand: {msg.get('hand', [])}")
        print(f"  Community: {msg.get('community', [])}")
        print(f"  Best hand: {msg.get('best', '?')}")

    elif mtype == "WINNER":
        print("\n  ★ ★ ★  WINNER  ★ ★ ★")
        winner = msg.get("player", "?")
        hand = msg.get("hand", "")
        pot = msg.get("pot", 0)
        split = msg.get("split", False)
        if split:
            print(f"  Split pot! Winners: {winner}  (hand: {hand})")
        else:
            print(f"  {winner} wins {pot} chips!  (hand: {hand})")
        chips = msg.get("chips", {})
        if chips:
            print(format_standings(chips))

    elif mtype == "STANDINGS":
        print(format_standings(msg.get("chips", {})))

    elif mtype == "ERROR":
        print(f"\n  [!] Server says: {msg.get('msg', '')}")

    elif mtype == "KICKED":
        print(f"\n  [!!!] {msg.get('msg', '')}")
        print("  You have been removed from the game.")
        sock.close()
        sys.exit(1)

    elif mtype == "GAME_OVER":
        print("\n  ════════════════════════════")
        print("       GAME OVER!")
        print("  ════════════════════════════")
        standings = msg.get("standings", [])
        for i, (name, chips) in enumerate(standings, 1):
            medal = ["🥇", "🥈", "🥉"][i-1] if i <= 3 else f"#{i}"
            print(f"  {medal}  {name:<16}  {chips} chips")
        print()


#  LISTENER THREAD

def listener(sock):
    """Continuously read messages from server and dispatch them."""
    buf = ""
    while True:
        try:
            data = sock.recv(4096).decode()
            if not data:
                print("\n  [!] Server disconnected.")
                turn_event.set()  # Unblock main thread if waiting
                break
            buf += data
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if line:
                    try:
                        msg = json.loads(line)
                        handle_message(msg, sock)
                    except json.JSONDecodeError:
                        pass  # Ignore malformed data
        except (ConnectionResetError, OSError):
            print("\n  [!] Lost connection to server.")
            turn_event.set()
            break

#  MAIN

def main():
    HOST = "127.0.0.1"
    PORT = 5555

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)

    # Wrap connect() — catches server-not-found errors
    try:
        sock.connect((HOST, PORT))
        sock.settimeout(None)  # Remove timeout after connecting
    except ConnectionRefusedError:
        print(f"\n  [ERROR] Server not found at {HOST}:{PORT}.")
        print("  Please check that server.py is running first.")
        sys.exit(1)
    except socket.timeout:
        print(f"\n  [ERROR] Connection timed out. Check the IP ({HOST}) and port ({PORT}).")
        sys.exit(1)
    except OSError as e:
        print(f"\n  [ERROR] Could not connect: {e}")
        sys.exit(1)

    print(f"\n  Connected to poker server at {HOST}:{PORT}!")
    print("  Type QUIT at any time to leave the game.\n")

    # Start background listener thread
    t = threading.Thread(target=listener, args=(sock,), daemon=True)
    t.start()

    # Main loop: wait for YOUR_TURN signal, then send action
    while t.is_alive():
        turn_event.wait()
        turn_event.clear()

        data = pending_turn[0]
        if data is None:
            break  # Server disconnected

        pending_turn[0] = None

        # Show community cards if any
        community = data.get("community", [])
        if community:
            print(format_community(community, data.get("stage", "")))

        action = get_action(
            chips=data.get("chips", my_chips[0]),
            pot=data.get("pot", 0),
            stage=data.get("stage", "")
        )

        # Update local chip tracker
        if action["action"] == "BET":
            my_chips[0] -= action["amount"]

        # Graceful exit
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
            print("  [!] Failed to send action. Disconnected?")
            break

    print("\n  Game ended. Closing connection.")
    try:
        sock.close()
    except Exception:
        pass


if __name__ == "__main__":
    main()
