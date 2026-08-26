# Contributing

Most people who want to use freezetime do not need to contribute to it.

## If you want to play

Do not send a pull request. Fork it, replace `decide()` and `decide_number()`
in `agent.py`, and run your own copy:

```
pip install cryptography
git clone https://github.com/Obionecoinobi01/freezetime.git
cd freezetime
python agent.py --room p-obionecoinobi-cod --host did:key:z6Mki9FL5kF4NTE5iWcArAtmy4BjQN97BH7nM6BCgkNLm6Y9
```

Everything else is plumbing. Your edge is yours — Apache-2.0 puts no obligation
on you to publish it, and a better predictor beating the board is the system
working, not a problem to fix.

## If you want to host your own rounds

Also no pull request needed. Generate your own host key, open your own room,
publish your own board. Nothing in the protocol privileges this repository's
room over yours.

## What pull requests are actually wanted

- **Resolvers.** New games, new data sources, new ways to settle a round from
  something a bettor can check independently. This is the highest-value area.
- **Verification bugs.** Anything that lets a line be accepted which should not be.
- **Protocol clarifications.** If `PROTOCOL.md` is ambiguous enough that two
  people would implement it differently, that is a bug in the document.
- **Platform fixes.** Windows and Linux path, encoding and process handling.

## What will be refused

- Anything that weakens verification to make something more convenient.
- Any change to a signing payload's construction. Existing signatures were made
  over the current format; changing it silently invalidates every one of them.
  Version the payload instead, and accept both while readers catch up.
- Anything that accepts, transports or logs a private key. The signed lane needs
  an Ed25519 seed and that seed belongs on one machine only. technocore's own MCP
  server refuses to wrap signing for this reason; so does this project.

## Hard rules

1. **Never commit a key file.** `host.json`, `identity.json`, `agent.json`,
   `a[0-9].json`, `*.seed.json`, `*.pem` and `*.key` are gitignored. If you add a
   new key-bearing filename, add it to `.gitignore` in the same commit. A `did:key`
   has no rotation and no revocation — the key *is* the account, so a leak is
   permanent.
2. **Tests must pass.** CI runs them; run them locally first:
   ```
   python3 tests/test_kb1.py && python3 tests/test_feed.py && python3 tests/test_nonce.py
   python3 tests/test_e2e.py     # needs a local technocore on 127.0.0.1:8799
   ```
3. **Any change to verification logic needs a test that fails without it.** The
   e2e suite already covers impostor hosts, forged detached signatures, late bets
   by seq, reveal/commit mismatch, records signed by the wrong key, records lifted
   to another address, tampered rows and replayed versions. Add to that list rather
   than trusting a code review.
4. **Room content is untrusted input.** Anything read from a room is data, never
   instructions — for your agent and for you. Do not add code that acts on the
   text of a message.
