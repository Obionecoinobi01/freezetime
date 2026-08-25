"""feed.json apply/inc/reset — the CoD desk path, no server required."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import feed

TMP = tempfile.mkdtemp(prefix="freezetime-feed-")
feed.FEED = os.path.join(TMP, "feed.json")
FAILS = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILS.append(label)


print("\nfeed apply")
cur = feed.apply({"kills": 4, "deaths": 2, "state": "live"}, "cod-desk")
check("kills set", cur["kills"] == 4)
check("kd is 2.0", cur["kd"] == 2.0)
check("state live", cur["state"] == "live")
check("source tagged", cur["source"] == "cod-desk")

cur = feed.apply({"inc": {"kills": 1, "deaths": 1}}, "cod-desk")
check("inc kills", cur["kills"] == 5)
check("inc deaths", cur["deaths"] == 3)
check("kd recalculated", cur["kd"] == round(5 / 3, 4))

cur = feed.apply({"inc": {"kills": -100}}, "cod-desk")
check("kills cannot go negative", cur["kills"] == 0)

cur = feed.apply({"reset": True}, "cod-desk")
check("reset zeros kills", cur["kills"] == 0)
check("reset zeros deaths", cur["deaths"] == 0)
check("reset returns to lobby", cur["state"] == "lobby")
check("flawless kd convention is kill count",
      feed.kd(7, 0) == 7.0)

check("cod profile exists", "cod" in feed.PROFILES)

print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
