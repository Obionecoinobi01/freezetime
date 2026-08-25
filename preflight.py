#!/usr/bin/env python3
"""
preflight — check kb1 actually works on YOUR machine against the LIVE server.

Everything in this project was built and tested against a local copy of
technocore, because the live server's robots.txt puts /r/ and /kv/ off limits to
automated fetchers. That means it has never run against the real thing. This is
the script that finds out, and it is the difference between "the tests pass" and
"this works tonight".

    python3 preflight.py                      # dry checks only
    python3 preflight.py --room p-kb1-check   # also writes to a scratch room
    python3 preflight.py --room ca-<mint> --live   # your real room

Nothing here writes to your real room unless you name it.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

OK, BAD, WARN, SKIP = "  ok  ", " FAIL ", " warn ", " skip "
issues, warnings = [], []


def say(mark: str, label: str, detail: str = "") -> None:
    print(f"[{mark}] {label}" + (f"\n         {detail}" if detail else ""))
    if mark is BAD:
        issues.append(label)
    if mark is WARN:
        warnings.append(label)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("TECHNOCORE_URL", "https://technocore.chat"))
    ap.add_argument("--room", help="scratch room to write to. Omit for read-only checks")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--mint", default="CXXpHyiwAzuwwxD9aGJCA3L6gjjJ4wMXoGYYjczKpump",
                    help="a mint to test the dex resolver against")
    ap.add_argument("--live", action="store_true",
                    help="acknowledge you are writing to a room you care about")
    a = ap.parse_args()

    print(f"\nkb1 preflight — {a.base}\n" + "-" * 58)

    # 1. dependencies -------------------------------------------------
    try:
        import cryptography  # noqa: F401
        say(OK, "cryptography installed")
    except ImportError:
        say(BAD, "cryptography missing", "pip install cryptography")
        return

    import kb1
    from technocore import Client, Identity, TechnocoreError
    say(OK, "kb1 modules import")

    # 2. identity -----------------------------------------------------
    idfile = os.path.join(HERE, "host.json")
    fresh = not os.path.exists(idfile)
    ident = Identity.load_or_create(idfile)
    say(OK, f"identity {'minted' if fresh else 'loaded'}", f"{ident.did}\n         shown as <{ident.short}>")
    if fresh:
        say(WARN, "this key is brand new — back up host.json",
            "there is no recovery; losing it loses your host identity")
    mode = oct(os.stat(idfile).st_mode & 0o777)
    if mode != "0o600":
        say(WARN, f"host.json permissions are {mode}", "expected 0o600")

    # 3. can we reach the service at all ------------------------------
    cli = Client(base=a.base, identity=ident)
    try:
        t0 = time.time()
        body = cli._get("/healthz")
        say(OK, f"service reachable ({round((time.time()-t0)*1000)} ms)", body.strip()[:60])
    except Exception as exc:
        say(BAD, "cannot reach the service", str(exc)[:160])
        return

    # 4. does the live instance agree with our assumptions ------------
    try:
        manifest = json.loads(cli._get("/.well-known/agent.json"))
        lim = manifest.get("limits", {})
        say(OK, f"manifest v{manifest.get('version')}",
            f"reads {lim.get('reads_per_minute_per_ip')}/min · "
            f"writes {lim.get('writes_per_minute_per_ip')}/min · "
            f"rooms/day {lim.get('new_rooms_per_day_per_ip')}")
        ident_spec = manifest.get("identity", {})
        want = "<room>|<nonce>|<text>"
        got = ident_spec.get("message_signature_payload")
        if got == want:
            say(OK, "signing payload matches what this client builds", got)
        else:
            say(BAD, "SIGNING PAYLOAD HAS CHANGED",
                f"server says {got!r}, this client signs {want!r} — stop and fix kb1")
        retention = lim.get("retention_seconds")
        if retention:
            say(OK, f"room retention {int(retention)//86400} days",
                "a room with no traffic for that long is deleted")
    except Exception as exc:
        say(WARN, "could not read the manifest", str(exc)[:120])

    # 5. the local board port ----------------------------------------
    s = socket.socket()
    s.settimeout(1)
    busy = s.connect_ex(("127.0.0.1", a.port)) == 0
    s.close()
    say(WARN if busy else OK, f"board port {a.port} {'in use' if busy else 'free'}",
        "something is already listening — ringmaster serve will fail" if busy else "")

    # 6. the dex resolver against the real API ------------------------
    try:
        import ringmaster
        winner, evidence = ringmaster.resolve(f"dex:{a.mint}:mcap:gte:1")
        if winner:
            say(OK, "dex resolver reaches DexScreener", evidence)
        else:
            say(WARN, "dex resolver returned nothing", evidence)
    except Exception as exc:
        say(WARN, "dex resolver failed", str(exc)[:140])

    # 7. a real signed write, end to end ------------------------------
    if not a.room:
        say(SKIP, "signed write not tested", "pass --room <scratch room> to test it")
    else:
        if not a.room.startswith(("p-", "e-")) and not a.live:
            say(BAD, f"{a.room} is not a scratch room",
                "use a p- or e- name, or pass --live if you mean it")
        else:
            probe = f"kb1 preflight {int(time.time())}"
            try:
                cli.say(a.room, probe)
                say(OK, "signed write accepted by the live server")
                view = cli.read(a.room, since=0, limit=20)
                mine = [m for m in view.get("messages", [])
                        if m.get("text") == probe and m.get("from") == ident.did]
                if mine:
                    say(OK, "write read back, attributed to our DID",
                        f"seq {mine[-1]['seq']} — signing works against the real service")
                else:
                    say(BAD, "write did not come back attributed to us",
                        "the server took it but did not credit the key")
            except TechnocoreError as exc:
                say(BAD, "signed write rejected", str(exc)[:200])

            # long-poll, the thing that gets you rate limited if done wrong
            try:
                t0 = time.time()
                cli.read(a.room, since=10**9, limit=1, wait=3)
                waited = time.time() - t0
                if waited < 1.0:
                    say(WARN, f"long-poll returned in {waited:.1f}s",
                        "waiter slots are capped at 4 per IP — an instant empty reply "
                        "means no slot, so poll normally rather than tight-looping")
                else:
                    say(OK, f"long-poll blocked for {waited:.1f}s as expected")
            except Exception as exc:
                say(WARN, "long-poll check failed", str(exc)[:120])

    # 8. what is still on you ----------------------------------------
    print("-" * 58)
    if issues:
        print(f"\n{len(issues)} BLOCKER(S): " + "; ".join(issues))
    elif warnings:
        print(f"\nno blockers, {len(warnings)} warning(s)")
    else:
        print("\nall checks passed")

    print("""
Still manual, and no script can do these for you:

  1. Keep the room alive. A room created without a reply is reaped in 24h,
     and any room idle for 7 days is deleted. Post in it the day you make it.
  2. Patch YOUR overlay, not the bundled snapshot:
       python3 overlay_patch.py <your real overlay>.html -o overlay-live.html
     Then load it in OBS and confirm the browser source reads 127.0.0.1.
  3. Decide the game and wire a resolver. Until then every gameplay round
     is trust tier [host], which is your word and nothing more.
  4. Get other people's agents in. One agent is not a leaderboard. Publishing
     PROTOCOL.md is what makes that possible.
  5. Sybil is unsolved. did:key is free. Decide whether you care before the
     board matters to anyone.
""")


if __name__ == "__main__":
    main()
