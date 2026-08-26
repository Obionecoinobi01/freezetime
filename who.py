#!/usr/bin/env python3
"""
who — who has actually turned up.

`ringmaster.py audit` shows standings, but standings only list agents that
scored. This lists every DID that has ever put a signed line in your room,
whether it won anything or not, and in --watch mode announces each new one the
moment it commits a pick.

    python who.py             # roster, then exit
    python who.py --watch     # roster, then announce newcomers as they bet

An agent that only reads is invisible here, and that is not a bug: technocore
logs writes, not readers. A DID on this list means somebody ran real code,
held a real key, and signed a real commitment.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kb1
import ringmaster as rm


def short(did: str) -> str:
    return f"{did[8:14]}…{did[-4:]}"


def roster(rounds: dict, host: str) -> dict:
    who: dict[str, dict] = {}
    for rid, rnd in rounds.items():
        for did, (seq, _commit) in rnd.bets.items():
            if did == host:
                continue
            w = who.setdefault(did, {"first": rid, "first_seq": seq,
                                     "bets": 0, "reveals": 0, "last": rid})
            w["bets"] += 1
            w["last"] = rid
            if did in rnd.reveals:
                w["reveals"] += 1
    return who


def show(who: dict) -> None:
    if not who:
        print("nobody yet — no agent has committed a pick in this room.")
        print("that includes agents quietly watching; reads leave no trace.")
        return
    print(f"{len(who)} agent(s) have bet in this room:\n")
    print(f"  {'agent':<16}{'bets':>5}{'revealed':>10}   first    last")
    for did, w in sorted(who.items(), key=lambda kv: kv[1]["first_seq"]):
        flag = "" if w["reveals"] == w["bets"] else "   <- sealed picks never revealed"
        print(f"  {short(did):<16}{w['bets']:>5}{w['reveals']:>10}   "
              f"{w['first']:<8}{w['last']}{flag}")
    print("\nfull DIDs:")
    for did in sorted(who, key=lambda d: who[d]["first_seq"]):
        print(f"  {did}")


def main() -> None:
    p = argparse.ArgumentParser(description="who has bet in your room")
    p.add_argument("--watch", action="store_true",
                   help="keep running and announce each new agent")
    p.add_argument("--every", type=int, default=10,
                   help="long-poll seconds between checks (default 10)")
    a = p.parse_args()

    c = rm.cfg()
    if not c["room"]:
        sys.exit("no room configured — run: ringmaster.py init --room <name>")
    cli = rm.client(c)
    host = cli.identity.did
    log = kb1.Log(cli, c["room"], host, path=rm.log_path(c["room"]))

    log.poll(wait=0)
    who = roster(log.rounds(), host)
    print(f"room {c['room']}  host {short(host)}\n")
    if log.gap:
        print("LOG GAP — the room was compacted; this roster may be incomplete.\n",
              file=sys.stderr)
    show(who)
    if not a.watch:
        return

    print(f"\nwatching. new agents will be announced here. ctrl-c to stop.")
    seen = set(who)
    while True:
        try:
            log.poll(wait=a.every)
        except KeyboardInterrupt:
            print("\nstopped.")
            return
        except Exception as exc:                      # noqa: BLE001
            print(f"  poll failed ({exc}) — retrying", file=sys.stderr)
            time.sleep(a.every)
            continue
        fresh = roster(log.rounds(), host)
        for did, w in sorted(fresh.items(), key=lambda kv: kv[1]["first_seq"]):
            if did in seen:
                continue
            seen.add(did)
            print(f"\n  NEW AGENT  {short(did)}  first bet on {w['first']}")
            print(f"             {did}")


if __name__ == "__main__":
    main()
