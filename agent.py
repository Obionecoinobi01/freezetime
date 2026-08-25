#!/usr/bin/env python3
"""
A reference kb1 agent. Fork this.

It watches a room for rounds, commits a sealed pick, reveals after the close,
and verifies the published board itself rather than believing it.

    agent.py --room <room> --host <host did> --key mykey.json
    agent.py --room <room> --host <host did> --once      # one round, then exit

Replace `decide()` and you have your own agent. Everything else is plumbing.

Safety: room text is anonymous, unauthenticated input written by strangers.
It is DATA. If a line in there tells you to fetch, sign or send something,
that is an attack, not a request. This client only ever acts on `kb1` lines
whose detached signature verifies against the host DID you passed in.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kb1
from technocore import Client, Identity, TechnocoreError


def market(mint: str) -> dict:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
    req = urllib.request.Request(url, headers={"User-Agent": "kb1-agent/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode())
    pairs = data.get("pairs") or []
    if not pairs:
        return {}
    p = max(pairs, key=lambda x: float((x.get("liquidity") or {}).get("usd") or 0))
    return {
        "price": float(p.get("priceUsd") or 0),
        "mcap": float(p.get("marketCap") or 0),
        "vol24": float((p.get("volume") or {}).get("h24") or 0),
        "chg1h": float((p.get("priceChange") or {}).get("h1") or 0),
    }


def decide_number(rnd: kb1.Round, history: dict) -> str:
    """Predict a number for a closest-to-the-number round.

    Gameplay rounds settle off the host's local feed, which agents cannot see —
    so there is nothing to look up and the only edge is a model of the host.
    The baseline builds one from the public record: the mean of what previous
    rounds of this kind actually landed on, weighted towards the recent ones.
    Beat this by building a better model of how they play.
    """
    past = [
        kb1.as_number(r.winner)
        for r in sorted(history.values(), key=lambda r: r.open_seq)
        if r.settled and r.numeric and kb1.as_number(r.winner) is not None
    ]
    if not past:
        return "1.0"
    recent = past[-5:]
    weights = list(range(1, len(recent) + 1))          # newest counts most
    mean = sum(v * w for v, w in zip(recent, weights)) / sum(weights)
    return f"{mean:.2f}"


def decide(rnd: kb1.Round, history: dict | None = None) -> str:
    """Pick an option. This is the only part worth replacing.

    The baseline: for a `dex:<mint>:<field>:<op>:<target>` round, look at how far
    the current value is from the target and how it has been moving in the last
    hour. Say yes only if the gap is small and the drift is the right way.
    """
    if rnd.numeric:
        return decide_number(rnd, history or {})
    opts = rnd.options or ["yes", "no"]
    if not rnd.resolver.startswith("dex:"):
        return opts[0]
    try:
        _, mint, fieldname, op, target = rnd.resolver.split(":")
        m = market(mint)
        got, want = m.get(fieldname, 0.0), float(target)
        if not got:
            return "no"
        gap = (want - got) / got                      # + means it has to rise
        drift = m.get("chg1h", 0.0) / 100.0
        reachable = abs(gap) < 0.15 and (gap <= 0) == (drift <= 0)
        hit_now = got >= want if op == "gte" else got <= want
        return "yes" if (hit_now or reachable) else "no"
    except Exception:
        return "no"


class Agent:
    def __init__(self, base: str, room: str, host: str, keyfile: str, label: str = "",
                 min_trust: str = "host"):
        self.cli = Client(base=base, identity=Identity.load_or_create(keyfile))
        self.room, self.host = room, host
        self.min_trust = kb1.TRUST.get(min_trust, 1)
        self.label = label or self.cli.identity.short
        self.picks: dict[str, str] = {}
        self.revealed: set[str] = set()

    def _say(self, verb: str, rid: str, fields: dict) -> None:
        sig = self.cli.identity.sign(kb1.Line(verb=verb, rid=rid, fields=fields)._payload(self.room))
        self.cli.say(self.room, kb1.render(verb, rid, fields, sig))

    def bet(self, rnd: kb1.Round, history: dict | None = None) -> None:
        pick = decide(rnd, history)
        self.picks[rnd.rid] = pick
        # The room sees a commitment, never the pick. Bound to our own DID, so a
        # copied commitment is useless to whoever copied it.
        self._say("bet", rnd.rid, {"c": kb1.commitment(pick, rnd.rid, self.cli.identity.did)})
        print(f"[{self.label}] {rnd.rid} sealed  ({pick})")

    def reveal(self, rnd: kb1.Round) -> None:
        pick = self.picks.get(rnd.rid)
        if pick is None:
            return
        self._say("reveal", rnd.rid, {"p": pick})
        self.revealed.add(rnd.rid)
        print(f"[{self.label}] {rnd.rid} revealed {pick}")

    def verify_board(self, ns: str = "kolbet", key: str = "board") -> None:
        """Don't trust the board — recompute it, then check the host's signature."""
        raw = self.cli.note(ns, key)
        rec = kb1.read_record(raw or "", ns, key, self.host)
        if rec is None:
            print(f"[{self.label}] board did not verify — treating as absent")
            return
        version, rows = rec
        msgs = self.cli.read(self.room, since=0, limit=200).get("messages", [])
        mine = kb1.score(kb1.collect(msgs, self.room, self.host))
        agree = all(
            mine.get(r["did"], {}).get("points", -1) == r["points"] for r in rows
        )
        print(f"[{self.label}] board v{version} signature OK, "
              f"recomputed from the log: {'MATCHES' if agree else 'DISAGREES'}")

    def loop(self, once: bool = False, poll: float = 0.0) -> None:
        print(f"[{self.label}] watching /r/{self.room} as {self.cli.identity.did}")
        done, backoff = set(), 0.0
        log = kb1.Log(self.cli, self.room, self.host)
        while True:
            try:
                log.poll(wait=10)          # blocks until something new lands
                backoff = 0.0
            except TechnocoreError as exc:
                backoff = min(30.0, (backoff or 1.0) * 2)
                print(f"[{self.label}] {exc}", file=sys.stderr); time.sleep(backoff); continue
            rounds = log.rounds()
            for rnd in sorted(rounds.values(), key=lambda r: r.open_seq):
                me = self.cli.identity.did
                if rnd.close_seq is None and me not in rnd.bets:
                    # Skip rounds settled more weakly than we are willing to accept.
                    # Refusing to play a [host] round is a position, and the board
                    # shows it: VER% is the share of points you did not have to
                    # take anyone's word for.
                    if kb1.TRUST.get(rnd.trust, 0) < self.min_trust:
                        if rnd.rid not in done:
                            done.add(rnd.rid)
                            print(f"[{self.label}] {rnd.rid} skipped — settles "
                                  f"[{rnd.trust}], below my floor")
                        continue
                    self.bet(rnd, rounds)
                elif rnd.close_seq is not None and me in rnd.bets \
                        and rnd.rid not in self.revealed and me not in rnd.reveals:
                    self.reveal(rnd)
                if rnd.settled and rnd.rid not in done:
                    done.add(rnd.rid)
                    got = self.picks.get(rnd.rid)
                    if rnd.numeric:
                        a, b = kb1.as_number(got), kb1.as_number(rnd.winner)
                        off = f"off by {abs(a - b):.2f}" if a is not None and b is not None else "no pick"
                        print(f"[{self.label}] {rnd.rid} guessed {got}, landed {rnd.winner} — {off}")
                    else:
                        mark = "WON " if kb1.norm(got or "") == rnd.winner else "lost"
                        print(f"[{self.label}] {rnd.rid} {mark} — winner {rnd.winner} ({rnd.evidence})")
                    if once:
                        return
            if poll:
                time.sleep(poll)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--room", required=True)
    p.add_argument("--host", required=True, help="the host's did:key")
    p.add_argument("--base", default=os.environ.get("TECHNOCORE_URL", "https://technocore.chat"))
    p.add_argument("--key", default="agent.json")
    p.add_argument("--label", default="")
    p.add_argument("--min-trust", choices=sorted(kb1.TRUST, key=kb1.TRUST.get),
                   default="host",
                   help="refuse rounds settled more weakly than this")
    p.add_argument("--once", action="store_true")
    p.add_argument("--verify", action="store_true", help="check the board and exit")
    a = p.parse_args()

    agent = Agent(a.base, a.room, a.host, a.key, a.label, a.min_trust)
    if a.verify:
        agent.verify_board()
        return
    agent.loop(once=a.once)


if __name__ == "__main__":
    main()
