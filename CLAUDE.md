# CLAUDE.md — freezetime

Read this first. It is the state of the project and the things that were learned
the hard way, so a new session does not rediscover them.

---

## What this is

A scoreboard for AI agents that guess what happens on a live stream. Agents post a
**sealed** guess, betting closes at spawn, guesses unseal, closest scores. Three
properties do the work:

1. **Nobody can guess late.** The close names a server-assigned `seq`, and nothing
   can be inserted into the middle of an append-only log. No clock is trusted.
2. **Nobody can copy a guess.** A bet is `sha256(pick:rid:did)` — bound to the
   sender's own key, so a stolen commitment is useless.
3. **Nobody has to trust the board.** It is recomputable by anyone from the public
   room. The published standings are signed, but the *arithmetic* is checkable.

Transport is [technocore.chat](https://technocore.chat) — HTTP GET only, no
accounts, no wallets, no money. Protocol is `kb1`, specced in `PROTOCOL.md`.

**No token, no chain, no stakes.** Deliberately. Keep it that way without a lawyer.

---

## Live setup

| | |
|---|---|
| Repo | `github.com/Obionecoinobi01/freezetime` — **private** |
| Local | `C:\Users\Obionecoinobi\freezetime` |
| Room | `p-obionecoinobi-cod` on `https://technocore.chat` |
| Host key | `host.json` → `did:key:z6Mki9FL5kF4NTE5iWcArAtmy4BjQN97BH7nM6BCgkNLm6Y9` |
| Agent key | `a1.json` (testbot) — separate identity, correct |
| Browser key | a third identity, presence only in the $FLOPPY room |
| Overlay | `overlay-live.html`, key `9`, in OBS as a **local file** |
| Python | **`py`**, not `python3` — Windows |

`host.json` is the whole game. Back it up; there is no recovery and no rotation.

---

## What is proven, and what is not

**Proven on the live service:** signing (payload matches what the server expects),
room creation and reads, the dex resolver reaching DexScreener, the overlay
rendering at 1920×1080 with a transparent background and nothing truncated.

**Not yet done:** a full round, open to settle, on the live service. Everything
else was tested against a local technocore. This is the last unknown.

**Known unsolved:** sybil. `did:key` is free, so one person is a hundred agents.
Scoring rewards streak and hit rate rather than volume, which blunts it and does
not fix it.

---

## Running a show

Four windows, each blocking, each needing `cd "$HOME\freezetime"` first.

```powershell
py ringmaster.py serve      # 1 — board on 127.0.0.1:8787
py feed.py desk --open      # 2 — control panel on 127.0.0.1:31337, second monitor, keep FOCUSED
py agent.py --key a1.json --label testbot --room p-obionecoinobi-cod --host did:key:z6Mki9FL5kF4NTE5iWcArAtmy4BjQN97BH7nM6BCgkNLm6Y9
                            # 4 — free, for round commands
```

Per match: desk `R` reset → `1` LOBBY → open the round → `2` LIVE **at spawn, this
is the close** → tap `K`/`D`/`A` → `3` OVER, which settles from the feed.

```powershell
py ringmaster.py open "what K/D do I finish this match on?" --opts number --res feed:kd --close-on live --trust frame
```

`publish` writes the signed standings record. **Optional per round** — the overlay
computes the board from the room directly. Do it once at the end of the night.

---

## Gotchas, all found the hard way

**technocore's limit errors name the wrong limit.** They quote the global cap
whatever actually fired. "note limit reached (40960)" was the **per-namespace** cap
on `did/`; "room limit reached (10240)" was **20 new rooms per day per IP**. Always
check `/rooms` and `/.well-known/agent.json` before believing the message.

**`did/` is full.** Publish identity notes in your own namespace instead. Nothing in
freezetime reads `did/` — agents are given the host DID directly.

**Room budget is per IP and shared.** Behind CGNAT you share 20/day with strangers.
An onboarding rush can spend it before you touch it.

**Rooms decay.** 7 days idle, or 24 hours if a room never got a second message. A
full round writes five or six, which settles it.

**Poll with `?since=<last seq>`, never `since=0`.** From zero the backlog returns
instantly, the `wait` never engages, and the read budget is gone in seconds.

**Long-poll waiters cap at 4 per IP**, counted per process. Over the cap the server
answers instantly with nothing, indistinguishable from a timeout.

**Nonces must survive restarts** — fixed in 0.1.3. They were seeded from the wall
clock, and writes inside one millisecond push the counter ~5s ahead of it, so a
restart re-seeded *below* what the server recorded and every write was refused.
Reserve-ahead ceiling now lives in `<identity>.nonce`.

**technocore verifies signatures then discards them.** The stored record keeps
`from` and `nonce`, never `sig`. That is why every `kb1` line carries its own
detached signature — otherwise the log is only as good as the relay's word.

**Signed `/kv/` writes are refused** outside `room-owners` and `room-allow`. That is
why the standings record carries its own internal signature over rows, version and
its own address, and why readers take the highest version that verifies.

**CORS is default-deny.** The overlay reads the ringmaster on localhost, not
technocore, which is the whole reason `ringmaster serve` exists.

**Windows:** `py`, not `python3`. In PowerShell `<` is a redirect operator and
`curl` is an alias for `Invoke-WebRequest` — use `curl.exe`.

**Do not run `git status` through the Cowork device bridge.** It leaves a
`.git/index.lock` the bridge cannot delete, and every later git command fails.
Use `git --no-optional-locks status`, or just run git from PowerShell.

---

## Open decisions

- **Repo is private**, so nobody can read the protocol, so nobody else's agent can
  play. This is the single thing blocking it from being a segment rather than a
  demo. Flip it public, or publish `PROTOCOL.md` as a standalone page.
- **Self-hosting** is built and parked — `deploy/` has compose, Caddy and a
  walkthrough. It fixes the caps entirely. Needs a VPS (~£5/mo) and a hostname.
- **Auto-open rounds** when the desk returns to LOBBY, so a show needs no terminal.
  Deliberately not built until the manual rhythm has been felt live.

## Editorial

Rounds settled from the local desk are `[frame]` tier — **say so on air.** The
agents cannot see the feed, so they are modelling the host, not reading the game.
And the host cannot see the guesses until after the close, so they cannot play to
spite the board either. Commit–reveal keeps the host honest, not just the bettors.
That is a better bit than pretending the board is more rigorous than it is.
