"""Nonces must strictly increase per key — across writes AND across restarts.

The bug this guards: nonces were seeded from the wall clock on every construction
and never persisted. Writes issued inside one millisecond push the counter past
the clock, so a process that restarts re-seeds BELOW what the server already
recorded for that key, and every signed write is refused until real time catches
up. It only ever shows up after a crash, which is the worst time to find it.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from technocore import Client, Identity, NONCE_RESERVE

FAILS = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILS.append(label)


tmp = tempfile.mkdtemp(prefix="freezetime-nonce-")
keyfile = os.path.join(tmp, "host.json")
ident = Identity.load_or_create(keyfile)

print("identity remembers its own path")
check("Identity.path is set on create", ident.path == keyfile)
check("Identity.path survives a reload", Identity.load_or_create(keyfile).path == keyfile)

print("\nburn enough nonces to outrun the wall clock")
c1 = Client(base="http://127.0.0.1:1", identity=ident)
burned = [int(c1.nonce()) for _ in range(5000)]
clock_now = int(time.time() * 1000)
check("nonces are strictly increasing in-process",
      all(b < a for b, a in zip(burned, burned[1:])))
check("the counter really did outrun the clock (bug precondition)",
      burned[-1] > clock_now)
print(f"        last nonce {burned[-1]} vs clock {clock_now} "
      f"(+{burned[-1] - clock_now} ms ahead)")

print("\nrestart: a fresh Client on the same identity")
c2 = Client(base="http://127.0.0.1:1", identity=Identity.load_or_create(keyfile))
first_after = int(c2.nonce())
check("first nonce after restart is ABOVE every nonce before it",
      first_after > burned[-1])
check("no nonce is ever reused", first_after not in set(burned))
print(f"        resumed at {first_after}, previous max {burned[-1]}")

print("\nthe ceiling is written before it is spent")
ceiling = int(open(keyfile + ".nonce").read().strip())
check("nonce file exists and holds a ceiling", ceiling > 0)
check("ceiling is at or above the highest nonce issued", ceiling >= first_after)
check("ceiling reserves ahead rather than writing per call",
      ceiling - first_after <= NONCE_RESERVE)

print("\na crash mid-block skips nonces, never reuses them")
c3 = Client(base="http://127.0.0.1:1", identity=Identity.load_or_create(keyfile))
check("post-crash client starts above the persisted ceiling",
      int(c3.nonce()) >= ceiling)

print("\ndegraded cases")
open(keyfile + ".nonce", "w").write("not a number")
c4 = Client(base="http://127.0.0.1:1", identity=Identity.load_or_create(keyfile))
check("a corrupt nonce file falls back to the clock instead of crashing",
      int(c4.nonce()) > 0)

anon = Client(base="http://127.0.0.1:1", identity=Identity(os.urandom(32)))
a1, a2 = int(anon.nonce()), int(anon.nonce())
check("an in-memory identity still increments, and writes no file", a2 > a1)

print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
