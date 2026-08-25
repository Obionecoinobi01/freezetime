"""Offline protocol and state tests. No server required."""
import os, sys, json, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import kb1
from technocore import Identity

TMP = tempfile.mkdtemp(prefix="freezetime-kb1-")
ROOM = "p-kb1-offline"
FAILS = []


def check(label, cond):
    print(("  PASS  " if cond else "  FAIL  ") + label)
    if not cond:
        FAILS.append(label)


host = Identity.load_or_create(os.path.join(TMP, "host.json"))
a1 = Identity.load_or_create(os.path.join(TMP, "a1.json"))
a2 = Identity.load_or_create(os.path.join(TMP, "a2.json"))
imp = Identity.load_or_create(os.path.join(TMP, "imp.json"))


def msg(ident, verb, rid, fields, seq):
    line = kb1.Line(verb=verb, rid=rid, fields=fields)
    sig = ident.sign(line._payload(ROOM))
    return {"seq": seq, "from": ident.did, "text": kb1.render(verb, rid, fields, sig)}


print("\nnorm / as_number")
check("1.5 and 1.50 normalise together", kb1.norm("1.5") == kb1.norm("1.50") == "1.5000")
check("Yes / yes / YES are one pick", kb1.norm("Yes") == kb1.norm(" yes ") == "yes")
check("inf is not a number", kb1.as_number("inf") is None)
check("nan is not a number", kb1.as_number("nan") is None)
check("inf hashes as a word, not a float", kb1.norm("inf") == "inf")
check("-1.25 is finite", kb1.as_number("-1.25") == -1.25)


print("\nfirst open is binding")
log = [
    msg(host, "open", "R1", {"q": "first", "o": "yes,no", "res": "manual", "t": "api"}, 1),
    msg(a1, "bet", "R1", {"c": kb1.commitment("yes", "R1", a1.did)}, 2),
    msg(host, "open", "R1", {"q": "rug", "o": "yes,no", "res": "manual", "t": "chain"}, 3),
]
rounds = kb1.collect(log, ROOM, host.did)
check("question is the first open's", rounds["R1"].question == "first")
check("trust is the first open's", rounds["R1"].trust == "api")
check("bets survived the second open", a1.did in rounds["R1"].bets)


print("\nimpostor / forged / host-as-bettor")
log = [
    msg(imp, "open", "Z9", {"q": "impostor", "o": "yes,no", "res": "manual", "t": "api"}, 1),
    msg(host, "open", "R2", {"q": "real", "o": "yes,no", "res": "manual", "t": "host"}, 2),
    msg(host, "bet", "R2", {"c": kb1.commitment("yes", "R2", host.did)}, 3),
    msg(a1, "bet", "R2", {"c": kb1.commitment("yes", "R2", a1.did)}, 4),
]
bogus = msg(a1, "bet", "R2", {"c": "0" * 64}, 5)
bogus["text"] = kb1.render("bet", "R2", {"c": "0" * 64}, "A" * 86)
log.append(bogus)
rounds = kb1.collect(log, ROOM, host.did)
check("impostor open is not a round", "Z9" not in rounds)
check("host bet is ignored", host.did not in rounds["R2"].bets)
check("forged sig dropped; a1 commit unchanged",
      rounds["R2"].bets[a1.did][1] == kb1.commitment("yes", "R2", a1.did))
check("unknown tier is not a round",
      "T0" not in kb1.collect(
          [msg(host, "open", "T0", {"q": "x", "o": "yes,no", "res": "manual", "t": "platinum"}, 1)],
          ROOM, host.did,
      ))


print("\nclose seq clamp")
# a1 bets at 2, close names the future at seq 3, a2 bets AFTER the close at 4.
log = [
    msg(host, "open", "C1", {"q": "clamp", "o": "yes,no", "res": "manual", "t": "api"}, 1),
    msg(a1, "bet", "C1", {"c": kb1.commitment("yes", "C1", a1.did)}, 2),
    msg(host, "close", "C1", {"at": "999999999"}, 3),
    msg(a2, "bet", "C1", {"c": kb1.commitment("yes", "C1", a2.did)}, 4),
    msg(a1, "reveal", "C1", {"p": "yes"}, 5),
    msg(a2, "reveal", "C1", {"p": "yes"}, 6),
    msg(host, "result", "C1", {"w": "yes", "ev": "-", "f": "-"}, 7),
]
c1 = kb1.collect(log, ROOM, host.did)["C1"]
check("close_seq is the close message seq, not 999999999", c1.close_seq == 3)
t = kb1.score({"C1": c1})
check("pre-close bet scores", t[a1.did]["wins"] == 1)
check("post-close bet is not played", t.get(a2.did, {}).get("played", 0) == 0)

# Host names an earlier cutoff on purpose: bet at seq 3 is after at=2.
log = [
    msg(host, "open", "C2", {"q": "early", "o": "yes,no", "res": "manual", "t": "api"}, 1),
    msg(a1, "bet", "C2", {"c": kb1.commitment("yes", "C2", a1.did)}, 2),
    msg(a2, "bet", "C2", {"c": kb1.commitment("yes", "C2", a2.did)}, 3),
    msg(host, "close", "C2", {"at": "2"}, 4),
    msg(a1, "reveal", "C2", {"p": "yes"}, 5),
    msg(a2, "reveal", "C2", {"p": "yes"}, 6),
    msg(host, "result", "C2", {"w": "yes", "ev": "-", "f": "-"}, 7),
]
c2 = kb1.collect(log, ROOM, host.did)["C2"]
check("named cutoff below the close message is honoured", c2.close_seq == 2)
t = kb1.score({"C2": c2})
check("bet at seq 2 is on time", t[a1.did]["played"] == 1)
check("bet at seq 3 is late when at=2", t.get(a2.did, {}).get("played", 0) == 0)

# First close sticks.
log = [
    msg(host, "open", "C3", {"q": "once", "o": "yes,no", "res": "manual", "t": "api"}, 1),
    msg(host, "close", "C3", {"at": "1"}, 2),
    msg(host, "close", "C3", {"at": "9"}, 3),
]
check("first close is binding", kb1.collect(log, ROOM, host.did)["C3"].close_seq == 1)


print("\nnumeric inf winner scores nobody")
log = [
    msg(host, "open", "N0", {"q": "kd", "o": kb1.NUMERIC, "res": "feed:kd", "t": "frame"}, 1),
    msg(a1, "bet", "N0", {"c": kb1.commitment("1.5", "N0", a1.did)}, 2),
    msg(host, "close", "N0", {"at": "2"}, 3),
    msg(a1, "reveal", "N0", {"p": "1.5"}, 4),
    msg(host, "result", "N0", {"w": "inf", "ev": "-", "f": "-"}, 5),
]
n0 = kb1.collect(log, ROOM, host.did)["N0"]
t = kb1.score({"N0": n0})
check("inf result is settled as a word, not a number", kb1.as_number(n0.winner) is None)
check("nobody scores on a non-numeric numeric-round winner", t[a1.did]["points"] == 0)


print("\nlog gap + persist")
class Fake:
    def __init__(self, views):
        self.views = list(views)
    def read(self, room, since=0, limit=200, wait=0):
        return self.views.pop(0)

path = os.path.join(TMP, "log.json")
fake = Fake([
    {"last_seq": 5, "first_seq": 1,
     "messages": [{"seq": 1, "from": "", "text": "a"}, {"seq": 5, "from": "", "text": "b"}]},
    {"last_seq": 20, "first_seq": 12,
     "messages": [{"seq": 12, "from": "", "text": "c"}]},
])
lg = kb1.Log(fake, ROOM, host.did, path=path)
lg.poll(wait=0)
check("contiguous first poll is not a gap", lg.gap is False)
lg.poll(wait=0)
check("a jump in first_seq sets gap", lg.gap is True)
lg2 = kb1.Log(Fake([]), ROOM, host.did, path=path)
check("gap survives a process restart", lg2.gap is True)
check("messages were persisted", len(lg2.messages) == 3)
check("seq high-water persisted", lg2.seq == 12)


print("\nstate.json merge (the persist() clobber)")
import ringmaster
saved = ringmaster.STATE
ringmaster.STATE = os.path.join(TMP, "state.json")
try:
    ringmaster.patch_state(deadlines={"R1": 1.0}, closeon={"R1": "live"}, resolveon={"R1": "over"})
    ringmaster.patch_state(deadlines={"R2": 2.0})
    st = ringmaster.load_state()
    check("closeon survived a deadlines-only write", st["closeon"]["R1"] == "live")
    check("resolveon survived a deadlines-only write", st["resolveon"]["R1"] == "over")
    check("R1 deadline kept while merging R2", st["deadlines"]["R1"] == 1.0)
    check("R2 deadline merged", st["deadlines"]["R2"] == 2.0)
    ringmaster.drop_round_state("R1")
    st = ringmaster.load_state()
    check("drop_round_state removes R1 closeon", "R1" not in st.get("closeon", {}))
    check("drop_round_state removes R1 deadline", "R1" not in st.get("deadlines", {}))
    check("drop_round_state keeps R2", st["deadlines"]["R2"] == 2.0)
finally:
    ringmaster.STATE = saved


print("\nagent picks survive a restart")
from agent import Agent
key = os.path.join(TMP, "ag.json")
one = Agent("http://127.0.0.1:9", ROOM, host.did, key)
one.picks["R9"] = "1.50"
one.revealed.add("R8")
one._save_picks()
two = Agent("http://127.0.0.1:9", ROOM, host.did, key)
check("pick reloaded from disk", two.picks.get("R9") == "1.50")
check("revealed set reloaded from disk", "R8" in two.revealed)
check("picks file sits next to the key", os.path.exists(key.replace(".json", ".picks.json")))


print("\n" + ("ALL PASS" if not FAILS else f"{len(FAILS)} FAILED: {FAILS}"))
sys.exit(1 if FAILS else 0)
