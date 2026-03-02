"""
Virtual Poker Game — Server
Run first: python3 server.py
"""

import socket
import threading
import random
import json
import time
import sys
from itertools import combinations

# ─────────────────────────────────────────────
#  CONSTANTS / ASCII
# ─────────────────────────────────────────────

HOST = "100.68.88.62" 
PORT = 5555
BLIND = 10

BANNER = """
  ██████╗  ██████╗ ██╗  ██╗███████╗██████╗     ██████╗  ██████╗ ██╗  ██╗███████╗██████╗
  ██╔══██╗██╔═══██╗██║ ██╔╝██╔════╝██╔══██╗    ██╔══██╗██╔═══██╗██║ ██╔╝██╔════╝██╔══██╗
  ██████╔╝██║   ██║█████╔╝ █████╗  ██████╔╝    ██████╔╝██║   ██║█████╔╝ █████╗  ██████╔╝
  ██╔═══╝ ██║   ██║██╔═██╗ ██╔══╝  ██╔══██╗    ██╔═══╝ ██║   ██║██╔═██╗ ██╔══╝  ██╔══██╗
  ██║     ╚██████╔╝██║  ██╗███████╗██║  ██║    ██║     ╚██████╔╝██║  ██╗███████╗██║  ██║
  ╚═╝      ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝    ╚═╝      ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
"""

SUITS = {"s": "Spades ♠", "h": "Hearts ♥", "d": "Diamonds ♦", "c": "Clubs ♣"}
RANKS = {"2":"2","3":"3","4":"4","5":"5","6":"6","7":"7","8":"8","9":"9",
         "10":"10","J":"Jack","Q":"Queen","K":"King","A":"Ace"}
RANK_ORDER = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]

# ─────────────────────────────────────────────
#  CARD HELPERS
# ─────────────────────────────────────────────

def card_name(code):
    return f"{RANKS.get(code[:-1], code[:-1])} of {SUITS.get(code[-1], code[-1])}"

def card_names(codes):
    return [card_name(c) for c in codes]

def create_deck():
    deck = [r + s for r in RANK_ORDER for s in SUITS]
    random.shuffle(deck)
    return deck

# ─────────────────────────────────────────────
#  HAND EVALUATOR  (best 5 from up to 7 cards)
# ─────────────────────────────────────────────

def _rank_val(card):
    return RANK_ORDER.index(card[:-1])

def _score_five(hand):
    ranks = sorted([_rank_val(c) for c in hand], reverse=True)
    suits = [c[-1] for c in hand]
    counts = sorted({r: ranks.count(r) for r in ranks}.values(), reverse=True)
    flush    = len(set(suits)) == 1
    straight = len(set(ranks)) == 5 and max(ranks) - min(ranks) == 4
    if flush and straight and max(ranks) == 12: return (9, "Royal Flush")
    if flush and straight:                      return (8, "Straight Flush")
    if counts[0] == 4:                          return (7, "Four of a Kind")
    if counts[0] == 3 and counts[1] == 2:      return (6, "Full House")
    if flush:                                   return (5, "Flush")
    if straight:                                return (4, "Straight")
    if counts[0] == 3:                          return (3, "Three of a Kind")
    if counts[0] == 2 and counts[1] == 2:      return (2, "Two Pair")
    if counts[0] == 2:                          return (1, "One Pair")
    return (0, "High Card")

def evaluate_hand(cards):
    cards = list(cards)
    if len(cards) <= 5:
        return _score_five(cards)
    return max((_score_five(list(c)) for c in combinations(cards, 5)), key=lambda x: x[0])

# ─────────────────────────────────────────────
#  GAME STATE
# ─────────────────────────────────────────────

class PokerGame:
    def __init__(self):
        self.players      = {}          # pid → player dict
        self.player_order = []          # insertion-ordered pids
        self.deck         = []
        self.pot          = 0
        self.current_bet  = 0
        self.community    = []
        self.lock         = threading.Lock()

        # Lobby config
        self.max_players    = 6
        self.starting_chips = 1000
        self.num_rounds     = 3
        self.lobby_open     = True

        # Sync primitives
        self.lobby_ready      = threading.Event()
        self.play_again_event = threading.Event()
        self.play_again_vote  = None

    # ── Player management ──────────────────────────────────────────────────

    def add_player(self, conn, addr, name):
        pid = len(self.player_order)
        self.players[pid] = {
            "conn": conn, "addr": addr, "name": name,
            "chips": self.starting_chips,
            "hand": [], "folded": False, "bet": 0,
            "active": True, "busted": False,
        }
        self.player_order.append(pid)
        return pid

    def reset_for_new_game(self):
        """Restore chips and clear hand state; un-bust all players for a fresh game."""
        for p in self.players.values():
            p.update(chips=self.starting_chips, hand=[], folded=False, bet=0,
                     active=True, busted=False)

    def reset_for_new_round(self):
        """Clear per-round state without touching chips."""
        for p in self.players.values():
            if not p["busted"]:
                p.update(hand=[], folded=False, bet=0)

    # ── Messaging ──────────────────────────────────────────────────────────

    @staticmethod
    def _encode(msg):
        return (json.dumps(msg) + "\n").encode()

    def send_to(self, pid, msg):
        try:
            self.players[pid]["conn"].sendall(self._encode(msg))
        except OSError:
            pass

    def broadcast(self, msg, exclude=None):
        data = self._encode(msg)
        for pid, p in self.players.items():
            if pid != exclude and p["active"]:
                try:
                    p["conn"].sendall(data)
                except OSError:
                    pass

    # ── Queries ────────────────────────────────────────────────────────────

    def active_in_hand(self):
        return [pid for pid in self.player_order
                if not self.players[pid]["folded"] and self.players[pid]["active"]]

    def solvent_players(self):
        """Players still alive (not busted) for the next round."""
        return [pid for pid in self.player_order
                if self.players[pid]["active"] and not self.players[pid]["busted"]]

    def lobby_status(self):
        return {
            "type": "LOBBY_STATUS",
            "players": [self.players[p]["name"] for p in self.player_order],
            "max": self.max_players,
            "chips": self.starting_chips,
            "rounds": self.num_rounds,
            "host": self.players[0]["name"] if self.player_order else "?",
        }

    def chip_snapshot(self):
        return {self.players[pid]["name"]: self.players[pid]["chips"]
                for pid in self.player_order if self.players[pid]["active"]}


game = PokerGame()

# ─────────────────────────────────────────────
#  NETWORK
# ─────────────────────────────────────────────

def receive_line(pid):
    """Block until one complete JSON line arrives; return parsed dict or None on disconnect."""
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
                        return line   # skip malformed line, keep reading
        except (ConnectionResetError, OSError):
            return None

def send_reject(conn, msg):
    """Send an error to a not-yet-registered connection and close it."""
    try:
        conn.sendall(PokerGame._encode({"type": "ERROR", "msg": msg}))
    except OSError:
        pass
    conn.close()

# ─────────────────────────────────────────────
#  CHEAT DETECTION
# ─────────────────────────────────────────────

def cheat_check(pid, claimed_score):
    real_score, real_desc = evaluate_hand(game.players[pid]["hand"] + game.community)
    if claimed_score <= real_score:
        return False
    game.send_to(pid, {"type": "KICKED",
                       "msg": (f"CHEAT DETECTED: claimed score {claimed_score} but "
                               f"real hand is {real_desc} (score={real_score}). Kicked.")})
    game.broadcast({"type": "INFO",
                    "msg": f"\n  [!] {game.players[pid]['name']} was KICKED for cheating!"},
                   exclude=pid)
    game.players[pid].update(active=False, folded=True)
    return True

# ─────────────────────────────────────────────
#  BETTING
# ─────────────────────────────────────────────

def _your_turn_payload(pid, stage):
    p = game.players[pid]
    return {
        "type": "YOUR_TURN", "stage": stage,
        "pot": game.pot, "chips": p["chips"],
        "hand": card_names(p["hand"]),
        "community": card_names(game.community),
        "current_bet": game.current_bet,
        "your_bet": p["bet"],
        "call_amount": max(0, game.current_bet - p["bet"]),
    }

def handle_action(pid, data):
    """
    Mutate game state for one player action.
    Returns: True (raise occurred), False (no raise), or 're-prompt' (invalid input).
    """
    p      = game.players[pid]
    action = data.get("action", "").upper().strip()
    call_amount = max(0, game.current_bet - p["bet"])

    if action == "FOLD":
        p["folded"] = True
        game.broadcast({"type": "INFO", "msg": f"  {p['name']} folds."})
        return False

    if action == "CHECK":
        if call_amount > 0:
            game.send_to(pid, {"type": "ERROR",
                               "msg": (f"Can't check — bet is {game.current_bet}. "
                                       f"CALL {call_amount}, RAISE, or FOLD.")})
            return "re-prompt"
        game.broadcast({"type": "INFO", "msg": f"  {p['name']} checks."})
        return False

    if action == "CALL":
        amount = min(call_amount, p["chips"])   # all-in cap
        if amount == 0:
            game.broadcast({"type": "INFO", "msg": f"  {p['name']} checks."})
            return False
        p["chips"] -= amount
        p["bet"]   += amount
        game.pot   += amount
        game.broadcast({"type": "INFO", "msg": f"  {p['name']} calls {amount}. Pot: {game.pot}"})
        return False

    if action in ("BET", "RAISE"):
        raise_by = data.get("amount", 0)
        if not isinstance(raise_by, int) or raise_by <= 0:
            game.send_to(pid, {"type": "ERROR", "msg": "Amount must be a positive whole number."})
            return "re-prompt"
        total_needed = call_amount + raise_by
        if total_needed > p["chips"]:
            game.send_to(pid, {"type": "ERROR",
                               "msg": f"Need {total_needed} chips; you have {p['chips']}."})
            return "re-prompt"
        if data.get("claimed_score", -1) >= 0 and cheat_check(pid, data["claimed_score"]):
            return False
        p["chips"]      -= total_needed
        p["bet"]        += total_needed
        game.pot        += total_needed
        game.current_bet = p["bet"]
        verb = "bets" if action == "BET" else "raises to"
        game.broadcast({"type": "INFO", "msg": f"  {p['name']} {verb} {p['bet']}. Pot: {game.pot}"})
        return True    # raise → all others must re-act

    if action == "QUIT":
        p.update(active=False, folded=True)
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

    if len(game.active_in_hand()) <= 1:
        return

    needs_to_act = list(game.active_in_hand())

    while needs_to_act:
        pid = needs_to_act.pop(0)
        p   = game.players[pid]
        if p["folded"] or not p["active"]:
            continue

        game.broadcast({"type": "TURN", "player": p["name"],
                        "pot": game.pot, "current_bet": game.current_bet})

        while True:
            game.send_to(pid, _your_turn_payload(pid, stage))
            data = receive_line(pid)

            if data is None:
                game.broadcast({"type": "INFO", "msg": f"  {p['name']} disconnected — folded."})
                p.update(active=False, folded=True)
                break

            if data.get("type") == "HOST_DECISION_RESPONSE":
                _handle_host_decision(data)
                continue

            result = handle_action(pid, data)
            if result == "re-prompt":
                continue
            if result is True:
                for other_pid in game.player_order:
                    op = game.players[other_pid]
                    if other_pid != pid and not op["folded"] and op["active"]:
                        if other_pid not in needs_to_act:
                            needs_to_act.append(other_pid)
            break

        if len(game.active_in_hand()) <= 1:
            break

# ─────────────────────────────────────────────
#  WINNER RESOLUTION
# ─────────────────────────────────────────────

def resolve_winner():
    active = [(pid, game.players[pid]) for pid in game.player_order
              if not game.players[pid]["folded"] and game.players[pid]["active"]]

    if not active:
        game.broadcast({"type": "INFO", "msg": "  Everyone folded. No winner this round."})
        game.pot = 0
        return

    if len(active) == 1:
        winner_id, winner = active[0]
        winner["chips"] += game.pot
        game.broadcast({"type": "WINNER", "player": winner["name"],
                        "reason": "Last player standing", "pot": game.pot,
                        "chips": game.chip_snapshot()})
        game.pot = 0
        return

    # Evaluate all hands
    results = [(evaluate_hand(p["hand"] + game.community), pid, p)
               for pid, p in active]

    for (score, desc), pid, p in results:
        game.send_to(pid, {"type": "SHOWDOWN",
                           "hand": card_names(p["hand"]),
                           "community": card_names(game.community),
                           "best": desc})

    game.broadcast({"type": "REVEAL",
                    "hands": [{"name": game.players[pid]["name"],
                               "hand": card_names(game.players[pid]["hand"]),
                               "best": desc}
                              for (_, desc), pid, _ in results]})

    results.sort(key=lambda x: x[0][0], reverse=True)
    top_score = results[0][0][0]
    winners   = [r for r in results if r[0][0] == top_score]
    split     = game.pot // len(winners)
    for _, pid, _ in winners:
        game.players[pid]["chips"] += split

    game.broadcast({"type": "WINNER",
                    "player": ", ".join(game.players[pid]["name"] for _, pid, _ in winners),
                    "hand": winners[0][0][1],
                    "pot": game.pot,
                    "split": len(winners) > 1,
                    "chips": game.chip_snapshot()})
    game.pot = 0

# ─────────────────────────────────────────────
#  BUST CHECK  (called after each round resolves)
# ─────────────────────────────────────────────

def check_for_busted_players():
    """
    Mark any active player with 0 chips as busted.
    Send them a BUSTED message so the client returns to lobby-wait mode.
    """
    for pid in game.player_order:
        p = game.players[pid]
        if p["active"] and not p["busted"] and p["chips"] <= 0:
            p["busted"] = True
            p["folded"] = True
            game.send_to(pid, {
                "type": "BUSTED",
                "msg": (f"  You're out of chips! You've been eliminated from this game.\n"
                        f"  Sit tight — you'll re-join with fresh chips next game.")
            })
            game.broadcast({"type": "INFO",
                            "msg": f"  💀  {p['name']} busted out!"},
                           exclude=pid)

# ─────────────────────────────────────────────
#  ROUND
# ─────────────────────────────────────────────

def _deal_community(n, stage):
    for _ in range(n):
        game.community.append(game.deck.pop())
    game.broadcast({"type": "COMMUNITY", "stage": stage,
                    "display": card_names(game.community)})

def start_round(round_num, total):
    game.reset_for_new_round()
    with game.lock:
        game.deck      = create_deck()
        game.pot       = 0
        game.current_bet = 0
        game.community = []
        for pid in game.solvent_players():
            p = game.players[pid]
            p["hand"] = [game.deck.pop(), game.deck.pop()]

    game.broadcast({"type": "INFO",
                    "msg": f"\n{'='*50}\n  ROUND {round_num} / {total}\n{'='*50}"})

    for pid in game.solvent_players():
        p = game.players[pid]
        game.send_to(pid, {"type": "HAND", "cards": p["hand"],
                           "display": card_names(p["hand"]), "chips": p["chips"]})

    # Blind ante
    for pid in game.solvent_players():
        p = game.players[pid]
        ante = min(BLIND, p["chips"])
        p["chips"] -= ante
        p["bet"]   += ante
        game.pot   += ante
    game.current_bet = BLIND
    game.broadcast({"type": "INFO",
                    "msg": f"  All players post {BLIND}-chip blind. Pot: {game.pot}"})

    stages = [
        ("Pre-Flop", None, None),
        ("Flop",     _deal_community, (3, "Flop")),
        ("Turn",     _deal_community, (1, "Turn")),
        ("River",    _deal_community, (1, "River")),
    ]

    for stage, deal_fn, deal_args in stages:
        if deal_fn:
            deal_fn(*deal_args)
        betting_round(stage)
        if len(game.active_in_hand()) < 2:
            break

    resolve_winner()
    check_for_busted_players()

# ─────────────────────────────────────────────
#  GAME SESSION  (plays multiple games)
# ─────────────────────────────────────────────

def run_game_session():
    while True:
        game.reset_for_new_game()
        total = game.num_rounds
        game.broadcast({"type": "INFO",
                        "msg": (f"\n  Game starting: {len(game.player_order)} players | "
                                f"{total} rounds | {game.starting_chips} chips each")})

        for rnd in range(1, total + 1):
            if len(game.solvent_players()) < 2:
                game.broadcast({"type": "INFO", "msg": "  Not enough solvent players. Ending early."})
                break
            start_round(rnd, total)
            time.sleep(1)

        final = sorted(
            [(game.players[pid]["name"], game.players[pid]["chips"])
             for pid in game.player_order if game.players[pid]["active"]],
            key=lambda x: x[1], reverse=True
        )
        game.broadcast({"type": "GAME_OVER", "standings": final})

        time.sleep(1)
        game.send_to(0, {"type": "HOST_DECISION",
                         "msg": "Type PLAY AGAIN to restart, or END to close."})
        game.broadcast({"type": "INFO", "msg": "\n  Waiting for host to decide..."},
                       exclude=0)

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
#  HOST DECISION
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

# Lobby command dispatch: cmd → (validator, setter, broadcast_status)
_LOBBY_COMMANDS = {
    "PLAYERS": (lambda n: 2 <= n <= 6,  lambda n: setattr(game, "max_players",    n), "Must be 2–6."),
    "CHIPS":   (lambda n: n >= 50,      lambda n: setattr(game, "starting_chips",  n), "Minimum is 50."),
    "ROUNDS":  (lambda n: 1 <= n <= 20, lambda n: setattr(game, "num_rounds",      n), "Must be 1–20."),
}

def run_lobby():
    HOST_PID = 0
    game.broadcast({"type": "INFO", "msg": BANNER})
    game.broadcast(game.lobby_status())
    game.send_to(HOST_PID, {
        "type": "HOST_LOBBY",
        "msg": ("\n  You are the HOST. Commands:\n"
                "    PLAYERS <n>  — max players (2–6)\n"
                "    CHIPS <n>    — starting chips\n"
                "    ROUNDS <n>   — number of rounds (1–20)\n"
                "    START        — begin (need 2+ players)\n"),
    })
    game.broadcast({"type": "INFO", "msg": "  Waiting for host to start the game...\n"},
                   exclude=HOST_PID)

    while True:
        data = receive_line(HOST_PID)
        if data is None:
            game.broadcast({"type": "ERROR", "msg": "Host disconnected. Game cancelled."})
            sys.exit(0)

        cmd = (data.get("cmd") or data.get("action") or "").upper().strip()
        val = data.get("value", "")

        if cmd in _LOBBY_COMMANDS:
            validate, apply, err_msg = _LOBBY_COMMANDS[cmd]
            try:
                n = int(val)
                if validate(n):
                    apply(n)
                    game.send_to(HOST_PID, {"type": "INFO", "msg": f"  {cmd} → {n}"})
                    game.broadcast(game.lobby_status())
                else:
                    game.send_to(HOST_PID, {"type": "ERROR", "msg": err_msg})
            except (ValueError, TypeError):
                game.send_to(HOST_PID, {"type": "ERROR", "msg": f"Usage: {cmd} <number>"})

        elif cmd == "START":
            if len(game.player_order) < 2:
                game.send_to(HOST_PID, {"type": "ERROR",
                                        "msg": "Need at least 2 players first."})
                continue
            game.lobby_open = False
            game.broadcast({"type": "INFO",
                            "msg": f"\n  Game started with {len(game.player_order)} players!"})
            game.lobby_ready.set()
            break

        else:
            game.send_to(HOST_PID, {"type": "ERROR",
                                    "msg": f"Unknown: '{cmd}'. Try PLAYERS / CHIPS / ROUNDS / START"})

# ─────────────────────────────────────────────
#  NAME HANDSHAKE
# ─────────────────────────────────────────────

def get_player_name(conn):
    try:
        conn.settimeout(30)
        conn.sendall(PokerGame._encode({"type": "NAME_REQUEST", "msg": "Enter your name: "}))
        buf = ""
        while True:
            chunk = conn.recv(256).decode()
            if not chunk:
                return "Player"
            buf += chunk
            if "\n" in buf:
                line, _ = buf.split("\n", 1)
                name = str(json.loads(line.strip()).get("name", "")).strip()[:20]
                conn.settimeout(None)
                return name or "Player"
    except Exception:
        return "Player"

# ─────────────────────────────────────────────
#  CLIENT THREAD
# ─────────────────────────────────────────────

ANNIE_MSG = "\n  ♠ ♥ ♦ ♣  Welcome, Annie! You're gonna clean house tonight.  ♣ ♦ ♥ ♠\n"

def handle_client(conn, addr, pid):
    p = game.players[pid]
    print(f"  [SERVER] {p['name']} joined from {addr}")

    if p["name"].strip().lower() == "annie":
        game.send_to(pid, {"type": "INFO", "msg": ANNIE_MSG})

    if pid == 0:
        game.send_to(pid, {"type": "INFO",
                           "msg": "  Others can join while you configure. Type START when ready."})
        run_lobby()
        run_game_session()
    else:
        game.send_to(pid, {"type": "INFO", "msg": "  Waiting for host to start..."})
        game.lobby_ready.wait()
        while p["active"]:
            time.sleep(1)

    try:
        conn.close()
    except OSError:
        pass

# ─────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────

def main():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((HOST, PORT))
    srv.listen(10)

    try:
        local_ip = socket.gethostbyname(socket.gethostname())
    except Exception:
        local_ip = "run `ipconfig` or `hostname -I`"

    print(BANNER)
    print(f"  [SERVER] Port        : {PORT}")
    print(f"  [SERVER] LAN IP      : {local_ip}")
    print(f"  [SERVER] Players set : HOST = \"{local_ip}\" in client.py")
    print(f"  [SERVER] First to connect is the host.\n")

    threads = []

    def accept_loop():
        while True:
            srv.settimeout(1.0)
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                if not game.lobby_open and game.lobby_ready.is_set():
                    break
                continue
            except (OSError, KeyboardInterrupt):
                break

            if not game.lobby_open:
                send_reject(conn, "Game already in progress.")
                continue

            name = get_player_name(conn)
            with game.lock:
                if len(game.player_order) >= game.max_players:
                    send_reject(conn, "Lobby is full.")
                    continue
                pid = game.add_player(conn, addr, name)

            print(f"  [SERVER] {name} joined  ({len(game.player_order)}/{game.max_players})")
            game.broadcast({"type": "INFO",
                            "msg": f"  ++ {name} joined! ({len(game.player_order)}/{game.max_players})"})
            game.broadcast(game.lobby_status())

            t = threading.Thread(target=handle_client, args=(conn, addr, pid), daemon=True)
            threads.append(t)
            t.start()

    acc = threading.Thread(target=accept_loop, daemon=True)
    acc.start()

    try:
        game.lobby_ready.wait()
        for t in threads:
            t.join(timeout=7200)
    except KeyboardInterrupt:
        print("\n  [SERVER] Interrupted.")

    srv.close()
    print("  [SERVER] Closed. Goodbye!")


if __name__ == "__main__":
    main()