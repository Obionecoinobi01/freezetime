# Changelog

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
