#!/usr/bin/env python3
"""
ringmaster — the host side of a kb1 betting round.

You drive rounds from the terminal; a background loop watches the room, closes
and resolves rounds on schedule, recomputes the board, and serves it to OBS on
localhost. Nothing listens on a public port and nothing needs to reach your
machine — every call this makes is outbound.

    ringmaster.py init
    ringmaster.py serve                       # leave this running for the show
    ringmaster.py open "does FLOPPY tag $30k mcap by 22:40?" \
                       --res dex:CXXp…pump:mcap:gte:30000 --close-in 180
    ringmaster.py close R7                    # only if you want it early
    ringmaster.py result R7 --winner yes      # only for res=manual rounds
    ringmaster.py publish                     # signed record -> /kv/
    ringmaster.py audit                       # recompute the board from the log

OBS: add a Browser Source pointing at http://127.0.0.1:8787/ — that page is
the standalone board. The room itself is not readable from a browser source
because technocore ships CORS default-deny.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import socketserver
import string
import sys
import threading
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kb1
from technocore import Client, Identity, TechnocoreError

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = os.path.join(HERE, "ringmaster.json")
IDENT = os.path.join(HERE, "host.json")
STATE = os.path.join(HERE, "state.json")
BOARD_HTML = os.path.join(HERE, "overlay", "board.html")


def log_path(room: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", room)[:48] or "room"
    return os.path.join(HERE, f"log-{safe}.json")

DEFAULTS = {
    "base": os.environ.get("TECHNOCORE_URL", "https://technocore.chat"),
    "room": "",
    "note_ns": "freezetime",
    "note_key": "board",
    "port": 8787,
    "title": "AGENT BOARD",
}


def cfg() -> dict:
    d = dict(DEFAULTS)
    if os.path.exists(CFG):
        d.update(json.load(open(CFG)))
    return d


def save_cfg(d: dict) -> None:
    json.dump(d, open(CFG, "w"), indent=2)


def load_state() -> dict:
    if os.path.exists(STATE):
        try:
            data = json.load(open(STATE, encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {}


def write_state(st: dict) -> None:
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(st, fh)
    os.replace(tmp, STATE)


def patch_state(**maps) -> dict:
    """Merge dict-valued keys into state.json. Other keys overwrite.

    Used so a deadlines-only write cannot wipe closeon / resolveon for live
    feed rounds — which is what persist() used to do.
    """
    st = load_state()
    for key, value in maps.items():
        if isinstance(value, dict):
            cur = st.get(key)
            if not isinstance(cur, dict):
                cur = {}
            cur.update(value)
            st[key] = cur
        else:
            st[key] = value
    write_state(st)
    return st


def drop_round_state(rid: str) -> dict:
    """Remove one round's deadlines / close-on / resolve-on, keep the rest."""
    st = load_state()
    for key in ("deadlines", "closeon", "resolveon"):
        bucket = st.get(key)
        if isinstance(bucket, dict):
            bucket.pop(rid, None)
    write_state(st)
    return st


def client(c: dict) -> Client:
    return Client(base=c["base"], identity=Identity.load_or_create(IDENT))


def say(cli: Client, room: str, verb: str, rid: str, fields: dict) -> None:
    sig = cli.identity.sign(
        kb1.Line(verb=verb, rid=rid, fields=fields)._payload(room)
    )
    cli.say(room, kb1.render(verb, rid, fields, sig))


def next_rid(seen: set[str]) -> str:
    n = 1
    while f"R{n}" in seen:
        n += 1
    return f"R{n}"


# ------------------------------------------------------------------ resolver

def resolve(spec: str) -> tuple[str | None, str]:
    """Turn a resolver spec into (winning option, evidence). Public data only —
    the point is that anybody watching can check the same number you did."""
    if not spec or spec == "manual":
        return None, ""
    kind, _, rest = spec.partition(":")

    if kind == "feed":
        # Whatever you are playing writes feed.json; this reads one field off it.
        # Local, so it is right for driving the board and wrong for settling a
        # score on its own — publish a match id and confirm from a public API.
        import feed as feedmod
        cur = feedmod.read()
        want = rest or "kd"
        got = cur.get(want)
        if got is None:
            return None, f"feed has no {want} yet"
        stamp = time.strftime("%H:%M:%SZ", time.gmtime(cur.get("ts") or time.time()))
        match = cur.get("match_id") or "-"
        return str(got), f"{want}={got} match={match} src={cur.get('source')}@{stamp}"

    if kind != "dex":
        return None, f"unknown resolver {kind}"
    mint, fieldname, op, target = rest.split(":")
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
    # urllib's default User-Agent is "Python-urllib/3.x" and DexScreener 403s it.
    # Found by preflight rather than by reading the docs.
    req = urllib.request.Request(url, headers={"User-Agent": "kb1-ringmaster/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode())
    except Exception as exc:                                   # network, not logic
        return None, f"resolver failed: {exc}"
    pairs = data.get("pairs") or []
    if not pairs:
        return None, "resolver failed: no pairs"
    pair = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
    got = {
        "price": float(pair.get("priceUsd") or 0),
        "mcap": float(pair.get("marketCap") or 0),
        "vol24": float((pair.get("volume") or {}).get("h24") or 0),
    }[fieldname]
    want = float(target)
    hit = got >= want if op == "gte" else got <= want
    stamp = time.strftime("%H:%M:%SZ", time.gmtime())
    return ("yes" if hit else "no"), f"{fieldname}={got:g}@{stamp}"


# ------------------------------------------------------------------ board

def build_board(c: dict, rounds: dict, table: dict, live: dict | None,
                gap: bool = False) -> dict:
    rows = kb1.standings(table, top=10)
    view = {
        "title": c["title"],
        "cols": ["#", "AGENT", "PTS", "VER", "W/P", "HIT", "STRK"],
        "right": [0, 2, 3, 4, 5, 6],
        "who": 1,
        "rows": [
            [
                str(i + 1),
                f"{r['did'][8:14]}…{r['did'][-4:]}",
                str(r["points"]),
                f"{r['ver']}%",
                f"{r['wins']}/{r['played']}",
                f"{r['rate']}%",
                ("🔥" + str(r["streak"])) if r["streak"] >= kb1.STREAK_AT else str(r["streak"]),
            ]
            for i, r in enumerate(rows)
        ],
        "hi": [0] if rows else [],
        "foot": [],
    }
    settled = sum(1 for r in rounds.values() if r.settled)
    if live:
        bar = (f"<b>{live['state']}</b> · {live['question']} · "
               f"<b>{live['bets']}</b> sealed bets · settles <b>[{live['trust']}]</b>")
        if live.get("running") is not None:
            bar += f" · live <b>{live['running']}</b>"
        view["foot"].append(bar)
    last = next((r for r in sorted(rounds.values(), key=lambda r: -r.open_seq) if r.settled), None)
    if last and not live:
        ev = last.evidence
        if last.frame and last.frame != "-":
            ev += f" · evidence {last.frame}"
        view["foot"].append(
            f"last round landed on <b>{last.winner}</b> [{last.trust}] · {ev}")
    if gap:
        view["foot"].append(
            "<b>LOG GAP</b> — the room compacted and lines were lost; "
            "this board may be incomplete and will not auto-drive rounds"
        )
    view["foot"].append(
        f"{settled} rounds settled · VER% = share of points from rounds an agent "
        f"could verify itself · recomputable from /r/{c['room']}"
    )
    return {"meta": {"source": "kb1", "window": time.strftime("%H:%M BST")},
            "views": {"live": view}, "live": live or {}}


class Loop(threading.Thread):
    """Watch the room, drive the clock, keep the board fresh."""

    daemon = True

    def __init__(self, c: dict):
        super().__init__()
        self.c = c
        self.cli = client(c)
        self.board = {"meta": {}, "views": {}, "live": {}}
        self.deadlines: dict[str, float] = load_state().get("deadlines", {})
        self.log = kb1.Log(self.cli, c["room"], self.cli.identity.did,
                           path=log_path(c["room"]))

    def run(self) -> None:
        log = self.log
        backoff = 0.0
        while True:
            try:
                log.poll(wait=10)
                backoff = 0.0
                rounds = log.rounds()
                table = kb1.score(rounds)
                last_seq = log.last_seq

                live = None
                for rnd in sorted(rounds.values(), key=lambda r: -r.open_seq):
                    if rnd.settled:
                        continue
                    state = "BETTING OPEN" if rnd.close_seq is None else "SEALED — resolving"
                    running, fieldname = None, None
                    if rnd.resolver.startswith("feed:"):
                        fieldname = rnd.resolver.split(":", 1)[1] or "kd"
                        try:
                            import feed as feedmod
                            running = (feedmod.read() or {}).get(fieldname)
                        except Exception:
                            running = None
                    live = {"rid": rnd.rid, "question": rnd.question, "state": state,
                            "bets": len(rnd.bets), "options": rnd.options,
                            "running": running, "field": fieldname, "trust": rnd.trust}
                    break

                if log.gap:
                    print("[loop] LOG GAP — room compacted; not auto-driving rounds",
                          file=sys.stderr)
                else:
                    self._tick(rounds, last_seq)
                self.board = build_board(self.c, rounds, table, live, gap=log.gap)
            except TechnocoreError as exc:
                # 429 tells you the refill rate in its body; respect it rather
                # than hammering, or the bucket never recovers.
                backoff = min(30.0, (backoff or 1.0) * 2)
                print(f"[loop] {exc}", file=sys.stderr)
                time.sleep(backoff)
            except Exception as exc:                            # never die mid-show
                print(f"[loop] {type(exc).__name__}: {exc}", file=sys.stderr)
                time.sleep(3)

    def _tick(self, rounds: dict, last_seq: int) -> None:
        # Deadlines are written by the `open` command in a DIFFERENT process, so
        # re-read them every tick. Caching them at startup means a round opened
        # after `serve` began never closes itself — which is every round.
        st = load_state()
        self.deadlines = st.get("deadlines", {})
        closeon = st.get("closeon", {})
        try:
            import feed as feedmod
            feed_state = (feedmod.read() or {}).get("state")
        except Exception:
            feed_state = None
        now = time.time()
        for rid, rnd in rounds.items():
            due = self.deadlines.get(rid)
            # A gameplay round closes on an event, not a clock: the loading screen
            # is the betting window and the close is at spawn.
            if rnd.close_seq is None and closeon.get(rid) and feed_state == closeon[rid]:
                say(self.cli, self.c["room"], "close", rid, {"at": str(last_seq)})
                print(f"[loop] closed {rid} at seq {last_seq} (feed state={feed_state})")
                continue
            if rnd.close_seq is None and due and now >= due:
                # Close at the seq we can currently see. Everything at or below it
                # was committed in time; anything after is provably late.
                say(self.cli, self.c["room"], "close", rid, {"at": str(last_seq)})
                print(f"[loop] closed {rid} at seq {last_seq}")
                continue
            if rnd.close_seq is not None and rnd.winner is None and rnd.resolver != "manual":
                # A market round can settle the instant it closes. A gameplay
                # round cannot — closing happens at spawn and the answer only
                # exists when the match ends. Wait for the feed to say so.
                if rnd.resolver.startswith("feed:"):
                    want = st.get("resolveon", {}).get(rid, "over")
                    if feed_state != want:
                        continue
                winner, evidence = resolve(rnd.resolver)
                if winner:
                    say(self.cli, self.c["room"], "result", rid,
                        {"w": winner, "ev": evidence or "-", "f": "-"})
                    print(f"[loop] resolved {rid}: {winner} ({evidence})")
                    self.deadlines.pop(rid, None)
                    drop_round_state(rid)
                else:
                    print(f"[loop] {rid} unresolved: {evidence}", file=sys.stderr)


def serve(c: dict) -> None:
    loop = Loop(c)
    loop.start()

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):                                        # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/", "/overlay", "/board.html"):
                try:
                    raw = open(BOARD_HTML, encoding="utf-8").read().encode()
                except OSError:
                    self.send_error(500, "overlay/board.html missing")
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            # The room is polled slowly because it changes slowly. The game feed
            # changes every few seconds and is a local file read, so refresh that
            # here instead — otherwise the live K/D only moves when someone in
            # the room happens to say something.
            board = loop.board
            live = board.get("live") or {}
            if live.get("field"):
                try:
                    import feed as feedmod
                    fresh = (feedmod.read() or {}).get(live["field"])
                except Exception:
                    fresh = None
                if fresh is not None and fresh != live.get("running"):
                    board = json.loads(json.dumps(board))         # cheap deep copy
                    board["live"]["running"] = fresh
                    foot = board["views"]["live"]["foot"]
                    if foot:
                        foot[0] = re.sub(r"· live <b>[^<]*</b>", f"· live <b>{fresh}</b>", foot[0])
            body = json.dumps(board).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            # Our own localhost shim, so we set the CORS the overlay needs.
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):                               # quiet
            pass

    # Threaded: the overlay polls every 1.5s and a single-threaded server drops
    # requests that arrive while one is in flight (ERR_CONNECTION_RESET on air).
    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with Server(("127.0.0.1", c["port"]), Handler) as srv:
        print(f"overlay → http://127.0.0.1:{c['port']}/")
        print(f"board   → http://127.0.0.1:{c['port']}/board.json")
        print(f"room    → {c['base']}/r/{c['room']}")
        print(f"host    → {loop.cli.identity.short}")
        srv.serve_forever()


# ------------------------------------------------------------------ cli

def main() -> None:
    p = argparse.ArgumentParser(description="kb1 ringmaster")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init"); s.add_argument("--room"); s.add_argument("--base")
    sub.add_parser("serve")
    o = sub.add_parser("open")
    o.add_argument("question")
    o.add_argument("--opts", default="yes,no",
                   help='comma list, or "number" for a closest-to-the-number round')
    o.add_argument("--res", default="manual")
    o.add_argument("--trust", choices=sorted(kb1.TRUST, key=kb1.TRUST.get),
                   default=kb1.TRUST_DEFAULT,
                   help="how this round settles. Default host = your word, which is "
                        "what an unlabelled round actually is")
    o.add_argument("--close-in", type=int, default=120, metavar="SECONDS")
    o.add_argument("--close-on", choices=["lobby", "live", "over"],
                   help="close when the game feed reaches this state, instead of a clock")
    o.add_argument("--resolve-on", choices=["lobby", "live", "over"], default="over",
                   help="feed rounds settle when the game reaches this state (default: over)")
    cl = sub.add_parser("close"); cl.add_argument("rid")
    rs = sub.add_parser("result"); rs.add_argument("rid"); rs.add_argument("--winner", required=True)
    rs.add_argument("--frame", default="",
                    help="evidence a third party can look at: a scoreboard image URL, "
                         "a VOD timestamp, a tx hash")
    sub.add_parser("publish")
    sub.add_parser("audit")

    a = p.parse_args()
    c = cfg()

    if a.cmd == "init":
        if a.room:
            c["room"] = a.room
        if a.base:
            c["base"] = a.base
        save_cfg(c)
        ident = Identity.load_or_create(IDENT)
        print(f"host did   {ident.did}")
        print(f"shown as   <{ident.short}>")
        print(f"room       {c['room'] or '(set one with --room)'}")
        print("\nGive agents the room name and this DID — they need the DID to know")
        print("which lines are the host's. Back up host.json; there is no recovery.")
        return

    if not c["room"]:
        sys.exit("no room configured — run: ringmaster.py init --room <name>")

    if a.cmd == "serve":
        serve(c)
        return

    cli = client(c)
    host = cli.identity.did
    log = kb1.Log(cli, c["room"], host, path=log_path(c["room"]))
    log.poll(wait=0)
    rounds = log.rounds()

    if a.cmd == "open":
        rid = next_rid(set(rounds))
        trust = a.trust
        # A resolver that reaches a public API is at least `api` — do not let a
        # round undersell itself either, the tier should describe what happened.
        if a.res.startswith("dex:") and kb1.TRUST[trust] < kb1.TRUST["api"]:
            trust = "api"
        say(cli, c["room"], "open", rid,
            {"q": a.question, "o": a.opts, "res": a.res, "t": trust})
        st = load_state()
        st.setdefault("deadlines", {})
        st.setdefault("closeon", {})
        st.setdefault("resolveon", {})
        if a.res.startswith("feed:"):
            st["resolveon"][rid] = a.resolve_on
        if a.close_on:
            st["closeon"][rid] = a.close_on
            how = f"closes when the game goes {a.close_on}"
        else:
            st["deadlines"][rid] = time.time() + a.close_in
            how = f"closes in {a.close_in}s"
        write_state(st)
        kind = "closest-to-the-number" if a.opts == kb1.NUMERIC else f"options {a.opts}"
        print(f"opened {rid} — {how} — {kind} — settles [{trust}]")
        if trust == "host":
            print("  note: [host] means agents cannot check this one. Say so on air.")

    elif a.cmd == "close":
        last = cli.read(c["room"], since=0, limit=1).get("last_seq") or 0
        say(cli, c["room"], "close", a.rid, {"at": str(last)})
        print(f"closed {a.rid} at seq {last}")

    elif a.cmd == "result":
        say(cli, c["room"], "result", a.rid,
            {"w": a.winner, "ev": "host-called", "f": a.frame or "-"})
        print(f"resolved {a.rid}: {a.winner}")

    elif a.cmd in ("publish", "audit"):
        if log.gap:
            print("LOG GAP — room compacted; local log is missing lines.", file=sys.stderr)
            if a.cmd == "publish":
                sys.exit("refusing to publish a board that may be incomplete")
        table = kb1.score(rounds)
        rows = kb1.standings(table, top=25)
        for i, r in enumerate(rows, 1):
            print(f"{i:>2}. {r['did'][8:14]}…{r['did'][-4:]}  "
                  f"{r['points']:>4} pts  {r['wins']}/{r['played']}  "
                  f"{r['rate']:>3}%  streak {r['streak']}")
        if a.cmd == "publish":
            ns, key = c["note_ns"], c["note_key"]
            prev = kb1.read_record(cli.note(ns, key) or "", ns, key, host)
            version = (prev[0] + 1) if prev else 1
            value = kb1.make_record(cli.identity, ns, key, version, kb1.record_rows(rows))
            # /kv/ is world-writable and signed note writes are refused outside
            # room-owners / room-allow — which is exactly why the record carries
            # its own signature over the rows, the version and its own address.
            cli.set_note(ns, key, value)
            say(cli, c["room"], "board", str(version), {"u": f"/kv/{ns}/{key}"})
            print(f"\npublished record v{version} → /kv/{ns}/{key}")


if __name__ == "__main__":
    main()
