# Local web server for the Pig app. Uses only what ships with Python.
#
#   python server.py          then open http://localhost:8000
#   python server.py 8080     to use a different port
#   python server.py 8000 https://example.trycloudflare.com
#                             to show a tunnel address on the party screen
#
# On a host such as Render, the port comes from the PORT environment variable and
# the public address from RENDER_EXTERNAL_URL (or PUBLIC_URL), so no arguments are needed.
#
# All game rules and all AI decisions come from pig.py (GameState, GameNode, sim).

import json
import os
import random
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from pig import GameState, GameNode, sim

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_SEATS = 8
TARGETS = (30, 50, 100)
LEVELS = {"easy": 60, "medium": 1000, "hard": 8000}          # simulations per decision
SPEEDS = {"slow": 1.6, "normal": 0.8, "fast": 0.25, "turbo": 0.03}   # seconds per AI move
THINK_TIME_LIMIT = 4.0                                       # seconds, cap on one AI decision
ROLLOUT_HOLD_AT = 20

MAX_GAMES = int(os.environ.get("PIG_MAX_GAMES", 30))        # most games kept at once
IDLE_SECONDS = int(os.environ.get("PIG_IDLE_SECONDS", 900))  # a game nobody has touched for this long is removed
MAX_BODY = 20000                                             # largest request body accepted, in bytes
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "").rstrip("/") or None

GAMES = {}
GAMES_LOCK = threading.Lock()


def new_id(length):
    letters = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    while True:
        value = "".join(random.choice(letters) for _ in range(length))
        if value not in GAMES and not any(g.code == value for g in list(GAMES.values())):
            return value


class Game:
    def __init__(self, mode, target, seats, phones):
        self.id = new_id(8)
        self.code = new_id(4)                 # short code people type on their phones
        self.mode = mode                      # "watch", "vs" or "party"
        self.target = target
        self.seats = seats                    # list of {"name", "kind", "level", "token"}
        self.phones = phones
        self.started = False
        self.speed = "normal"
        self.paused = False
        self.lock = threading.Lock()
        self.last_active = time.time()
        self.reset()

    def touch(self):
        self.last_active = time.time()

    def reset(self):
        first = random.randrange(len(self.seats)) if self.seats else 0
        self.state = GameState([0] * max(len(self.seats), 1), first, 0, self.target)
        self.winner = None
        self.last = None                      # the most recent thing that happened
        self.log = []
        self.thinking = None
        self.event_count = 0
        self.round = getattr(self, "round", 0) + 1

    # ---- playing moves (every rule comes from GameState) ----
    def apply(self, action):
        seat = self.state.cur_player
        if action == "roll":
            rolled = self.state.roll_dice()
            self.state = self.state.roll_reaction(rolled)
            event = {"seat": seat, "type": "bust" if rolled == 1 else "roll", "value": rolled}
        else:
            banked = self.state.cur_tot
            self.state = self.state.hold_reaction()
            event = {"seat": seat, "type": "hold", "value": banked}
        self.event_count += 1
        event["n"] = self.event_count
        self.last = event
        self.log.append(event)
        self.log = self.log[-40:]
        self.winner = self.state.is_won()

    def public(self):
        return {
            "id": self.id, "code": self.code, "mode": self.mode, "target": self.target,
            "phones": self.phones, "started": self.started, "round": self.round,
            "seats": [{"name": s["name"], "kind": s["kind"], "level": s.get("level")} for s in self.seats],
            "scores": list(self.state.scores) if self.seats else [],
            "current": self.state.cur_player, "turn_total": self.state.cur_tot,
            "options": self.state.action_options() if self.started and self.winner is None else [],
            "winner": self.winner, "last": self.last, "log": self.log[-12:],
            "thinking": self.thinking, "speed": self.speed, "paused": self.paused,
        }

    # ---- the AI's turns, run on a background thread ----
    def run_ai(self, my_round):
        while True:
            time.sleep(0.02)
            with self.lock:
                if self.round != my_round or self.winner is not None:
                    return
                seat = self.seats[self.state.cur_player]
                waiting = self.paused or seat["kind"] != "ai"
                state = self.state
                seat_index = state.cur_player
                delay = SPEEDS[self.speed]
            if waiting:
                time.sleep(0.05)
                continue

            options = state.action_options()
            if len(options) == 1:
                choice = options[0]
                with self.lock:
                    self.thinking = {"seat": seat_index, "forced": True, "choice": choice}
                time.sleep(delay * 0.6)
            else:
                choice = self.think(state, seat_index, LEVELS[seat["level"]], delay, my_round)
                if choice is None:
                    return
            time.sleep(delay * 0.4)

            with self.lock:
                while self.paused and self.round == my_round:
                    self.lock.release()
                    time.sleep(0.05)
                    self.lock.acquire()
                if self.round != my_round or self.winner is not None:
                    return
                self.apply(choice)

    def think(self, state, seat_index, total_sims, delay, my_round):
        root = GameNode(state)
        batches = 12
        per_batch = max(1, total_sims // batches)
        done = 0
        started = time.time()
        for _ in range(batches):
            for _ in range(per_batch):
                sim(root, ROLLOUT_HOLD_AT)
            done += per_batch
            roll_visits, roll_wins = root.existing_vals("roll")
            hold_visits, hold_wins = root.existing_vals("hold")
            with self.lock:
                if self.round != my_round:
                    return None
                self.thinking = {
                    "seat": seat_index, "forced": False, "choice": None,
                    "done": done, "total": per_batch * batches,
                    "roll": {"visits": roll_visits, "wins": roll_wins},
                    "hold": {"visits": hold_visits, "wins": hold_wins},
                }
            if time.time() - started > THINK_TIME_LIMIT:
                break
            time.sleep(delay * 0.6 / batches)

        # same final pick as ai_make_choice in pig.py: the most visited action
        choice = "hold" if root.existing_vals("hold")[0] > root.existing_vals("roll")[0] else "roll"
        with self.lock:
            if self.thinking:
                self.thinking["choice"] = choice
                self.thinking["total"] = done
        return choice

    def start(self):
        self.started = True
        threading.Thread(target=self.run_ai, args=(self.round,), daemon=True).start()


def remove_idle_games():
    # runs forever on a background thread; frees games nobody has used for a while
    while True:
        time.sleep(max(1, min(30, IDLE_SECONDS // 3)))
        cutoff = time.time() - IDLE_SECONDS
        with GAMES_LOCK:
            for game_id in [i for i, g in GAMES.items() if g.last_active < cutoff]:
                game = GAMES.pop(game_id)
                game.round += 1                  # makes this game's AI thread stop
                game.paused = False


def lan_address():
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))          # nothing is sent; this just picks an interface
        address = probe.getsockname()[0]
        probe.close()
        return address
    except OSError:
        return "127.0.0.1"


def clean_name(text, fallback):
    text = "".join(ch for ch in str(text or "") if ch.isprintable()).strip()[:16]
    return text or fallback


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def fail(self, message, status=400):
        self.send_json({"error": message}, status)

    def do_GET(self):
        url = urlparse(self.path)
        query = parse_qs(url.query)
        if url.path in ("/", "/index.html"):
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        elif url.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif url.path == "/api/info":
            port = self.server.server_address[1]
            self.send_json({"join_url": PUBLIC_URL or f"http://{lan_address()}:{port}",
                            "public": PUBLIC_URL is not None, "levels": LEVELS,
                            "targets": TARGETS, "max_seats": MAX_SEATS})
        elif url.path == "/api/state":
            game = GAMES.get(query.get("id", [""])[0])
            if game is None:
                return self.fail("That game no longer exists. Start a new one.", 404)
            game.touch()
            with game.lock:
                self.send_json(game.public())
        elif url.path == "/api/heatmap":
            target = query.get("target", ["100"])[0]
            if target not in [str(t) for t in TARGETS]:
                return self.fail("Unknown target score.")
            result = {"target": int(target), "exact": None, "mcts": None}
            for kind in ("exact", "mcts"):
                path = os.path.join(HERE, "data", f"{kind}_{target}.json")
                if os.path.exists(path):
                    with open(path) as f:
                        result[kind] = json.load(f)
            self.send_json(result)
        else:
            self.fail("Not found.", 404)

    def do_POST(self):
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > MAX_BODY:
                return self.fail("That request is too large.", 413)
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            return self.fail("Could not read that request.")

        if url.path == "/api/new":
            return self.new_game(data)
        if url.path == "/api/join":
            return self.join(data)

        game = GAMES.get(str(data.get("id", "")))
        if game is None:
            return self.fail("That game no longer exists. Start a new one.", 404)
        game.touch()

        with game.lock:
            if url.path == "/api/start":
                if game.started:
                    return self.send_json(game.public())
                if len(game.seats) < 2:
                    return self.fail("A game needs at least 2 players.")
                game.reset()
                game.start()
            elif url.path == "/api/act":
                error = self.act(game, data)
                if error:
                    return self.fail(error)
            elif url.path == "/api/control":
                if data.get("speed") in SPEEDS:
                    game.speed = data["speed"]
                if "paused" in data:
                    game.paused = bool(data["paused"])
            elif url.path == "/api/rematch":
                game.reset()
                game.start()
            else:
                return self.fail("Not found.", 404)
            self.send_json(game.public())

    def act(self, game, data):
        if not game.started or game.winner is not None:
            return "There is no move to make right now."
        seat = game.seats[game.state.cur_player]
        if seat["kind"] != "human":
            return "It is not a human player's turn."
        if game.phones and data.get("token") != seat["token"]:
            return "It is not your turn."
        if data.get("action") not in game.state.action_options():
            return "That move is not allowed right now."
        game.thinking = None
        game.apply(data["action"])
        return None

    def new_game(self, data):
        mode = data.get("mode")
        target = data.get("target")
        phones = bool(data.get("phones"))
        if mode not in ("watch", "vs", "party") or target not in TARGETS:
            return self.fail("Unknown game settings.")
        seats = []
        for number, entry in enumerate(data.get("seats", [])[:MAX_SEATS], start=1):
            kind = "ai" if entry.get("kind") == "ai" else "human"
            if phones and kind == "human":
                continue                              # phone players add themselves by joining
            level = entry.get("level") if entry.get("level") in LEVELS else "medium"
            seats.append({"name": clean_name(entry.get("name"), f"Player {number}"),
                          "kind": kind, "level": level if kind == "ai" else None, "token": None})
        if not phones and len(seats) < 2:
            return self.fail("A game needs at least 2 players.")
        game = Game(mode, target, seats, phones)
        with GAMES_LOCK:
            if len(GAMES) >= MAX_GAMES:
                return self.fail("The server is full right now. Try again in a few minutes.", 503)
            GAMES[game.id] = game
        if not phones:
            game.start()
        self.send_json(game.public())

    def join(self, data):
        code = str(data.get("code", "")).strip().upper()
        game = next((g for g in list(GAMES.values()) if g.code == code and g.phones), None)
        if game is None:
            return self.fail("No game has that code. Check the code on the host's screen.")
        game.touch()
        with game.lock:
            if game.started:
                return self.fail("That game has already started.")
            if len(game.seats) >= MAX_SEATS:
                return self.fail(f"That game is full ({MAX_SEATS} players).")
            token = new_id(12)
            name = clean_name(data.get("name"), f"Player {len(game.seats) + 1}")
            game.seats.append({"name": name, "kind": "human", "level": None, "token": token})
            game.reset()
            self.send_json({"id": game.id, "seat": len(game.seats) - 1, "token": token})


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT", 8000))
    if len(sys.argv) > 2:
        PUBLIC_URL = sys.argv[2].rstrip("/")
    threading.Thread(target=remove_idle_games, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Pig is running.")
    print(f"  On this computer:  http://localhost:{port}")
    if PUBLIC_URL:
        print(f"  For phones, through your tunnel:  {PUBLIC_URL}")
    else:
        print(f"  For phones on the same network:  http://{lan_address()}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
