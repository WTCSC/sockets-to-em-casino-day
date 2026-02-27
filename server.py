"""
Virtual Poker Game - Server
Run this FIRST: python server.py
"""

import socket
import threading
import random
import json
import time
import sys
from itertools import combinations

# ─────────────────────────────────────────────
#  ASCII ART
# ─────────────────────────────────────────────

BANNER = """
  ██████╗  ██████╗ ██╗  ██╗███████╗██████╗     ██████╗  ██████╗ ██╗  ██╗███████╗██████╗
  ██╔══██╗██╔═══██╗██║ ██╔╝██╔════╝██╔══██╗    ██╔══██╗██╔═══██╗██║ ██╔╝██╔════╝██╔══██╗
  ██████╔╝██║   ██║█████╔╝ █████╗  ██████╔╝    ██████╔╝██║   ██║█████╔╝ █████╗  ██████╔╝
  ██╔═══╝ ██║   ██║██╔═██╗ ██╔══╝  ██╔══██╗    ██╔═══╝ ██║   ██║██╔═██╗ ██╔══╝  ██╔══██╗
  ██║     ╚██████╔╝██║  ██╗███████╗██║  ██║    ██║     ╚██████╔╝██║  ██╗███████╗██║  ██║
  ╚═╝      ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝    ╚═╝      ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
"""

ANNIE_MSG = """
  ♠ ♥ ♦ ♣  Welcome, Annie! You're gonna clean house tonight.  ♣ ♦ ♥ ♠
"""

SUITS  = {"s": "Spades \u2660", "h": "Hearts \u2665", "d": "Diamonds \u2666", "c": "Clubs \u2663"}
RANKS  = {"2":"2","3":"3","4":"4","5":"5","6":"6","7":"7","8":"8","9":"9",
          "10":"10","J":"Jack","Q":"Queen","K":"King","A":"Ace"}

def card_name(code):
    suit = SUITS.get(code[-1], code[-1])
    rank = RANKS.get(code[:-1], code[:-1])
    return f"{rank} of {suit}"

# ─────────────────────────────────────────────
#  DECK
# ─────────────────────────────────────────────

def create_deck():
    deck = [r+s for r in ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
            for s in ["s","h","d","c"]]
    random.shuffle(deck)
    return deck

# ─────────────────────────────────────────────
#  HAND EVALUATOR (best 5 from up to 7 cards)
# ─────────────────────────────────────────────

RANK_ORDER = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]

def rv(card):
    return RANK_ORDER.index(card[:-1])

def score_five(hand):
    ranks = sorted([rv(c) for c in hand], reverse=True)
    suits = [c[-1] for c in hand]
    cnt = sorted({r: ranks.count(r) for r in ranks}.values(), reverse=True)
    flush    = len(set(suits)) == 1
    straight = len(set(ranks)) == 5 and (max(ranks) - min(ranks) == 4)
    if flush and straight and max(ranks)==12: return (9,"Royal Flush")
    if flush and straight:                   return (8,"Straight Flush")
    if cnt[0]==4:                            return (7,"Four of a Kind")
    if cnt[0]==3 and cnt[1]==2:             return (6,"Full House")
    if flush:                                return (5,"Flush")
    if straight:                             return (4,"Straight")
    if cnt[0]==3:                            return (3,"Three of a Kind")
    if cnt[0]==2 and cnt[1]==2:             return (2,"Two Pair")
    if cnt[0]==2:                            return (1,"One Pair")
    return (0,"High Card")

def evaluate_hand(hand):
    cards = list(hand)
    if len(cards) <= 5:
        return score_five(cards)
    return max((score_five(list(c)) for c in combinations(cards, 5)), key=lambda x: x[0])

# ─────────────────────────────────────────────
#  GAME STATE
# ─────────────────────────────────────────────

class PokerGame:
    def __init__(self):
        self.players       = {}
        self.player_order  = []
        self.deck          = []
        self.pot           = 0
        self.current_bet   = 0
        self.community     = []
        self.lock          = threading.Lock()

        # Lobby settings
        self.max_players     = 6
        self.starting_chips  = 1000
        self.num_rounds      = 3
        self.lobby_open      = True

        # Sync
        self.lobby_ready      = threading.Event()
        self.play_again_event = threading.Event()
        self.play_again_vote  = None

    # ── player management ──────────────────────
    def add_player(self, conn, addr, name):
        pid = len(self.player_order)
        self.players[pid] = {
            "conn": conn, "addr": addr, "name": name,
            "chips": self.starting_chips, "hand": [],
            "folded": False, "bet": 0, "active": True,
            "is_host": pid == 0
        }
        self.player_order.append(pid)
        return pid

    def reset_for_new_game(self):
        for p in self.players.values():
            p["chips"]  = self.starting_chips
            p["hand"]   = []
            p["folded"] = False
            p["bet"]    = 0

    # ── messaging ──────────────────────────────
    def broadcast(self, message, exclude=None):
        data = json.dumps(message) + "\n"
        for pid in self.player_order:
            if pid == exclude:
                continue
            if self.players[pid]["active"]:
                try:
                    self.players[pid]["conn"].sendall(data.encode())
                except Exception:
                    pass

    def send_to(self, pid, message):
        try:
            self.players[pid]["conn"].sendall((json.dumps(message) + "\n").encode())
        except Exception:
            pass

    # ── helpers ────────────────────────────────
    def active_in_hand(self):
        return [pid for pid in self.player_order
                if not self.players[pid]["folded"] and self.players[pid]["active"]]

    def lobby_status(self):
        return {
            "type"   : "LOBBY_STATUS",
            "players": [self.players[p]["name"] for p in self.player_order],
            "max"    : self.max_players,
            "chips"  : self.starting_chips,
            "rounds" : self.num_rounds,
            "host"   : self.players[0]["name"] if self.player_order else "?"
        }


game = PokerGame()

# ─────────────────────────────────────────────
#  NETWORK: read one JSON line (no timeout)
# ─────────────────────────────────────────────

def receive_line(pid):
    conn = game.players[pid]["conn"]
    buf  = ""
    while True:
        try:
            chunk = conn.recv(1024).decode()
            if not chunk:
                return None
            buf += chunk
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if line:
                    try:
                        return json.loads(line)
                    except json.JSONDecodeError:
                        pass
        except (ConnectionResetError, OSError):
            return None

# ─────────────────────────────────────────────
#  CHEAT DETECTION
# ─────────────────────────────────────────────

def cheat_check(pid, claimed_score):
    hand = game.players[pid]["hand"] + game.community
    real_score, real_desc = evaluate_hand(hand)
    if claimed_score > real_score:
        game.send_to(pid, {"type": "KICKED",
                           "msg": (f"CHEAT DETECTED: claimed score {claimed_score} "
                                   f"but real hand is {real_desc} (score={real_score}). Kicked.")})
        game.broadcast({"type": "INFO",
                        "msg": f"\n  [!] {game.players[pid]['name']} was KICKED for cheating!"},
                       exclude=pid)
        game.players[pid]["active"] = False
        game.players[pid]["folded"] = True
        return True
    return False

# ─────────────────────────────────────────────
#  BETTING
# ─────────────────────────────────────────────

def send_your_turn(pid, stage):
    p = game.players[pid]
    call_amount = max(0, game.current_bet - p["bet"])
    game.send_to(pid, {
        "type"        : "YOUR_TURN",
        "stage"       : stage,
        "pot"         : game.pot,
        "chips"       : p["chips"],
        "hand"        : [card_name(c) for c in p["hand"]],
        "community"   : [card_name(c) for c in game.community],
        "current_bet" : game.current_bet,
        "your_bet"    : p["bet"],
        "call_amount" : call_amount
    })

def handle_action(pid, data):
    """Process one action. Returns False (no raise), True (raise), or 're-prompt'."""
    p      = game.players[pid]
    action = data.get("action", "").upper().strip()

    if action == "FOLD":
        p["folded"] = True
        game.broadcast({"type": "INFO", "msg": f"  {p['name']} folds."})
        return False

    if action == "CHECK":
        call_amount = max(0, game.current_bet - p["bet"])
        if call_amount > 0:
            game.send_to(pid, {"type": "ERROR",
                               "msg": f"Can't check — current bet is {game.current_bet}. "
                                      f"You need to CALL {call_amount}, RAISE, or FOLD."})
            return "re-prompt"
        game.broadcast({"type": "INFO", "msg": f"  {p['name']} checks."})
        return False

    if action == "CALL":
        call_amount = max(0, game.current_bet - p["bet"])
        if call_amount == 0:
            game.broadcast({"type": "INFO", "msg": f"  {p['name']} checks."})
            return False
        call_amount = min(call_amount, p["chips"])  # all-in cap
        p["chips"] -= call_amount
        p["bet"]   += call_amount
        game.pot   += call_amount
        game.broadcast({"type": "INFO", "msg": f"  {p['name']} calls {call_amount}. Pot: {game.pot}"})
        return False

    if action in ("BET", "RAISE"):
        amount = data.get("amount", 0)
        if not isinstance(amount, int) or amount <= 0:
            game.send_to(pid, {"type": "ERROR", "msg": "Amount must be a positive whole number."})
            return "re-prompt"
        call_amount   = max(0, game.current_bet - p["bet"])
        total_needed  = call_amount + amount
        if total_needed > p["chips"]:
            game.send_to(pid, {"type": "ERROR",
                               "msg": f"Need {total_needed} chips but you only have {p['chips']}."})
            return "re-prompt"
        claimed = data.get("claimed_score", -1)
        if claimed >= 0 and cheat_check(pid, claimed):
            return False
        p["chips"]       -= total_needed
        p["bet"]         += total_needed
        game.pot         += total_needed
        game.current_bet  = p["bet"]
        verb = "bets" if action == "BET" else "raises to"
        game.broadcast({"type": "INFO", "msg": f"  {p['name']} {verb} {p['bet']}. Pot: {game.pot}"})
        return True   # raise → others act again

    if action == "QUIT":
        p["active"] = False
        p["folded"] = True
        game.broadcast({"type": "INFO", "msg": f"  {p['name']} left the game."})
        return False

    game.send_to(pid, {"type": "ERROR",
                       "msg": f"Unknown action '{action}'. Options: BET, CALL, RAISE, CHECK, FOLD, QUIT"})
    return "re-prompt"


def betting_round(stage):
    game.current_bet = 0
    for p in game.players.values():
        p["bet"] = 0

    game.broadcast({"type": "INFO",
                    "msg": f"\n{'─'*50}\n  {stage}  —  Pot: {game.pot}\n{'─'*50}"})

    active = game.active_in_hand()
    if len(active) <= 1:
        return

    needs_to_act = list(active)

    while needs_to_act:
        pid = needs_to_act.pop(0)
        p   = game.players[pid]

        if p["folded"] or not p["active"]:
            continue

        game.broadcast({"type": "TURN", "player": p["name"],
                        "pot": game.pot, "current_bet": game.current_bet})

        while True:
            send_your_turn(pid, stage)
            data = receive_line(pid)

            if data is None:
                game.broadcast({"type": "INFO", "msg": f"  {p['name']} disconnected — folded."})
                p["active"] = False
                p["folded"] = True
                break

            # Intercept host game-decision messages that arrive mid-game
            if data.get("type") == "HOST_DECISION_RESPONSE":
                _handle_host_decision(data)
                continue

            result = handle_action(pid, data)

            if result == "re-prompt":
                continue
            if result is True:
                # Raise — re-add everyone else who is still in
                for other in game.player_order:
                    op = game.players[other]
                    if other != pid and not op["folded"] and op["active"]:
                        if other not in needs_to_act:
                            needs_to_act.append(other)
            break

        if len(game.active_in_hand()) <= 1:
            break

# ─────────────────────────────────────────────
#  WINNER
# ─────────────────────────────────────────────

def resolve_winner():
    active = [(pid, game.players[pid]) for pid in game.player_order
              if not game.players[pid]["folded"] and game.players[pid]["active"]]

    if not active:
        game.broadcast({"type": "INFO", "msg": "  Everyone folded. No winner."})
        game.pot = 0
        return

    if len(active) == 1:
        winner_id, winner = active[0]
        winner["chips"] += game.pot
        game.broadcast({
            "type": "WINNER", "player": winner["name"],
            "reason": "Last player standing", "pot": game.pot,
            "chips": {game.players[pid]["name"]: game.players[pid]["chips"]
                      for pid in game.player_order if game.players[pid]["active"]}
        })
        game.pot = 0
        return

    results = []
    for pid, p in active:
        score, desc = evaluate_hand(p["hand"] + game.community)
        results.append((score, pid, desc))
        game.send_to(pid, {
            "type"     : "SHOWDOWN",
            "hand"     : [card_name(c) for c in p["hand"]],
            "community": [card_name(c) for c in game.community],
            "best"     : desc
        })

    # Reveal all hands to everyone
    game.broadcast({
        "type": "REVEAL",
        "hands": [{"name": game.players[pid]["name"],
                   "hand": [card_name(c) for c in game.players[pid]["hand"]],
                   "best": desc}
                  for _, pid, desc in results]
    })

    results.sort(key=lambda x: x[0], reverse=True)
    top     = results[0][0]
    winners = [r for r in results if r[0] == top]
    split   = game.pot // len(winners)
    for _, pid, _ in winners:
        game.players[pid]["chips"] += split

    game.broadcast({
        "type"  : "WINNER",
        "player": ", ".join(game.players[pid]["name"] for _, pid, _ in winners),
        "hand"  : winners[0][2],
        "pot"   : game.pot,
        "split" : len(winners) > 1,
        "chips" : {game.players[pid]["name"]: game.players[pid]["chips"]
                   for pid in game.player_order if game.players[pid]["active"]}
    })
    game.pot = 0

# ─────────────────────────────────────────────
#  ROUND
# ─────────────────────────────────────────────

def start_round(round_num, total):
    with game.lock:
        game.deck      = create_deck()
        game.pot       = 0
        game.current_bet = 0
        game.community = []
        for pid in game.player_order:
            p = game.players[pid]
            if p["active"]:
                p["hand"]   = [game.deck.pop(), game.deck.pop()]
                p["folded"] = False
                p["bet"]    = 0

    game.broadcast({"type": "INFO",
                    "msg": f"\n{'='*50}\n  ROUND {round_num} / {total}\n{'='*50}"})

    # Deal hands privately
    for pid in game.player_order:
        p = game.players[pid]
        if p["active"]:
            game.send_to(pid, {
                "type"   : "HAND",
                "cards"  : p["hand"],
                "display": [card_name(c) for c in p["hand"]],
                "chips"  : p["chips"]
            })

    # Blind ante
    blind = 10
    for pid in game.player_order:
        p = game.players[pid]
        if p["active"] and p["chips"] >= blind:
            p["chips"]     -= blind
            p["bet"]       += blind
            game.pot       += blind
    game.current_bet = blind
    game.broadcast({"type": "INFO",
                    "msg": f"  All players post {blind}-chip blind. Pot: {game.pot}"})

    betting_round("Pre-Flop")
    if len(game.active_in_hand()) < 2:
        resolve_winner(); return

    for _ in range(3):
        game.community.append(game.deck.pop())
    game.broadcast({"type": "COMMUNITY", "stage": "Flop",
                    "display": [card_name(c) for c in game.community]})
    betting_round("Flop")
    if len(game.active_in_hand()) < 2:
        resolve_winner(); return

    game.community.append(game.deck.pop())
    game.broadcast({"type": "COMMUNITY", "stage": "Turn",
                    "display": [card_name(c) for c in game.community]})
    betting_round("Turn")
    if len(game.active_in_hand()) < 2:
        resolve_winner(); return

    game.community.append(game.deck.pop())
    game.broadcast({"type": "COMMUNITY", "stage": "River",
                    "display": [card_name(c) for c in game.community]})
    betting_round("River")
    resolve_winner()

# ─────────────────────────────────────────────
#  GAME SESSION (multiple games)
# ─────────────────────────────────────────────

def run_game_session():
    while True:
        game.reset_for_new_game()
        total = game.num_rounds
        game.broadcast({"type": "INFO",
                        "msg": (f"\n  Game starting: {len(game.player_order)} players | "
                                f"{total} rounds | {game.starting_chips} chips each")})

        for rnd in range(1, total + 1):
            if len(game.active_in_hand()) < 2 and \
               sum(1 for p in game.players.values() if p["active"]) < 2:
                game.broadcast({"type": "INFO", "msg": "  Not enough players. Ending early."})
                break
            # Reset folded status for new round
            for p in game.players.values():
                if p["active"]:
                    p["folded"] = False
            start_round(rnd, total)
            time.sleep(1)

        final = sorted(
            [(game.players[pid]["name"], game.players[pid]["chips"])
             for pid in game.player_order if game.players[pid]["active"]],
            key=lambda x: x[1], reverse=True
        )
        game.broadcast({"type": "GAME_OVER", "standings": final})

        # Ask host to play again
        time.sleep(1)
        game.send_to(0, {
            "type": "HOST_DECISION",
            "msg": "Type PLAY AGAIN to start a new game, or END to close the server."
        })
        game.broadcast({"type": "INFO",
                        "msg": "\n  Waiting for host to decide..."}, exclude=0)

        game.play_again_event.clear()
        game.play_again_vote = None
        game.play_again_event.wait()

        if not game.play_again_vote:
            game.broadcast({"type": "SERVER_CLOSING",
                            "msg": "Host ended the session. Thanks for playing!"})
            time.sleep(1)
            break

        game.broadcast({"type": "INFO", "msg": "\n  ♻  New game incoming!\n"})

# ─────────────────────────────────────────────
#  HOST DECISION HANDLER
# ─────────────────────────────────────────────

def _handle_host_decision(data):
    cmd = data.get("cmd", "").upper().strip()
    if cmd == "PLAY AGAIN":
        game.play_again_vote = True
        game.play_again_event.set()
    elif cmd == "END":
        game.play_again_vote = False
        game.play_again_event.set()

# ─────────────────────────────────────────────
#  LOBBY
# ─────────────────────────────────────────────

def run_lobby():
    host_pid = 0
    game.broadcast({"type": "INFO", "msg": BANNER})
    game.broadcast(game.lobby_status())
    game.send_to(host_pid, {
        "type": "HOST_LOBBY",
        "msg": (
            "\n  You are the HOST. Commands available while lobby is open:\n"
            "    PLAYERS <n>   — max players (2–6)\n"
            "    CHIPS <n>     — starting chips per player\n"
            "    ROUNDS <n>    — number of rounds (1–20)\n"
            "    START         — start the game (need 2+ players)\n"
        )
    })
    game.broadcast({"type": "INFO",
                    "msg": "  Waiting for host to configure and start the game...\n"},
                   exclude=host_pid)

    while True:
        data = receive_line(host_pid)
        if data is None:
            game.broadcast({"type": "ERROR", "msg": "Host disconnected. Game cancelled."})
            sys.exit(0)

        cmd = data.get("cmd", "").upper().strip()
        val = data.get("value", "")

        if cmd == "PLAYERS":
            try:
                n = int(val)
                if 2 <= n <= 6:
                    game.max_players = n
                    game.send_to(host_pid, {"type": "INFO", "msg": f"  Max players → {n}"})
                    game.broadcast(game.lobby_status())
                else:
                    game.send_to(host_pid, {"type": "ERROR", "msg": "Must be 2–6."})
            except (ValueError, TypeError):
                game.send_to(host_pid, {"type": "ERROR", "msg": "Usage: PLAYERS <number>"})

        elif cmd == "CHIPS":
            try:
                n = int(val)
                if n >= 50:
                    game.starting_chips = n
                    game.send_to(host_pid, {"type": "INFO", "msg": f"  Starting chips → {n}"})
                    game.broadcast(game.lobby_status())
                else:
                    game.send_to(host_pid, {"type": "ERROR", "msg": "Minimum is 50."})
            except (ValueError, TypeError):
                game.send_to(host_pid, {"type": "ERROR", "msg": "Usage: CHIPS <number>"})

        elif cmd == "ROUNDS":
            try:
                n = int(val)
                if 1 <= n <= 20:
                    game.num_rounds = n
                    game.send_to(host_pid, {"type": "INFO", "msg": f"  Rounds → {n}"})
                    game.broadcast(game.lobby_status())
                else:
                    game.send_to(host_pid, {"type": "ERROR", "msg": "Must be 1–20."})
            except (ValueError, TypeError):
                game.send_to(host_pid, {"type": "ERROR", "msg": "Usage: ROUNDS <number>"})

        elif cmd == "START":
            if len(game.player_order) < 2:
                game.send_to(host_pid, {"type": "ERROR",
                                        "msg": "Need at least 2 players connected first."})
                continue
            game.lobby_open = False
            game.broadcast({"type": "INFO",
                            "msg": f"\n  HOST started the game with "
                                   f"{len(game.player_order)} players!"})
            game.lobby_ready.set()
            break

        else:
            game.send_to(host_pid, {"type": "ERROR",
                                    "msg": f"Unknown: '{cmd}'. Try PLAYERS / CHIPS / ROUNDS / START"})

# ─────────────────────────────────────────────
#  NAME HANDSHAKE
# ─────────────────────────────────────────────

def get_player_name(conn):
    try:
        conn.settimeout(30)
        conn.sendall((json.dumps({"type": "NAME_REQUEST",
                                  "msg": "Enter your name: "}) + "\n").encode())
        buf = ""
        while True:
            chunk = conn.recv(256).decode()
            if not chunk:
                return "Player"
            buf += chunk
            if "\n" in buf:
                line, _ = buf.split("\n", 1)
                data = json.loads(line.strip())
                name = str(data.get("name", "Player")).strip()[:20] or "Player"
                conn.settimeout(None)
                return name
    except Exception:
        return "Player"

# ─────────────────────────────────────────────
#  CLIENT THREAD
# ─────────────────────────────────────────────

def handle_client(conn, addr, pid):
    p = game.players[pid]
    print(f"  [SERVER] {p['name']} joined from {addr}")

    if p["name"].strip().lower() == "annie":
        game.send_to(pid, {"type": "INFO", "msg": ANNIE_MSG})

    if pid == 0:
        # Host drives lobby + game session
        game.send_to(pid, {"type": "INFO",
                           "msg": "  More players can join while you configure. "
                                  "Type START when ready."})
        run_lobby()
        run_game_session()
    else:
        # Non-host: idle until lobby opens, then kept alive by game loop
        game.send_to(pid, {"type": "INFO",
                           "msg": "  Waiting for host to start the game..."})
        game.lobby_ready.wait()
        while p["active"]:
            time.sleep(1)

    try:
        conn.close()
    except Exception:
        pass

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    HOST = "100.68.88.62"
    PORT = 5555

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(10)

    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "run `ipconfig` or `hostname -I`"

    print(BANNER)
    print(f"  [SERVER] Port         : {PORT}")
    print(f"  [SERVER] Your LAN IP  : {local_ip}")
    print(f"  [SERVER] Tell players : HOST = \"{local_ip}\" in client.py")
    print(f"  [SERVER] First player to connect becomes the host.\n")

    threads = []

    def accept_loop():
        while True:
            try:
                srv.settimeout(1.0)
                try:
                    conn, addr = srv.accept()
                except socket.timeout:
                    if not game.lobby_open and game.lobby_ready.is_set():
                        break   # lobby closed, stop accepting
                    continue

                if not game.lobby_open:
                    conn.sendall((json.dumps({"type": "ERROR",
                                              "msg": "Game already in progress."}) + "\n").encode())
                    conn.close()
                    continue

                name = get_player_name(conn)
                with game.lock:
                    if len(game.player_order) >= game.max_players:
                        conn.sendall((json.dumps({"type": "ERROR",
                                                  "msg": "Lobby is full."}) + "\n").encode())
                        conn.close()
                        continue
                    pid = game.add_player(conn, addr, name)

                print(f"  [SERVER] {name} joined  ({len(game.player_order)}/{game.max_players})")
                game.broadcast({"type": "INFO",
                                "msg": f"  ++ {name} joined! "
                                       f"({len(game.player_order)}/{game.max_players})"})
                game.broadcast(game.lobby_status())

                t = threading.Thread(target=handle_client, args=(conn, addr, pid), daemon=True)
                threads.append(t)
                t.start()

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"  [SERVER] Accept error: {e}")

    acc = threading.Thread(target=accept_loop, daemon=True)
    acc.start()

    try:
        game.lobby_ready.wait()   # block until host types START
        for t in threads:
            t.join(timeout=7200)
    except KeyboardInterrupt:
        print("\n  [SERVER] Interrupted.")

    srv.close()
    print("  [SERVER] Closed. Goodbye!")


if __name__ == "__main__":
    main()