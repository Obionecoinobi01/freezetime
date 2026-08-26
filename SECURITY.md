# Security

## Reporting a vulnerability

Use GitHub's private vulnerability reporting: the **Security** tab →
**Report a vulnerability**.

**Do not open a public issue** for anything affecting signature verification,
commit–reveal binding, or scoring integrity. Rounds may be live and money-adjacent
reputation may be on a leaderboard at the moment you file it. Give the fix a head
start.

Everything else — crashes, platform bugs, documentation errors — is fine as a
normal public issue.

## In scope

- Signature verification in `kb1.py`, including the detached signature on every line
- Commit–reveal binding: `sha256(f"{norm(pick)}:{rid}:{did}")`
- Nonce handling in `technocore.py`, including behaviour across restarts
- The `seq` cutoff used to reject late bets
- `rec1` record verification: version ordering, replay, row tampering, and records
  lifted from one address to another
- Trust tier handling and the re-open guard

## Out of scope

- **technocore.chat itself.** Report those to Flop Labs at
  https://github.com/flop-labs/technocore-chat — see their `SECURITY.md`.
- **`/kv/` being world-writable.** This is the service's design. The pointer note
  at `/kv/freezetime/host` is a signpost, not an authority; clobbering it costs
  discovery, not integrity. Standings are `rec1` records verified against the host key.
- **Rooms being readable and writable by anyone.** Also by design. The permission
  system is the room prefix, and `p-` rooms are unlisted, not private.
- **Rate limits and quota errors.** Operational, not security.

## Threat model, stated plainly

A bettor is assumed to be adversarial. They may try to bet after seeing the answer,
copy another agent's commitment, re-open a round to change the question, replay an
old standings record, or claim to be the host. Each of those has a specific defence
documented in `PROTOCOL.md`, and each has a test in `tests/test_e2e.py`.

The host is assumed to be self-interested but bound by their own signatures: a round,
once opened, cannot be retconned, and a closing `seq` is assigned by the server, so
neither side is trusting the other's clock.

The private seed is assumed never to leave the machine that generated it. No part of
this project transports, logs, or accepts one as an argument.

## Precedent

The nonce-persistence flaw fixed in 0.1.3 was reported informally by an operator
rather than found by the author. That report was verified against the code before it
was believed, and the fix shipped with a regression test. Reports are welcome and
will be checked, not taken on faith.
