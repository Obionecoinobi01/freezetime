# Changelog

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
