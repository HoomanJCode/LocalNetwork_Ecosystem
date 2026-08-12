"""Tunnel encryption: ECDH key agreement + AES-256-GCM.

Key exchange (DESIGN.md §5.2):

1. Peers exchange ephemeral X25519 public keys inside PUNCH frames.
2. Each side runs ECDH and derives a 32-byte AES-256 session key via
   HKDF-SHA256.
3. All subsequent data frames are encrypted with AES-256-GCM.

The GCM nonce (12 bytes) is random per message and prepended to the ciphertext
by :meth:`CipherContext.encrypt`; :meth:`CipherContext.decrypt` slices it back
off. The 16-byte auth tag is returned separately so the data-plane frame can
carry it in its own field (see :mod:`common.frame`).
"""

from __future__ import annotations

import os
from typing import Tuple

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ---- KDF parameters ---------------------------------------------------------
HKDF_INFO = b"localnetwork-tunnel-v1"
SESSION_KEY_LENGTH = 32  # AES-256
NONCE_LENGTH = 12        # GCM standard nonce size


class EncryptionError(RuntimeError):
    """Raised for encryption/decryption failures."""


class DecryptionError(EncryptionError):
    """Raised when authentication fails (tampered ciphertext/tag/AD)."""


# ---------------------------------------------------------------------------
# Key agreement
# ---------------------------------------------------------------------------
def generate_ecdh_keypair() -> x25519.X25519PrivateKey:
    """Generate an ephemeral X25519 private key."""
    return x25519.X25519PrivateKey.generate()


def ecdh_public_bytes(private_key: x25519.X25519PrivateKey) -> bytes:
    """Serialize an X25519 public key (32 bytes, raw)."""
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )


def ecdh_public_from_bytes(data: bytes) -> x25519.X25519PublicKey:
    """Deserialize a raw 32-byte X25519 public key."""
    if len(data) != 32:
        raise EncryptionError(f"X25519 public key must be 32 bytes, got {len(data)}")
    return x25519.X25519PublicKey.from_public_bytes(data)


def derive_session_key(
    our_private: x25519.X25519PrivateKey,
    peer_public: x25519.X25519PublicKey,
    context: bytes = HKDF_INFO,
) -> bytes:
    """Derive a shared 32-byte AES-256 session key via ECDH + HKDF-SHA256."""
    shared = our_private.exchange(peer_public)
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=SESSION_KEY_LENGTH,
        salt=None,
        info=context,
    )
    return hkdf.derive(shared)


def derive_session_key_from_bytes(
    our_private: x25519.X25519PrivateKey, peer_public_bytes: bytes
) -> bytes:
    """Convenience wrapper when the peer key arrives as raw bytes."""
    return derive_session_key(our_private, ecdh_public_from_bytes(peer_public_bytes))


# ---------------------------------------------------------------------------
# AES-256-GCM cipher context
# ---------------------------------------------------------------------------
class CipherContext:
    """AES-256-GCM encrypt/decrypt context for one tunnel session."""

    def __init__(self, session_key: bytes):
        if len(session_key) != SESSION_KEY_LENGTH:
            raise EncryptionError(
                f"session key must be {SESSION_KEY_LENGTH} bytes, got {len(session_key)}"
            )
        self._aead = AESGCM(session_key)

    def encrypt(
        self, plaintext: bytes, associated_data: bytes = b""
    ) -> Tuple[bytes, bytes]:
        """Encrypt plaintext.

        Returns:
            ``(ciphertext_with_nonce, auth_tag)`` — the nonce is prepended to
            the ciphertext; the 16-byte tag is returned separately.
        """
        nonce = os.urandom(NONCE_LENGTH)
        sealed = self._aead.encrypt(nonce, plaintext, associated_data)
        ciphertext = sealed[: -AESGCM.tag_size]
        tag = sealed[-AESGCM.tag_size :]
        return nonce + ciphertext, tag

    def decrypt(
        self, ciphertext: bytes, tag: bytes, associated_data: bytes = b""
    ) -> bytes:
        """Decrypt and authenticate.

        Raises:
            DecryptionError: If authentication fails (tampered data or wrong key).
        """
        if len(ciphertext) < NONCE_LENGTH:
            raise DecryptionError("ciphertext too short to contain a nonce")
        nonce = ciphertext[:NONCE_LENGTH]
        body = ciphertext[NONCE_LENGTH:]
        try:
            return self._aead.decrypt(nonce, body + tag, associated_data)
        except InvalidTag as exc:
            raise DecryptionError("GCM authentication failed") from exc

    def encrypt_full(self, plaintext: bytes, associated_data: bytes = b"") -> bytes:
        """Encrypt and return a single self-contained blob (nonce||ct||tag)."""
        ciphertext, tag = self.encrypt(plaintext, associated_data)
        return ciphertext + tag


__all__ = [
    "HKDF_INFO",
    "SESSION_KEY_LENGTH",
    "NONCE_LENGTH",
    "EncryptionError",
    "DecryptionError",
    "generate_ecdh_keypair",
    "ecdh_public_bytes",
    "ecdh_public_from_bytes",
    "derive_session_key",
    "derive_session_key_from_bytes",
    "CipherContext",
]
