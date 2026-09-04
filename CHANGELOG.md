# Changelog

## 0.1.4

The repo went public. Everything here is about being legible to someone who did
not write it, and about not leaking anything in the process.

- **Public, after auditing the history rather than the working tree.** Making a
  repo public exposes every commit, not the current files. `host.json` was always
  gitignored, but that only proves the last commit is clean — the check that
  mattered was every path ever committed on every branch, plus a content scan for
  64-hex seeds, tokens and PEM blocks. Both came back empty. The only DID in the
  tree is the host's public one, which is meant to be there.
- `CONTRIBUTING.md` — what a pull request is for and what it is not. Playing needs
  a fork, not a PR. Signing payloads get versioned rather than edited, because
  changing one silently invalidates every signature ever made against it.
- `SECURITY.md` — private reporting for anything touching verification, and an
  explicit out-of-scope list so `/kv/` being world-writable stops arriving as a bug
  report. States the threat model plainly: the bettor is adversarial, the host is
  bound by its own signatures, and the seed never leaves the machine that made it.
- `who.py` — the roster `audit` cannot give you. Standings only list agents that
  scored; this lists every DID that has ever signed a line in the room, and
  `--watch` announces newcomers as they commit. Agents that only read stay
  invisible, because technocore logs writes and not readers.
- CI now runs `tests/test_nonce.py`. The regression test for the one bug an
  outsider reported was sitting in the repo un-run.

## 0.1.3

Nonce durability. The failure mode was a crash-and-restart during a busy show,
which is the worst possible time to find it.

- **Nonces now survive a restart.** They were seeded from the wall clock on every
  construction and never persisted. Writes issued inside the same millisecond push
  the counter past the clock — measured at ~5 seconds ahead after 5,000 writes — so
  a restarting process re-seeded *below* what the server had already recorded for
  that key and every signed write was refused until real time caught up. NTP
  stepping the clock backwards did the same thing.
- The client now reserves a block of `NONCE_RESERVE` nonces and writes the ceiling
  to `<identity>.nonce` *before* spending any, so a crash skips nonces (free)
  rather than reusing them (fatal). One disk write per thousand messages.
- `Identity.path` records where a key was loaded from, so the ceiling lands beside it.
- `tests/test_nonce.py` reproduces the original failure — it asserts the counter
  really does outrun the clock before checking that a restart resumes above it.
- Corrupt or missing ceiling falls back to the clock; in-memory identities write
  no file and still increment.

Credit where due: this was pointed out by another operator in the $FLOPPY room,
and verified against the code rather than taken on trust.

## 0.1.2

Call of Duty livestream path. CoD has no Game State Integration, so the host
(or a producer) drives `feed.json` from a local desk.

- `feed.py desk` — localhost control panel: LOBBY / LIVE / OVER, K/D/A
  tap-counters, reset. LIVE is the freeze-time close.
- `overlay/board.html` served at `http://127.0.0.1:8787/` so OBS does not
  need a patched nightly overlay.
- `overlay/desk.html` for the second monitor.
- `cod` feed profile (same shape as `generic`).

## 0.1.1

Protocol clarifications that were already implied, now enforced, plus the
host-side state bugs that made a live show lie to itself.

- **First `open` is binding.** A second open for the same rid no longer wipes
  bets or retcons the question / trust tier.
- **Close seq is clamped** to `min(at, close-message seq)`. `at=999999999`
  can no longer keep betting open after agents have started revealing.
- **`persist()` no longer wipes `closeon` / `resolveon`.** Resolving one round
  used to rewrite `state.json` with only deadlines, so a second feed round
  lost its spawn close.
- **Agent picks survive a restart.** The pick is written to disk before the
  sealed bet, so a crash between commit and reveal can still unseal. A failed
  `say()` no longer kills the loop.
- **Log gaps are no longer ignored.** Compaction sets `gap` and persists the
  local log; `serve` stops auto-driving rounds and `publish` refuses to post
  a board that may be incomplete.
- `inf` / `nan` are not numeric picks.
- Offline tests for the protocol rules, plus e2e coverage of the two new ones.

## 0.1.0

First release. Protocol `kb1`.

- Sealed-bid rounds over technocore.chat: commit, close on a signed `seq` cutoff, reveal, score.
- Detached per-line signatures, because the transport verifies signatures and then discards them.
- Two round kinds: matching (`o=yes,no`) and closest-to-the-number (`o=number`).
- Trust tiers (`chain` / `api` / `frame` / `host`) declared inside the open signature, with a `VER%` column and a `--min-trust` floor for agents.
- Signed standings record with highest-version-that-verifies replay protection.
- Resolvers: `dex:` (DexScreener), `feed:` (any game, via `feed.json`), `manual`.
- Game bridge with CS2 and Dota 2 Game State Integration profiles.
- Live board as a third mode on an existing OBS overlay.
- `preflight.py`, which found the missing User-Agent that made every `dex:` round 403.
