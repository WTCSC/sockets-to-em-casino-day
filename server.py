"""
Virtual Poker Game - Server
Run this FIRST: python server.py
"""

import socket
import threading
import random
import json
import time


#  ASCII ART & DISPLAY


BANNER = r"""
 ____   ___  _  __ _____  ____      ____   ___  _  __ _____  ____
|  _ \ / _ \| |/ /| ____||  _ \    |  _ \ / _ \| |/ /| ____||  _ \
| |_) | | | |   / |  _|  | |_) |   | |_) | | | |   / |  _|  | |_) |
|  __/| |_| | |\ \| |___ |  _ <    |  __/| |_| | |\ \| |___ |  _ <
|_|    \___/ |_| \_|_____||_| \_\   |_|    \___/ |_| \_|_____||_| \_\
"""

SUITS = {"s": "Spades", "h": "Hearts", "d": "Diamonds", "c": "Clubs"}
RANKS = {
    "2": "2", "3": "3", "4": "4", "5": "5", "6": "6", "7": "7",
    "8": "8", "9": "9", "10": "10", "J": "Jack", "Q": "Queen",
    "K": "King", "A": "Ace"
}

def card_name(code):
    suit = SUITS.get(code[-1], code[-1])
    rank = RANKS.get(code[:-1], code[:-1])
    return f"{rank} of {suit}"


#  DECK MANAGER


def create_deck():
    ranks = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]
    suits = ["s","h","d","c"]
    deck = [r + s for r in ranks for s in suits]
    random.shuffle(deck)
    return deck

def deal_card(deck):
    return deck.pop()


#  HAND EVALUATOR (server is source of truth)

RANK_ORDER = ["2","3","4","5","6","7","8","9","10","J","Q","K","A"]

def rank_value(card):
    return RANK_ORDER.index(card[:-1])

def evaluate_hand(hand):
    """Returns (score 0-9, description string)."""
    ranks = sorted([rank_value(c) for c in hand], reverse=True)
    suits = [c[-1] for c in hand]
    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    cnt = sorted(counts.values(), reverse=True)
    flush = len(set(suits)) == 1
    straight = len(set(ranks)) == 5 and (max(ranks) - min(ranks) == 4)

    if flush and straight and max(ranks) == 12:  return (9, "Royal Flush")
    if flush and straight:                        return (8, "Straight Flush")
    if cnt[0] == 4:                               return (7, "Four of a Kind")
    if cnt[0] == 3 and cnt[1] == 2:              return (6, "Full House")
    if flush:                                     return (5, "Flush")
    if straight:                                  return (4, "Straight")
    if cnt[0] == 3:                               return (3, "Three of a Kind")
    if cnt[0] == 2 and cnt[1] == 2:              return (2, "Two Pair")
    if cnt[0] == 2:                               return (1, "One Pair")
    return (0, "High Card")


#  GAME STATE


class PokerGame:
    def __init__(self):
        self.players = {}
        self.player_order = []
        self.deck = []
        self.pot = 0
        self.current_turn = 0
        self.community_cards = []
        self.lock = threading.Lock()

    def add_player(self, conn, addr):
        pid = len(self.player_order)
        self.players[pid] = {
            "conn": conn, "addr": addr,
            "name": f"Player {pid + 1}",
            "chips": 1000, "hand": [],
            "folded": False, "bet": 0, "active": True
        }
        self.player_order.append(pid)
        return pid

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

    def active_count(self):
        return sum(1 for p in self.players.values()
                   if not p["folded"] and p["active"])


game = PokerGame()
MAX_PLAYERS = 2
waiting_barrier = threading.Barrier(MAX_PLAYERS)

#  CHEAT DETECTION

def cheat_check(pid, claimed_score):
    """Kick player if they claim a stronger hand than they actually hold."""
    hand = game.players[pid]["hand"] + game.community_cards
    real_score, real_desc = evaluate_hand(hand)
    if claimed_score > real_score:
        game.send_to(pid, {
            "type": "KICKED",
            "msg": (f"CHEAT DETECTED: You claimed score {claimed_score} "
                    f"but your real hand is {real_desc} (score={real_score}). "
                    f"You are removed from the game.")
        })
        game.broadcast(
            {"type": "INFO", "msg": f"[!] {game.players[pid]['name']} was KICKED for cheating!"},
            exclude=pid
        )
        game.players[pid]["active"] = False
        game.players[pid]["folded"] = True
        return True
    return False

#  NETWORK HELPER

def receive_with_timeout(pid, timeout=60):
    """Read one JSON line from a player, respecting a timeout."""
    conn = game.players[pid]["conn"]
    conn.settimeout(timeout)
    buf = ""
    try:
        while True:
            chunk = conn.recv(1024).decode()
            if not chunk:
                return None
            buf += chunk
            if "\n" in buf:
                line, _ = buf.split("\n", 1)
                return json.loads(line.strip())
    except socket.timeout:
        return None
    except (ConnectionResetError, json.JSONDecodeError, OSError):
        return None

#  GAME FLOW

def handle_action(pid, data):
    p = game.players[pid]
    action = data.get("action", "").upper()

    if action == "FOLD":
        p["folded"] = True
        game.broadcast({"type": "INFO", "msg": f"  {p['name']} folds."})

    elif action == "CHECK":
        game.broadcast({"type": "INFO", "msg": f"  {p['name']} checks."})

    elif action == "BET":
        amount = data.get("amount", 0)
        if not isinstance(amount, int) or amount <= 0:
            game.send_to(pid, {"type": "ERROR", "msg": "Invalid bet. Must be a positive integer. Auto-folding."})
            p["folded"] = True
            return
        if amount > p["chips"]:
            game.send_to(pid, {"type": "ERROR", "msg": "Not enough chips! Auto-folding."})
            p["folded"] = True
            return
        claimed = data.get("claimed_score", -1)
        if claimed >= 0 and cheat_check(pid, claimed):
            return
        p["chips"] -= amount
        p["bet"] += amount
        game.pot += amount
        game.broadcast({"type": "INFO",
                        "msg": f"  {p['name']} bets {amount} chips. Pot: {game.pot}"})

    elif action == "QUIT":
        p["active"] = False
        p["folded"] = True
        game.broadcast({"type": "INFO", "msg": f"  {p['name']} has left the game."})

    else:
        game.send_to(pid, {"type": "ERROR", "msg": f"Unknown action '{action}'. Treating as fold."})
        p["folded"] = True


def betting_round(stage):
    game.broadcast({"type": "INFO", "msg": f"\n--- {stage} Betting ---  Pot: {game.pot}"})
    for i, pid in enumerate(game.player_order):
        p = game.players[pid]
        if p["folded"] or not p["active"]:
            continue

        game.current_turn = i
        game.broadcast({"type": "TURN", "player": p["name"], "pot": game.pot})
        game.send_to(pid, {
            "type": "YOUR_TURN",
            "stage": stage,
            "pot": game.pot,
            "chips": p["chips"],
            "community": [card_name(c) for c in game.community_cards]
        })

        response = receive_with_timeout(pid, timeout=60)
        if response is None:
            game.broadcast({"type": "INFO",
                            "msg": f"  {p['name']} timed out. Auto-folded."})
            p["folded"] = True
            continue

        handle_action(pid, response)


def resolve_winner():
    active = [(pid, game.players[pid]) for pid in game.player_order
              if not game.players[pid]["folded"] and game.players[pid]["active"]]

    if not active:
        game.broadcast({"type": "INFO", "msg": "All players folded. No winner this round."})
        game.pot = 0
        return

    if len(active) == 1:
        winner_id, winner = active[0]
        winner["chips"] += game.pot
        game.broadcast({
            "type": "WINNER", "player": winner["name"],
            "reason": "Last player standing", "pot": game.pot,
            "chips": {game.players[pid]["name"]: game.players[pid]["chips"]
                      for pid in game.player_order}
        })
        game.pot = 0
        return

    results = []
    for pid, p in active:
        full = p["hand"] + game.community_cards
        score, desc = evaluate_hand(full)
        results.append((score, pid, desc))
        game.send_to(pid, {
            "type": "SHOWDOWN",
            "hand": [card_name(c) for c in p["hand"]],
            "community": [card_name(c) for c in game.community_cards],
            "best": desc
        })

    results.sort(key=lambda x: x[0], reverse=True)
    top = results[0][0]
    winners = [r for r in results if r[0] == top]
    split = game.pot // len(winners)
    for _, pid, _ in winners:
        game.players[pid]["chips"] += split

    game.broadcast({
        "type": "WINNER",
        "player": ", ".join(game.players[pid]["name"] for _, pid, _ in winners),
        "hand": winners[0][2],
        "pot": game.pot,
        "split": len(winners) > 1,
        "chips": {game.players[pid]["name"]: game.players[pid]["chips"]
                  for pid in game.player_order}
    })
    game.pot = 0


def start_round():
    with game.lock:
        game.deck = create_deck()
        game.pot = 0
        game.community_cards = []
        for pid in game.player_order:
            p = game.players[pid]
            p["hand"] = [deal_card(game.deck), deal_card(game.deck)]
            p["folded"] = False
            p["bet"] = 0

    game.broadcast({"type": "INFO", "msg": "\n" + "="*50 + "\n  NEW ROUND\n" + "="*50})

    for pid in game.player_order:
        p = game.players[pid]
        game.send_to(pid, {
            "type": "HAND",
            "cards": p["hand"],
            "display": [card_name(c) for c in p["hand"]],
            "chips": p["chips"]
        })

    betting_round("Pre-Flop")
    if game.active_count() < 2:
        resolve_winner()
        return

    for _ in range(3):
        game.community_cards.append(deal_card(game.deck))
    game.broadcast({
        "type": "COMMUNITY", "stage": "Flop",
        "cards": game.community_cards,
        "display": [card_name(c) for c in game.community_cards]
    })
    betting_round("Flop")
    if game.active_count() < 2:
        resolve_winner()
        return

    game.community_cards.append(deal_card(game.deck))
    game.broadcast({
        "type": "COMMUNITY", "stage": "Turn",
        "cards": game.community_cards,
        "display": [card_name(c) for c in game.community_cards]
    })
    betting_round("Turn")
    if game.active_count() < 2:
        resolve_winner()
        return

    game.community_cards.append(deal_card(game.deck))
    game.broadcast({
        "type": "COMMUNITY", "stage": "River",
        "cards": game.community_cards,
        "display": [card_name(c) for c in game.community_cards]
    })
    betting_round("River")
    resolve_winner()

#  CLIENT THREAD

def handle_client(conn, addr, pid):
    print(f"[SERVER] {game.players[pid]['name']} connected from {addr}")
    game.send_to(pid, {
        "type": "WAITING",
        "msg": f"Connected as {game.players[pid]['name']}. Waiting for other players..."
    })

    try:
        waiting_barrier.wait(timeout=120)
    except threading.BrokenBarrierError:
        game.send_to(pid, {"type": "ERROR", "msg": "Timed out waiting for players."})
        return

    # Player 0 drives the game
    if pid == 0:
        game.broadcast({"type": "INFO", "msg": BANNER})
        game.broadcast({"type": "INFO", "msg": f"All {MAX_PLAYERS} players connected! Starting game!\n"})
        for rnd in range(1, 4):
            actives = [p for p in game.players.values() if p["active"]]
            if len(actives) < 2:
                game.broadcast({"type": "INFO", "msg": "Not enough active players. Game over!"})
                break
            game.broadcast({"type": "INFO", "msg": f"\n*** ROUND {rnd} OF 3 ***"})
            start_round()
            time.sleep(1)

        final = sorted(
            [(game.players[pid]["name"], game.players[pid]["chips"])
             for pid in game.player_order if game.players[pid]["active"]],
            key=lambda x: x[1], reverse=True
        )
        game.broadcast({"type": "GAME_OVER", "standings": final})

#  MAIN

def main():
    HOST = "127.0.0.1"
    PORT = 5555

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((HOST, PORT))
    server_sock.listen(MAX_PLAYERS)

    print(BANNER)
    print(f"[SERVER] Listening on {HOST}:{PORT}  (waiting for {MAX_PLAYERS} players)\n")

    threads = []
    connected = 0
    while connected < MAX_PLAYERS:
        try:
            conn, addr = server_sock.accept()
            conn.settimeout(60)
            with game.lock:
                pid = game.add_player(conn, addr)
            connected += 1
            print(f"[SERVER] Player {pid+1} joined from {addr}  ({connected}/{MAX_PLAYERS})")
            t = threading.Thread(target=handle_client, args=(conn, addr, pid), daemon=True)
            threads.append(t)
            t.start()
        except KeyboardInterrupt:
            print("\n[SERVER] Interrupted. Shutting down.")
            break
        except Exception as e:
            print(f"[SERVER] Error accepting connection: {e}")

    for t in threads:
        t.join()

    server_sock.close()
    print("[SERVER] Server closed.")


if __name__ == "__main__":
    main()
