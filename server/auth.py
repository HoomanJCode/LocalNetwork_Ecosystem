"""Challenge/response authentication.

Flow (DESIGN.md §5.1):

1. Client sends REGISTER with its client_id and public key.
2. Server stores the key and issues a random 256-bit challenge.
3. Client signs the challenge with its private key and returns it.
4. Server verifies the signature against the registered public key.

Pending challenges expire after ``AUTH_CHALLENGE_TTL`` seconds and failed
attempts are counted to throttle brute force (``AUTH_MAX_ATTEMPTS``).
"""

from __future__ import annotations

import binascii
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from common import constants
from common.messages import AuthChallenge, Message, make_message
from server.registry import ClientRegistry

CHALLENGE_LENGTH = 32  # 256-bit nonce


def generate_challenge() -> bytes:
    """Return a fresh 32-byte random challenge nonce."""
    return os.urandom(CHALLENGE_LENGTH)


def create_auth_challenge(
    client_id: str,
    challenge: Optional[bytes] = None,
) -> AuthChallenge:
    """Build the AUTH_CHALLENGE message payload for a client."""
    nonce = challenge if challenge is not None else generate_challenge()
    return AuthChallenge(
        challenge=binascii.hexlify(nonce).decode("ascii"),
        client_id=client_id,
    )


def verify_auth_response(
    public_key_pem: str, challenge: bytes, signature: bytes
) -> bool:
    """Verify an RSA signature over a challenge nonce.

    Args:
        public_key_pem: The client's registered public key (PEM string).
        challenge: The original challenge nonce bytes.
        signature: Raw signature bytes.

    Returns:
        True when the signature is valid for the given key/challenge.
    """
    try:
        key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    except (ValueError, TypeError):
        return False
    if not isinstance(key, rsa.RSAPublicKey):
        return False
    try:
        key.verify(signature, challenge, padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, ValueError):
        return False


@dataclass
class PendingAuth:
    """A challenge awaiting its signed response."""

    client_id: str
    challenge: bytes
    created_at: float = field(default_factory=time.time)
    attempts: int = 0


class AuthSession:
    """Tracks pending challenges and enforces TTL + attempt limits."""

    def __init__(
        self,
        registry: ClientRegistry,
        ttl: float = constants.AUTH_CHALLENGE_TTL,
        max_attempts: int = constants.AUTH_MAX_ATTEMPTS,
    ) -> None:
        self._registry = registry
        self.ttl = ttl
        self.max_attempts = max_attempts
        self._pending: Dict[str, PendingAuth] = {}

    def issue(self, client_id: str) -> AuthChallenge:
        """Issue a fresh challenge for a client (replacing any pending one)."""
        nonce = generate_challenge()
        self._pending[client_id] = PendingAuth(client_id=client_id, challenge=nonce)
        return create_auth_challenge(client_id, nonce)

    def consume(self, client_id: str) -> Optional[PendingAuth]:
        """Fetch and clear the pending challenge for a client (one-shot)."""
        pending = self._pending.pop(client_id, None)
        if pending is None:
            return None
        if time.time() - pending.created_at > self.ttl:
            return None
        return pending

    def verify(
        self, client_id: str, challenge_hex: str, signature_hex: str
    ) -> tuple[bool, str]:
        """Verify a challenge response; returns (ok, message)."""
        pending = self.consume(client_id)
        if pending is None:
            return False, "no pending challenge (expired or not issued)"
        try:
            signature = binascii.unhexlify(signature_hex)
        except (binascii.Error, ValueError):
            return False, "malformed signature"

        record = self._registry.get(client_id)
        if record is None:
            return False, "unknown client"
        if not verify_auth_response(record.public_key_pem, pending.challenge, signature):
            pending.attempts += 1
            if pending.attempts >= self.max_attempts:
                return False, "auth failed; too many attempts"
            # Re-queue with incremented attempts so the limit persists
            self._pending[client_id] = pending
            return False, "signature verification failed"
        return True, "authenticated"

    def expire_stale(self) -> list[str]:
        """Drop challenges older than the TTL; returns dropped client ids."""
        cutoff = time.time() - self.ttl
        stale = [
            cid
            for cid, pending in self._pending.items()
            if pending.created_at < cutoff
        ]
        for cid in stale:
            del self._pending[cid]
        return stale

    def pending_count(self) -> int:
        return len(self._pending)


__all__ = [
    "CHALLENGE_LENGTH",
    "PendingAuth",
    "AuthSession",
    "generate_challenge",
    "create_auth_challenge",
    "verify_auth_response",
]
