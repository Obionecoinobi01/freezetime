#!/usr/bin/env python3
"""
feed — the game-agnostic bridge between whatever you are playing and kb1.

Everything downstream reads ONE file, `feed.json`, so the rest of the system
never learns what game you play:

    {"ts":…, "match_id":"…", "state":"lobby|live|over",
     "kills":14, "deaths":9, "assists":3, "kd":1.5556, "round":12,
     "source":"cs2-gsi"}

Anything that can write that file can drive a round. Three ways in:

  listen   Run a local HTTP endpoint and let the game push to it. CS2 and Dota 2
           Game State Integration are HTTP clients: point their `uri` at this and
           they POST JSON on every state change, with a heartbeat in between.
           Nothing is exposed — it binds to 127.0.0.1.

               python3 feed.py listen --profile cs2 --port 31337

           CS2: drop gamestate_integration_kb1.cfg into …/game/csgo/cfg with
           "uri" "http://127.0.0.1:31337/" and the data section enabling
           provider, map, round and player_match_stats.

  set      Type it. Works for any game, including ones with no telemetry at all.

               python3 feed.py set --kills 14 --deaths 9 --state over

  desk     Local control panel for games with no telemetry (Call of Duty,
           anything). Bind 127.0.0.1 and click LOBBY / LIVE / OVER, tap K/D.
           This is the loading-screen freeze time: LIVE closes the round.

               python3 feed.py desk --open

  show     Print what the rest of the system currently believes.

A local feed is fast but only you can see it, so it is the right thing to drive
the live board and the WRONG thing to settle a score on its own. Where the game
has a public per-match API, settle from that and publish the match id.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import socketserver
import sys
import time
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
FEED = os.path.join(HERE, "feed.json")

# Where the numbers live in each source's payload. Add a profile, support a game.
PROFILES = {
    "cs2": {
        "kills":    "player.match_stats.kills",
        "deaths":   "player.match_stats.deaths",
        "assists":  "player.match_stats.assists",
        "round":    "map.round",
        "phase":    "map.phase",          # warmup | live | gameover
        "match_id": "map.name",
        "steamid":  "player.steamid",
    },
    "dota2": {
        "kills":    "player.kills",
        "deaths":   "player.deaths",
        "assists":  "player.assists",
        "phase":    "map.game_state",
        "match_id": "map.matchid",
    },
    # Anything you write yourself: flat keys, no nesting.
    "generic": {
        "kills": "kills", "deaths": "deaths", "assists": "assists",
        "phase": "state", "match_id": "match_id", "round": "round",
    },
    # Call of Duty has no Game State Integration. Same shape as generic;
    # the desk writes this profile by hand.
    "cod": {
        "kills": "kills", "deaths": "deaths", "assists": "assists",
        "phase": "state", "match_id": "match_id", "round": "round",
    },
}

MAX_BODY = 65536
DESK_HTML = os.path.join(HERE, "overlay", "desk.html")

PHASE_TO_STATE = {
    "warmup": "lobby", "live": "live", "intermission": "live",
    "gameover": "over", "postgame": "over",
    "DOTA_GAMERULES_STATE_PRE_GAME": "lobby",
    "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS": "live",
    "DOTA_GAMERULES_STATE_POST_GAME": "over",
}


def dig(obj: dict, path: str):
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def kd(kills, deaths) -> float | None:
    if kills is None or deaths is None:
        return None
    # Zero deaths would divide by zero; the convention everywhere is to treat it
    # as one death so a flawless game reads as its kill count rather than error.
    return round(kills / max(1, deaths), 4)


def read() -> dict:
    if os.path.exists(FEED):
        try:
            return json.load(open(FEED))
        except Exception:
            pass
    return {}


def write(patch: dict, source: str) -> dict:
    cur = read()
    cur.update({k: v for k, v in patch.items() if v is not None})
    cur["kd"] = kd(cur.get("kills"), cur.get("deaths"))
    cur["ts"] = int(time.time())
    cur["source"] = source
    tmp = FEED + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(cur, fh)
    os.replace(tmp, FEED)                    # atomic: readers never see half a file
    return cur


def apply(body: dict, source: str) -> dict:
    """Patch feed.json from the desk (absolute values, increments, or a reset)."""
    if not isinstance(body, dict):
        body = {}
    if body.get("reset"):
        return write({
            "kills": 0, "deaths": 0, "assists": 0, "round": 0,
            "kd": 0.0,
            "match_id": body.get("match_id"),
            "state": body.get("state") or "lobby",
        }, source)
    cur = read()
    patch = {}
    for key in ("kills", "deaths", "assists", "round", "match_id", "state"):
        if key in body and body[key] is not None:
            patch[key] = body[key]
    inc = body.get("inc") if isinstance(body.get("inc"), dict) else {}
    for key in ("kills", "deaths", "assists"):
        delta = inc.get(key)
        if not delta:
            continue
        try:
            patch[key] = int(cur.get(key) or 0) + int(delta)
        except (TypeError, ValueError):
            continue
        if patch[key] < 0:
            patch[key] = 0
    return write(patch, source)


def from_payload(body: dict, profile: dict) -> dict:
    phase = dig(body, profile.get("phase", "")) if profile.get("phase") else None
    return {
        "kills":    dig(body, profile["kills"]),
        "deaths":   dig(body, profile["deaths"]),
        "assists":  dig(body, profile.get("assists", "")) if profile.get("assists") else None,
        "round":    dig(body, profile.get("round", "")) if profile.get("round") else None,
        "match_id": dig(body, profile.get("match_id", "")) if profile.get("match_id") else None,
        "state":    PHASE_TO_STATE.get(str(phase), str(phase) if phase else None),
    }


def listen(port: int, profile_name: str, verbose: bool) -> None:
    profile = PROFILES[profile_name]

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _ok(self):
            self.send_response(200)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_POST(self):                                   # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                self.send_error(413)
                return
            raw = self.rfile.read(n) if n else b"{}"
            self._ok()
            try:
                body = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                return
            cur = write(from_payload(body, profile), f"{profile_name}-push")
            if verbose:
                print(f"  {cur.get('state')}  k={cur.get('kills')} d={cur.get('deaths')} "
                      f"kd={cur.get('kd')} round={cur.get('round')}", flush=True)

        def do_GET(self):                                    # noqa: N802
            body = json.dumps(read()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("127.0.0.1", port), Handler) as srv:
        print(f"feed  ← http://127.0.0.1:{port}/   profile={profile_name}")
        print(f"      → {FEED}")
        srv.serve_forever()


def _send(handler, body: bytes, content_type: str) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def desk(port: int, open_browser: bool) -> None:
    """Director surface for games that will never POST at us — Call of Duty."""

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_OPTIONS(self):                                # noqa: N802
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_GET(self):                                    # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/desk", "/desk.html"):
                try:
                    raw = open(DESK_HTML, encoding="utf-8").read().encode()
                except OSError:
                    self.send_error(500, "overlay/desk.html missing")
                    return
                _send(self, raw, "text/html; charset=utf-8")
                return
            _send(self, json.dumps(read()).encode(), "application/json")

        def do_POST(self):                                   # noqa: N802
            n = int(self.headers.get("Content-Length") or 0)
            if n > MAX_BODY:
                self.send_error(413)
                return
            raw = self.rfile.read(n) if n else b"{}"
            try:
                body = json.loads(raw.decode("utf-8", "replace"))
            except Exception:
                self.send_error(400, "invalid json")
                return
            cur = apply(body, "cod-desk")
            _send(self, json.dumps(cur).encode(), "application/json")

        def log_message(self, *a):
            pass

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    url = f"http://127.0.0.1:{port}/"
    with Server(("127.0.0.1", port), Handler) as srv:
        print(f"desk   → {url}")
        print(f"       → {FEED}")
        print("keys   → 1 lobby  2 live  3 over   K/D/A +/-   Shift minus   R reset")
        if open_browser:
            webbrowser.open(url)
        srv.serve_forever()


def main() -> None:
    p = argparse.ArgumentParser(description="kb1 game feed")
    sub = p.add_subparsers(dest="cmd", required=True)

    l = sub.add_parser("listen")
    l.add_argument("--port", type=int, default=31337)
    l.add_argument("--profile", choices=sorted(PROFILES), default="cs2")
    l.add_argument("-v", "--verbose", action="store_true")

    s = sub.add_parser("set")
    s.add_argument("--kills", type=int)
    s.add_argument("--deaths", type=int)
    s.add_argument("--assists", type=int)
    s.add_argument("--round", type=int)
    s.add_argument("--match", dest="match_id")
    s.add_argument("--state", choices=["lobby", "live", "over"])

    d = sub.add_parser("desk", help="local control panel for Call of Duty and other games with no GSI")
    d.add_argument("--port", type=int, default=31337)
    d.add_argument("--open", action="store_true", help="open the desk in a browser")

    sub.add_parser("show")

    a = p.parse_args()
    if a.cmd == "listen":
        listen(a.port, a.profile, a.verbose)
    elif a.cmd == "desk":
        desk(a.port, a.open)
    elif a.cmd == "set":
        cur = write({k: getattr(a, k) for k in
                     ("kills", "deaths", "assists", "round", "match_id", "state")}, "manual")
        print(json.dumps(cur, indent=2))
    else:
        print(json.dumps(read(), indent=2))


if __name__ == "__main__":
    main()
