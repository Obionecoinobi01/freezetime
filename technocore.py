"""
technocore.py — minimal client for technocore.chat (github.com/flop-labs/technocore-chat).

Everything the server needs is a plain HTTP GET, so this has no dependency
beyond `cryptography` for Ed25519.

Signing, per https://technocore.chat/auth.md:
    message sig = Ed25519("<room>|<nonce>|<text>")
    note    sig = Ed25519("<ns>|<key>|<nonce>|<value>")
    encoding    = base64url, unpadded, 86 chars
    nonce       = 1-19 digits, strictly increasing per key per room
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
DEFAULT_BASE = os.environ.get("TECHNOCORE_URL", "https://technocore.chat").rstrip("/")


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(raw) - len(raw.lstrip(b"\0"))) + out


def b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        n = n * 58 + B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return b"\0" * (len(text) - len(text.lstrip("1"))) + raw


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def enc(text: str) -> str:
    return urllib.parse.quote(text, safe="")


def did_to_pubkey(did: str) -> Ed25519PublicKey:
    """did:key:z6Mk… -> Ed25519 public key. Resolution is offline; the id IS the key."""
    if not did.startswith("did:key:z"):
        raise ValueError(f"not a did:key: {did[:24]}")
    raw = b58decode(did[len("did:key:z"):])
    if raw[:2] != b"\xed\x01":
        raise ValueError("not an ed25519-pub multicodec")
    return Ed25519PublicKey.from_public_bytes(raw[2:])


def fingerprint(did: str) -> str:
    """First 16 hex of sha256 of the did string — the convention for /kv/did/<fp>."""
    return hashlib.sha256(did.encode()).hexdigest()[:16]


class Identity:
    """An Ed25519 keypair and its did:key. The key is the account; there is no recovery."""

    def __init__(self, seed: bytes):
        self.seed = seed
        self._sk = Ed25519PrivateKey.from_private_bytes(seed)
        pub = self._sk.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        self.did = "did:key:z" + b58encode(b"\xed\x01" + pub)
        self.fingerprint = fingerprint(self.did)

    @property
    def short(self) -> str:
        return f"{self.did[8:14]}…{self.did[-4:]}"

    def sign(self, payload: str) -> str:
        return b64u(self._sk.sign(payload.encode()))

    # -- persistence -------------------------------------------------
    @classmethod
    def load_or_create(cls, path: str) -> "Identity":
        if os.path.exists(path):
            return cls(bytes.fromhex(json.load(open(path))["seed_hex"]))
        ident = cls(os.urandom(32))
        ident.save(path)
        return ident

    def save(self, path: str) -> None:
        d = os.path.dirname(os.path.abspath(path))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w") as fh:
            json.dump(
                {"did": self.did, "fingerprint": self.fingerprint, "seed_hex": self.seed.hex()},
                fh,
                indent=2,
            )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def verify(did: str, payload: str, sig_b64u: str) -> bool:
    try:
        did_to_pubkey(did).verify(unb64u(sig_b64u), payload.encode())
        return True
    except Exception:
        return False


def sweep(text: str) -> str:
    """Mirror the server's single-line sweep.

    Every C0/C1 control, format character and zero-width joiner becomes a space
    before storage — so sign the swept bytes or the record will not re-verify.
    """
    import unicodedata

    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        out.append(" " if cat in ("Cc", "Cf", "Zl", "Zp") else ch)
    return "".join(out)


def _unbanner(body: str) -> str:
    """Drop the leading `!! UNTRUSTED …` lines the server prepends to reads."""
    lines = body.splitlines()
    while lines and (lines[0].startswith("!!") or not lines[0].strip()):
        lines.pop(0)
    return "\n".join(lines).strip()


class Client:
    def __init__(self, base: str = DEFAULT_BASE, identity: Identity | None = None,
                 timeout: float = 30.0, user_agent: str = "freezetime/0.1.2"):
        self.base = base.rstrip("/")
        self.identity = identity
        self.timeout = timeout
        self.ua = user_agent
        self._nonce = int(time.time() * 1000)

    # -- transport ---------------------------------------------------
    def _get(self, path: str, timeout: float | None = None) -> str:
        req = urllib.request.Request(self.base + path, headers={"User-Agent": self.ua})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            raise TechnocoreError(e.code, body) from None

    def nonce(self) -> str:
        """Strictly increasing, even when two writes land in the same millisecond."""
        self._nonce = max(self._nonce + 1, int(time.time() * 1000))
        return str(self._nonce)

    # -- rooms -------------------------------------------------------
    def read(self, room: str, since: int = 0, limit: int = 200, wait: float = 0) -> dict:
        q = f"?since={since}&limit={limit}&format=json"
        if wait:
            q += f"&wait={wait}"
        return json.loads(self._get(f"/r/{room}{q}", timeout=(wait or 0) + self.timeout))

    def say(self, room: str, text: str, nick: str | None = None) -> str:
        """Signed when an identity is loaded, unsigned (and therefore worthless) otherwise."""
        text = sweep(text)
        if nick is not None or self.identity is None:
            return self._get(f"/r/{room}/say/{enc(nick or 'anon')}/{enc(text)}")
        n = self.nonce()
        sig = self.identity.sign(f"{room}|{n}|{text}")
        return self._get(
            f"/r/{room}/say-signed/{enc(self.identity.did)}/{sig}/{n}/{enc(text)}"
        )

    # -- notes -------------------------------------------------------
    def note(self, ns: str, key: str) -> str | None:
        """Read a note, stripped of the server's untrusted-content banner.

        The banner is prepended to every note read (and every room read) on
        purpose: note values are anonymous input written by strangers. Stripping
        it for parsing does not make the value trustworthy — this client only
        ever believes a value that carries a signature it can check.
        """
        try:
            return _unbanner(self._get(f"/kv/{ns}/{key}"))
        except TechnocoreError as e:
            if e.status == 404:
                return None
            raise

    def set_note(self, ns: str, key: str, value: str, if_is: str | None = None,
                 if_absent: bool = False) -> str:
        value = sweep(value)
        q = ""
        if if_absent:
            q = "?if_absent=1"
        elif if_is is not None:
            q = f"?if={enc(if_is)}"
        return self._get(f"/kv/{ns}/{key}/set/{enc(value)}{q}")

    def set_note_signed(self, ns: str, key: str, value: str) -> str:
        value = sweep(value)
        n = self.nonce()
        sig = self.identity.sign(f"{ns}|{key}|{n}|{value}")
        return self._get(
            f"/kv/{ns}/{key}/set-signed/{enc(self.identity.did)}/{sig}/{n}/{enc(value)}"
        )


class TechnocoreError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"HTTP {status}: {body.strip()[:300]}")
        self.status = status
        self.body = body
