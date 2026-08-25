"""End-to-end proof against a real technocore server, including the attacks."""
import os, sys, json, time, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import kb1
from technocore import Client, Identity

BASE = os.environ.get("TECHNOCORE_URL", "http://127.0.0.1:8799")
TMP = tempfile.mkdtemp(prefix="freezetime-test-")
ROOM = "p-kb1-test-" + os.urandom(3).hex()
os.environ["TECHNOCORE_URL"] = BASE
FAILS = []

def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond: FAILS.append(label)

host = Identity.load_or_create(os.path.join(TMP, "t_host.json"))
hcli = Client(base=BASE, identity=host)

def hsay(verb, rid, fields):
    sig = host.sign(kb1.Line(verb=verb, rid=rid, fields=fields)._payload(ROOM))
    hcli.say(ROOM, kb1.render(verb, rid, fields, sig))

class A:
    def __init__(self, n):
        self.id = Identity.load_or_create(os.path.join(TMP, f"t_a{n}.json"))
        self.cli = Client(base=BASE, identity=self.id)
    def say(self, verb, rid, fields, sig=None):
        s = sig or self.id.sign(kb1.Line(verb=verb, rid=rid, fields=fields)._payload(ROOM))
        self.cli.say(ROOM, kb1.render(verb, rid, fields, s))
    def bet(self, rid, pick):
        self.say("bet", rid, {"c": kb1.commitment(pick, rid, self.id.did)})
    def reveal(self, rid, pick):
        self.say("reveal", rid, {"p": pick})

a1, a2, a3, a4, imp = A(1), A(2), A(3), A(4), A(9)

print(f"\nroom {ROOM}\nhost {host.short}\n")
print("ROUND R1 — three honest bettors, one late, one liar, one impostor host")
hsay("open", "R1", {"q": "does it tag 30k?", "o": "yes,no", "res": "manual", "t": "api"})
a1.bet("R1", "yes")            # correct, earliest
a2.bet("R1", "yes")            # correct, second
a3.bet("R1", "no")             # wrong
a4.bet("R1", "yes")            # will reveal a DIFFERENT pick than committed

# an impostor tries to open a round of their own
imp.say("open", "Z9", {"q": "impostor round", "o": "yes,no", "res": "manual"})
# and tries to post a bet with a garbage detached signature
a1.say("bet", "R1", {"c": "0" * 64}, sig="A" * 86)

last = hcli.read(ROOM, since=0, limit=1)["last_seq"]
hsay("close", "R1", {"at": str(last)})

late = A(5)
late.bet("R1", "yes")          # provably late: its seq is above the close

a1.reveal("R1", "yes")
a2.reveal("R1", "yes")
a3.reveal("R1", "no")
a4.reveal("R1", "no")          # does not match its commitment of "yes"
late.reveal("R1", "yes")
hsay("result", "R1", {"w": "yes", "ev": "mcap=31200@22:40:00Z"})

msgs = hcli.read(ROOM, since=0, limit=200)["messages"]
rounds = kb1.collect(msgs, ROOM, host.did)
table = kb1.score(rounds)
r1 = rounds["R1"]

print("\nassertions")
check("impostor's round is not a round", "Z9" not in rounds)
check("round rebuilt with 5 sealed bets", len(r1.bets) == 5)
check("forged detached signature dropped (a1 commit unchanged)",
      r1.bets[a1.id.did][1] == kb1.commitment("yes", "R1", a1.id.did))
check("winner recorded", r1.winner == "yes")
check("a1 scored", table.get(a1.id.did, {}).get("wins") == 1)
check("a2 scored", table.get(a2.id.did, {}).get("wins") == 1)
check("a3 wrong pick scores nothing", table.get(a3.id.did, {}).get("wins") == 0)
check("a4 reveal/commit mismatch scores nothing", table.get(a4.id.did, {}).get("wins") == 0)
check("a4 still counted as played", table.get(a4.id.did, {}).get("played") == 1)
check("late bet earns nothing", table.get(late.id.did, {}).get("wins", 0) == 0)
check("late bet is not even counted as played",
      table.get(late.id.did, {}).get("played", 0) == 0)
check("speed bonus: a1 (earliest correct) beats a2",
      table[a1.id.did]["points"] > table[a2.id.did]["points"])
check("a1 = 10 base + 5 speed", table[a1.id.did]["points"] == 15)
check("a2 = 10 base + 3 speed", table[a2.id.did]["points"] == 13)

print("\nROUNDS R2, R3 — streak doubling at 3")
for rid in ("R2", "R3"):
    hsay("open", rid, {"q": "again?", "o": "yes,no", "res": "manual", "t": "api"})
    a1.bet(rid, "yes"); a3.bet(rid, "no")
    last = hcli.read(ROOM, since=0, limit=1)["last_seq"]
    hsay("close", rid, {"at": str(last)})
    a1.reveal(rid, "yes"); a3.reveal(rid, "no")
    hsay("result", rid, {"w": "yes", "ev": "-"})

msgs = hcli.read(ROOM, since=0, limit=200)["messages"]
rounds = kb1.collect(msgs, ROOM, host.did)
table = kb1.score(rounds)
check("a1 streak is 3", table[a1.id.did]["streak"] == 3)
check("third win doubled the base (15+15+25=55)", table[a1.id.did]["points"] == 55)
check("a3 hit rate is 0%", table[a3.id.did]["rate"] == 0)

print("\nrecord")
rows = kb1.standings(table, top=10)
ns, key = "kolbet", "board" + os.urandom(2).hex()
v1 = kb1.make_record(host, ns, key, 1, kb1.record_rows(rows))
hcli.set_note(ns, key, v1)
back = kb1.read_record(hcli.note(ns, key), ns, key, host.did)
check("record verifies against the host key", back is not None and back[0] == 1)
check("record rows survive the round trip", back[1][0]["points"] == rows[0]["points"])
check("record signed by someone else is rejected",
      kb1.read_record(v1, ns, key, a1.id.did) is None)
check("record lifted to a different note address is rejected",
      kb1.read_record(v1, ns, "somewhere-else", host.did) is None)
tampered = v1.rsplit(" ", 1)[0] + " b|" + a1.id.did + "|9999|9|9|9"
check("tampered rows fail verification", kb1.read_record(tampered, ns, key, host.did) is None)

print("\nreplay")
v2 = kb1.make_record(host, ns, key, 2, kb1.record_rows(rows))
seen = max([kb1.read_record(x, ns, key, host.did) for x in (v1, v2)], key=lambda r: r[0])
check("highest version that verifies wins", seen[0] == 2)

print("\nROUND N1 — closest to the number (a K/D round)")
hsay("open", "N1", {"q": "what K/D does he finish on?", "o": kb1.NUMERIC,
                    "res": "feed:kd", "t": "frame"})
a1.bet("N1", "1.5")     # actual 1.5556 -> off by 0.0556  -> 1st
a2.bet("N1", "1.6")     # off by 0.0444 ... actually NEARER than a1
a3.bet("N1", "0.8")     # off by 0.7556 -> 3rd
a4.bet("N1", "3.0")     # off by 1.4444 -> 4th, out of the points
imp.bet("N1", "banana") # a word pick in a numeric round
last = hcli.read(ROOM, since=0, limit=1)["last_seq"]
hsay("close", "N1", {"at": str(last)})
for who, pick in ((a1,"1.5"), (a2,"1.6"), (a3,"0.8"), (a4,"3.0"), (imp,"banana")):
    who.reveal("N1", pick)
hsay("result", "N1", {"w": "1.5556", "ev": "kd=1.5556 match=de_dust2 src=manual"})

msgs = hcli.read(ROOM, since=0, limit=200)["messages"]
rounds = kb1.collect(msgs, ROOM, host.did)
n1 = rounds["N1"]
t2 = kb1.score({"N1": n1})

check("numeric round is detected as numeric", n1.numeric)
check("1.5 and 1.50 commit to the same hash",
      kb1.commitment("1.5", "N1", a1.id.did) == kb1.commitment("1.50", "N1", a1.id.did))
check("nearest (a2, off 0.044) takes 1st = 12", t2[a2.id.did]["points"] == 12)
check("second nearest (a1, off 0.056) takes 8", t2[a1.id.did]["points"] == 8)
check("third (a3, off 0.756) takes 5", t2[a3.id.did]["points"] == 5)
check("fourth (a4) scores nothing", t2[a4.id.did]["points"] == 0)
check("fourth still counted as played", t2[a4.id.did]["played"] == 1)
check("a word pick in a numeric round scores nothing",
      t2.get(imp.id.did, {}).get("points", 0) == 0)
check("top three all count as a win for streak purposes",
      all(t2[x.id.did]["wins"] == 1 for x in (a1, a2, a3)))

print("\nexact hit")
hsay("open", "N2", {"q": "again", "o": kb1.NUMERIC, "res": "feed:kd", "t": "frame"})
a1.bet("N2", "2.0"); a2.bet("N2", "1.0")
last = hcli.read(ROOM, since=0, limit=1)["last_seq"]
hsay("close", "N2", {"at": str(last)})
a1.reveal("N2", "2.0"); a2.reveal("N2", "1.0")
hsay("result", "N2", {"w": "2.0", "ev": "kd=2.0"})
msgs = hcli.read(ROOM, since=0, limit=200)["messages"]
t3 = kb1.score({"N2": kb1.collect(msgs, ROOM, host.did)["N2"]})
check("exact hit gets 12 + 5 bonus", t3[a1.id.did]["points"] == 17)
check("runner-up gets 8, no bonus", t3[a2.id.did]["points"] == 8)

print("\nTRUST TIERS")
# unlabelled -> host, the weakest, on purpose
hsay("open", "T1", {"q": "unlabelled", "o": "yes,no", "res": "manual"})
# a tier nobody has heard of is not a round at all
hsay("open", "T2", {"q": "bogus tier", "o": "yes,no", "res": "manual", "t": "platinum"})
# an impostor cannot upgrade someone else's round
hsay("open", "T3", {"q": "chain round", "o": "yes,no", "res": "manual", "t": "chain"})
a1.bet("T1", "yes"); a1.bet("T3", "yes")
last = hcli.read(ROOM, since=0, limit=1)["last_seq"]
hsay("close", "T1", {"at": str(last)}); hsay("close", "T3", {"at": str(last)})
a1.reveal("T1", "yes"); a1.reveal("T3", "yes")
hsay("result", "T1", {"w": "yes", "ev": "-", "f": "-"})
hsay("result", "T3", {"w": "yes", "ev": "-", "f": "vod@1:42:07"})

msgs = hcli.read(ROOM, since=0, limit=200)["messages"]
rounds = kb1.collect(msgs, ROOM, host.did)
check("an unlabelled round defaults to the weakest tier",
      rounds["T1"].trust == "host")
check("an unknown tier is rejected outright", "T2" not in rounds)
check("a declared tier survives the round trip", rounds["T3"].trust == "chain")
check("frame evidence is carried on the result", rounds["T3"].frame == "vod@1:42:07")

t4 = kb1.score({"T1": rounds["T1"], "T3": rounds["T3"]})
r = t4[a1.id.did]
check("points from a host round do not count as verified", r["points"] > r["verified"])
check("points from a chain round do count as verified", r["verified"] > 0)
check("VER% is the verified share", r["ver"] == round(100 * r["verified"] / r["points"]))

print("\ntampering with the tier")
# take a real signed open line and try to pass it off as a stronger tier
orig = kb1.Line(verb="open", rid="T9", fields={"q": "x", "o": "yes,no",
                                               "res": "manual", "t": "host"})
sig = host.sign(orig._payload(ROOM))
forged = kb1.Line(verb="open", rid="T9", fields={"q": "x", "o": "yes,no",
                                                 "res": "manual", "t": "chain"},
                  sig=sig, did=host.did)
check("upgrading the tier breaks the host's signature", not forged.signed_ok(ROOM))

print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
