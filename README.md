# Virtual Poker Game

A two-player Texas Hold'em-style poker game built with Python sockets and multithreading.

---

## Installation

1. **Python 3.8+** is required. Download it from https://www.python.org/downloads/  
   Verify your installation: `python3 --version`
2. No third-party packages needed — only Python's standard library is used.

---

## How to Run

**Step 1 — Start the server** (do this first, in its own terminal window):
```
python3 server.py
```
You will see the server banner and `Listening on 127.0.0.1:5555`.

**Step 2 — Connect each player** (open a new terminal for each):
```
python3 client.py
```
Both clients must connect before the game begins. The server holds and deals until it has exactly 2 players.

> Both terminals should be on the same machine. To play over a network, change `HOST` in both files to the server machine's IP address.

---

## The Rules

This game plays a simplified version of **Texas Hold'em** over 3 rounds.

1. Each player starts with **1000 chips**.
2. At the start of each round, every player is dealt **2 private cards** (the "hole cards").
3. Betting happens in four stages:
   - **Pre-Flop** — before any community cards are shown.
   - **Flop** — after 3 community cards are revealed.
   - **Turn** — after a 4th community card is revealed.
   - **River** — after the 5th and final community card is revealed.
4. On your turn you may: **BET \<amount\>**, **CHECK** (pass), **FOLD** (give up), or **QUIT** (leave).
5. After the River betting, the best 5-card hand wins the pot.
6. If all other players fold, the last player standing wins automatically.

**Hand rankings (high to low):**
Royal Flush → Straight Flush → Four of a Kind → Full House → Flush → Straight → Three of a Kind → Two Pair → One Pair → High Card

---

## Features

| Feature | Details |
|---|---|
| **Multithreading** | Each client runs in its own thread; the server handles multiple connections simultaneously. |
| **Turn enforcement** | The server controls whose turn it is. A client cannot act out of turn. |
| **Deck manager** | A 52-card deck is shuffled server-side each round; cards cannot be dealt twice. |
| **Pot tracking** | The server tracks all bets and distributes chips to the winner. |
| **Timeout / auto-fold** | Players have 60 seconds to act. Inactivity results in an automatic fold. |
| **Disconnect handling** | If a client drops, the server catches `ConnectionResetError`, folds their hand, and continues. |
| **Input validation** | The client rejects non-numeric or out-of-range bets before anything is sent to the server. |
| **Card formatter** | Raw codes like `As` are displayed as `Ace of Spades`. |
| **Graceful exit** | Type `QUIT` at any time to close your connection cleanly. |
| **Cheat detection** | If a client claims a hand score higher than what the server computed, the player is immediately kicked. |
| **ASCII UI** | Formatted hand boxes, standings tables, and stage headers throughout. |

---

## Protocol

Messages are sent as newline-terminated JSON strings. Each message has a `"type"` field:

| Type | Direction | Purpose |
|---|---|---|
| `WAITING` | S → C | Holding lobby message |
| `HAND` | S → C | Deal private cards |
| `COMMUNITY` | S → C | Reveal community cards |
| `TURN` | S → C | Announce whose turn it is |
| `YOUR_TURN` | S → C | Request an action from this client |
| `INFO` | S → C | General status / log message |
| `WINNER` | S → C | Announce round winner + chip update |
| `SHOWDOWN` | S → C | Reveal hands at showdown |
| `GAME_OVER` | S → C | Final standings after 3 rounds |
| `KICKED` | S → C | Cheat detection — player removed |
| `ERROR` | S → C | Server-side validation error |
| Action | C → S | `{"action": "BET", "amount": 50}` etc. |

---

## Error Handling Summary

- **`ConnectionRefusedError`** on `connect()` → prints "Server not found" and exits cleanly.
- **`socket.timeout`** during connect → prints timeout message and exits.
- **`ConnectionResetError`** on `recv()` → server detects disconnect, folds hand, announces it, continues game.
- **60-second turn timeout** → server auto-folds the inactive player.
- **Invalid bet string** (e.g. "banana") → client catches it before sending to server.
- **Bet exceeding chips** → client warns the player and re-prompts.
- **Unknown action** → server responds with an ERROR message and folds the player.