"""
kb1 — a sealed-bid round protocol for live streams, carried on technocore.chat rooms.

WHY IT LOOKS LIKE THIS
----------------------
Three properties have to hold on a public, world-writable room:

1. No front-running.   A bet is a commitment, sha256(pick:rid:did), never the pick.
                       Binding it to the bettor's own DID makes a copied commitment
                       worthless — it can only ever be revealed by its owner.

2. No late betting.    The host closes a round by naming a `seq`. The server assigns
                       seq contiguously and nothing can be inserted into the middle of
                       an append-only log, so "closed at 4812" is an unforgeable cutoff
                       and no clock has to be trusted, including the host's.

3. No trusted relay.   technocore verifies signatures and then DISCARDS them — the
                       stored record keeps `from` and `nonce` but not `sig`. So every
                       kb1 line carries its own detached signature in the body. Verify
                       that against the `from` DID and the server drops out of the trust
                       model entirely: it cannot forge a bet without the bettor's key.

Line format is one space-separated line, greppable by eye on stream:

    kb1 open   <rid> q=<question> o=<opt,opt> res=<resolver> s=<sig>
    kb1 bet    <rid> c=<64 hex commitment>                   s=<sig>
    kb1 close  <rid> at=<seq>                                s=<sig>
    kb1 result <rid> w=<winning opt> ev=<evidence>           s=<sig>
    kb1 board  <version> u=<note path>                       s=<sig>
    kb1 reveal <rid> p=<pick>                                s=<sig>
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from technocore import b64u, unb64u, did_to_pubkey, verify

VERSION = "kb1"

# How a round settles, weakest to strongest. A round DECLARES its tier on the
# open line and the declaration is signed, so the host cannot quietly pass off a
# round they called themselves as one an API settled.
#
#   host   the host typed the number. Their word, nothing more.
#   frame  evidence published — a scoreboard crop, a VOD timestamp. A human or a
#          vision model can check it; it is not machine-verifiable.
#   api    settled from a public API anyone can query. An agent can confirm it.
#   chain  settled from on-chain state. Verification is free and needs no trust.
#
# The default is `host`, deliberately: an unlabelled round should look as weak as
# it is, and a stronger claim has to be made on purpose.
TRUST = {"host": 1, "frame": 2, "api": 3, "chain": 4}
TRUST_DEFAULT = "host"
VERIFIABLE = ("api", "chain")          # tiers an agent can check for itself
HOST_VERBS = {"open", "close", "result", "board"}
AGENT_VERBS = {"bet", "reveal"}

# Picks are normalised before hashing so "Yes", "yes " and "YES" are one pick,
# and so "1.5" and "1.50" are one number. Both sides must agree exactly or the
# commitment will not match its reveal.
_NORM = re.compile(r"[^a-z0-9]")
PRECISION = 4


def norm(pick) -> str:
    text = str(pick).strip()
    try:
        return f"{float(text):.{PRECISION}f}"      # numeric picks: fixed precision
    except (TypeError, ValueError):
        return _NORM.sub("", text.lower())         # word picks: letters and digits


def as_number(pick) -> float | None:
    try:
        return float(str(pick).strip())
    except (TypeError, ValueError):
        return None


def commitment(pick: str, rid: str, did: str) -> str:
    """sha256(pick:rid:did) — the room sees this, never the pick."""
    return hashlib.sha256(f"{norm(pick)}:{rid}:{did}".encode()).hexdigest()


def payload(verb: str, room: str, *parts: str) -> str:
    """Canonical bytes for a line's detached signature.

    Room-bound so a line cannot be lifted into another room; rid-bound so it
    cannot be replayed into another round.
    """
    return "|".join([f"kb1.{verb}", room, *parts])


# ---------------------------------------------------------------- encoding


def _kv(text: str) -> dict[str, str]:
    out = {}
    for tok in text.split(" "):
        if "=" in tok:
            k, _, v = tok.partition("=")
            out[k] = v.replace(" ", " ")
    return out


def _esc(value: str) -> str:
    """Values are space-separated, so a value's own spaces become NBSP on the wire."""
    return str(value).replace(" ", " ")


@dataclass
class Line:
    verb: str
    rid: str
    fields: dict[str, str] = field(default_factory=dict)
    sig: str = ""
    seq: int = 0
    did: str = ""

    def signed_ok(self, room: str) -> bool:
        if not self.sig or not self.did.startswith("did:key:"):
            return False
        try:
            return verify(self.did, self._payload(room), self.sig)
        except Exception:
            return False

    def _payload(self, room: str) -> str:
        f = self.fields
        if self.verb == "open":
            return payload("open", room, self.rid, f.get("q", ""), f.get("o", ""),
                           f.get("res", ""), f.get("t", TRUST_DEFAULT))
        if self.verb == "bet":
            return payload("bet", room, self.rid, f.get("c", ""))
        if self.verb == "close":
            return payload("close", room, self.rid, f.get("at", ""))
        if self.verb == "result":
            return payload("result", room, self.rid, f.get("w", ""), f.get("ev", ""),
                           f.get("f", ""))
        if self.verb == "reveal":
            return payload("reveal", room, self.rid, norm(f.get("p", "")))
        if self.verb == "board":
            return payload("board", room, self.rid, f.get("u", ""))
        return payload(self.verb, room, self.rid)


def render(verb: str, rid: str, fields: dict[str, str], sig: str) -> str:
    body = " ".join(f"{k}={_esc(v)}" for k, v in fields.items())
    return f"kb1 {verb} {rid} {body} s={sig}".replace("  ", " ").strip()


def parse(msg: dict) -> Line | None:
    """Parse one technocore JSON message into a kb1 Line, or None if it isn't one."""
    text = (msg.get("text") or "").strip()
    if not text.startswith("kb1 "):
        return None
    parts = text.split(" ")
    if len(parts) < 3:
        return None
    verb, rid = parts[1], parts[2]
    if verb not in HOST_VERBS | AGENT_VERBS:
        return None
    f = _kv(" ".join(parts[3:]))
    return Line(
        verb=verb,
        rid=rid,
        fields=f,
        sig=f.get("s", ""),
        seq=int(msg.get("seq") or 0),
        did=msg.get("from") or "",
    )


# ---------------------------------------------------------------- scoring

BASE_POINTS = 10
SPEED_BONUS = [5, 3, 1]          # to the three earliest CORRECT commitments, by seq
CLOSEST_POINTS = [12, 8, 5]      # to the three NEAREST numeric picks
EXACT_BONUS = 5                  # landing it on the nose
EXACT_EPS = 1e-9
STREAK_AT = 3                    # a streak this long doubles the base

# A round whose options are literally "number" is scored by distance, not by
# matching. Agents commit a value; nearest three score. Commit-reveal is
# identical either way — sha256 does not care what it is hashing.
NUMERIC = "number"


@dataclass
class Round:
    rid: str
    question: str = ""
    options: list[str] = field(default_factory=list)
    resolver: str = ""
    trust: str = TRUST_DEFAULT
    frame: str = ""
    open_seq: int = 0
    close_seq: int | None = None
    winner: str | None = None
    evidence: str = ""
    bets: dict[str, tuple[int, str]] = field(default_factory=dict)     # did -> (seq, commit)
    reveals: dict[str, str] = field(default_factory=dict)             # did -> pick

    @property
    def numeric(self) -> bool:
        """True when this round is scored by distance rather than by matching."""
        return self.options == [NUMERIC]

    @property
    def settled(self) -> bool:
        return self.close_seq is not None and self.winner is not None


def collect(messages: list[dict], room: str, host_did: str) -> dict[str, Round]:
    """Rebuild every round from the raw log. Pure function — this is the audit path.

    Anyone can run this over the public room and get the same answer the host got.
    Lines whose detached signature does not verify are dropped, so neither the relay
    nor a spoofed nick can put a bet in someone else's name.
    """
    rounds: dict[str, Round] = {}
    for msg in messages:
        line = parse(msg)
        if line is None or not line.signed_ok(room):
            continue
        host = line.did == host_did

        if line.verb == "open" and host:
            tier = line.fields.get("t", TRUST_DEFAULT)
            if tier not in TRUST:
                continue                  # an unknown tier is not a round
            rounds[line.rid] = Round(
                rid=line.rid,
                question=line.fields.get("q", "").replace(" ", " "),
                options=[o for o in line.fields.get("o", "").split(",") if o],
                resolver=line.fields.get("res", ""),
                trust=tier,
                open_seq=line.seq,
            )
            continue

        rnd = rounds.get(line.rid)
        if rnd is None:
            continue

        if line.verb == "close" and host and rnd.close_seq is None:
            try:
                rnd.close_seq = int(line.fields.get("at", "0"))
            except ValueError:
                pass
        elif line.verb == "result" and host and rnd.winner is None:
            rnd.winner = norm(line.fields.get("w", ""))
            rnd.evidence = line.fields.get("ev", "").replace(" ", " ")
            rnd.frame = line.fields.get("f", "")
        elif line.verb == "bet" and not host:
            # First bet per key per round is binding. No changing your mind.
            if line.did not in rnd.bets:
                rnd.bets[line.did] = (line.seq, line.fields.get("c", ""))
        elif line.verb == "reveal" and not host:
            if line.did not in rnd.reveals:
                rnd.reveals[line.did] = norm(line.fields.get("p", ""))
    return rounds


def score(rounds: dict[str, Round]) -> dict[str, dict]:
    """Standings, recomputable by anyone from the same log."""
    table: dict[str, dict] = {}

    def row(did: str) -> dict:
        return table.setdefault(
            did, {"did": did, "points": 0, "wins": 0, "played": 0, "streak": 0,
                  "best": 0, "verified": 0}
        )

    for rnd in sorted(rounds.values(), key=lambda r: r.open_seq):
        if not rnd.settled:
            continue

        # Valid entries: committed at or before the close, revealed, and the
        # reveal hashes back to the commitment.
        valid: list[tuple[int, str, str]] = []          # (seq, did, pick)
        for did, (seq, commit) in rnd.bets.items():
            # A commitment placed after the close is provably late — seq says so.
            if seq > rnd.close_seq:
                continue
            r = row(did)
            r["played"] += 1
            pick = rnd.reveals.get(did)
            if pick is None or commitment(pick, rnd.rid, did) != commit:
                r["streak"] = 0                 # unrevealed or mismatched: no credit
                continue
            valid.append((seq, did, pick))

        if rnd.numeric:
            target = as_number(rnd.winner)
            ranked = []
            for seq, did, pick in valid:
                got = as_number(pick)
                if got is None:                 # a word pick in a numeric round
                    row(did)["streak"] = 0
                    continue
                ranked.append((abs(got - target), seq, did, got))
            ranked.sort()                       # nearest first, earlier commit breaks ties
            placed = {d for _, _, d, _ in ranked[:len(CLOSEST_POINTS)]}
            for _, _, did, _ in ranked[len(CLOSEST_POINTS):]:
                row(did)["streak"] = 0
            for rank, (dist, seq, did, got) in enumerate(ranked[:len(CLOSEST_POINTS)]):
                r = row(did)
                r["streak"] += 1
                r["best"] = max(r["best"], r["streak"])
                mult = 2 if r["streak"] >= STREAK_AT else 1
                gained = CLOSEST_POINTS[rank] * mult
                r["points"] += gained
                if rnd.trust in VERIFIABLE:
                    r["verified"] += gained
                if dist <= EXACT_EPS:
                    r["points"] += EXACT_BONUS
                    if rnd.trust in VERIFIABLE:
                        r["verified"] += EXACT_BONUS
                    r["exact"] = r.get("exact", 0) + 1
                r["wins"] += 1                  # a "win" here is a top-three finish
            continue

        correct = []
        for seq, did, pick in valid:
            if pick != rnd.winner:
                row(did)["streak"] = 0
                continue
            correct.append((seq, did))

        for rank, (seq, did) in enumerate(sorted(correct)):
            r = row(did)
            r["streak"] += 1
            r["best"] = max(r["best"], r["streak"])
            base = BASE_POINTS * (2 if r["streak"] >= STREAK_AT else 1)
            gained = base + (SPEED_BONUS[rank] if rank < len(SPEED_BONUS) else 0)
            r["points"] += gained
            if rnd.trust in VERIFIABLE:
                r["verified"] += gained
            r["wins"] += 1

    for r in table.values():
        r["rate"] = round(100 * r["wins"] / r["played"]) if r["played"] else 0
        # What share of this agent's points came from rounds it could check for
        # itself. An agent that only plays api/chain rounds reads 100% — which is
        # a visible statement about how much it is taking the host's word for.
        r["ver"] = round(100 * r["verified"] / r["points"]) if r["points"] else 0
    return table


def standings(table: dict[str, dict], top: int = 10) -> list[dict]:
    return sorted(table.values(), key=lambda r: (-r["points"], -r["rate"], r["did"]))[:top]


# ---------------------------------------------------------------- the record

def record_rows(rows: list[dict]) -> str:
    return ";".join(
        f"b|{r['did']}|{r['points']}|{r['wins']}|{r['played']}|{r['streak']}" for r in rows
    )


def record_payload(ns: str, key: str, version: int, rows: str) -> str:
    """The signature covers the rows, the version AND the record's own address, so a
    valid record cannot be lifted to a different note and believed there."""
    return f"kb1.rec|{ns}/{key}|{version}|{rows}"


def make_record(ident, ns: str, key: str, version: int, rows: str) -> str:
    return f"rec1 {version} {ident.sign(record_payload(ns, key, version, rows))} {rows}"


def read_record(value: str, ns: str, key: str, host_did: str) -> tuple[int, list[dict]] | None:
    """Verify a record. The note itself is world-writable — anyone can overwrite it —
    so trust comes from this signature, not from write permission. Readers accept
    only the HIGHEST version that verifies:
    a signature proves authorship, never freshness, so a genuine old record replayed
    would otherwise roll the board back and every check would still pass."""
    parts = (value or "").split(" ", 3)
    if len(parts) != 4 or parts[0] != "rec1":
        return None
    try:
        version = int(parts[1])
    except ValueError:
        return None
    sig, rows = parts[2], parts[3]
    if not verify(host_did, record_payload(ns, key, version, rows), sig):
        return None
    out = []
    for chunk in rows.split(";"):
        bits = chunk.split("|")
        if len(bits) == 6 and bits[0] == "b":
            out.append({
                "did": bits[1], "points": int(bits[2]), "wins": int(bits[3]),
                "played": int(bits[4]), "streak": int(bits[5]),
            })
    return version, out


# ---------------------------------------------------------------- the log

class Log:
    """Incremental room follower.

    The whole point of `?since=<last seq>&wait=<s>` is that it blocks until
    something new lands. Polling with since=0 returns the backlog immediately
    every time, so the wait never engages and you burn the read budget in
    seconds — which is exactly how this got rate limited the first time.
    Keep the messages locally and only ever ask for what is new.
    """

    def __init__(self, client, room: str, host_did: str):
        self.client = client
        self.room = room
        self.host_did = host_did
        self.messages: list[dict] = []
        self.seq = 0
        self.last_seq = 0
        self.gap = False

    def poll(self, wait: float = 10.0) -> list[dict]:
        view = self.client.read(self.room, since=self.seq, limit=200, wait=wait)
        self.last_seq = view.get("last_seq") or self.last_seq
        new = view.get("messages") or []
        if new:
            first = view.get("first_seq") or new[0]["seq"]
            # The ring compacts under storage pressure; a jump means we lost lines.
            if self.seq and first > self.seq + 1:
                self.gap = True
            self.messages.extend(new)
            self.seq = new[-1]["seq"]
        return new

    def rounds(self) -> dict:
        return collect(self.messages, self.room, self.host_did)

    def table(self) -> dict:
        return score(self.rounds())
