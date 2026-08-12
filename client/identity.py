"""Client identity management.

Each client owns an RSA-2048 key pair:

* The **private key** never leaves the machine. It is stored PEM-encoded with
  ``0600`` permissions in ``~/.localnetwork/identity.pem``.
* The **public key** is registered with the mediation server and used to verify
  signed auth challenges.

Security model (DESIGN.md §5.1): the server sends a random nonce, the client
signs it with its private key, and the server verifies the signature using the
stored public key.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

DEFAULT_IDENTITY_DIR = "~/.localnetwork"
PRIVATE_KEY_FILENAME = "identity.pem"
PUBLIC_KEY_FILENAME = "identity.pub"
RSA_KEY_SIZE = 2048


class IdentityError(RuntimeError):
    """Raised for identity generation, storage, or verification failures."""


def generate_identity() -> Tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """Generate a fresh RSA-2048 key pair."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_SIZE)
    return private_key, private_key.public_key()


def _private_pem(private_key: rsa.RSAPrivateKey) -> bytes:
    return private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_pem(public_key: rsa.RSAPublicKey) -> bytes:
    return public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def _resolve_dir(path: str | os.PathLike) -> Path:
    return Path(os.path.expanduser(os.fspath(path)))


def save_identity(
    private_key: rsa.RSAPrivateKey,
    public_key: rsa.RSAPublicKey,
    path: str = DEFAULT_IDENTITY_DIR,
) -> Tuple[Path, Path]:
    """Persist the key pair as PEM files.

    The directory is created if missing; the private key file gets ``0600``
    permissions (``0400`` owner-read on Windows, where the bit map differs).

    Returns:
        ``(private_path, public_path)``.
    """
    directory = _resolve_dir(path)
    directory.mkdir(parents=True, exist_ok=True)

    private_path = directory / PRIVATE_KEY_FILENAME
    public_path = directory / PUBLIC_KEY_FILENAME

    private_path.write_bytes(_private_pem(private_key))
    public_path.write_bytes(_public_pem(public_key))

    _chmod_private(private_path)
    return private_path, public_path


def _chmod_private(path: Path) -> None:
    """Best-effort 0600 permissions on POSIX; no-op on Windows."""
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # Windows doesn't support the same permission model


def load_identity(
    path: str = DEFAULT_IDENTITY_DIR,
) -> Tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey]:
    """Load the key pair from disk.

    Raises:
        IdentityError: If the key files don't exist or can't be parsed.
    """
    directory = _resolve_dir(path)
    private_path = directory / PRIVATE_KEY_FILENAME
    public_path = directory / PUBLIC_KEY_FILENAME

    if not private_path.exists() or not public_path.exists():
        raise IdentityError(
            f"no identity found in {directory} — run generate_identity() first"
        )
    try:
        private_key = serialization.load_pem_private_key(
            private_path.read_bytes(), password=None
        )
        public_key = serialization.load_pem_public_key(public_path.read_bytes())
    except (ValueError, TypeError) as exc:
        raise IdentityError(f"failed to parse identity files: {exc}") from exc

    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise IdentityError("identity.pem does not contain an RSA private key")
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise IdentityError("identity.pub does not contain an RSA public key")
    return private_key, public_key


def load_public_key(path: str) -> rsa.RSAPublicKey:
    """Load only the public key from an arbitrary PEM file path."""
    pem_path = _resolve_dir(path) if not Path(path).exists() else Path(path)
    if pem_path.is_dir():
        pem_path = pem_path / PUBLIC_KEY_FILENAME
    if not pem_path.exists():
        raise IdentityError(f"public key file not found: {pem_path}")
    key = serialization.load_pem_public_key(pem_path.read_bytes())
    if not isinstance(key, rsa.RSAPublicKey):
        raise IdentityError(f"{pem_path} does not contain an RSA public key")
    return key


def public_key_fingerprint(public_key: rsa.RSAPublicKey) -> str:
    """Return a short human-friendly fingerprint (SHA-256, colon-separated hex)."""
    digest = hashlib.sha256(_public_pem(public_key)).digest()
    return ":".join(f"{b:02x}" for b in digest[:16])


def sign_challenge(
    private_key: rsa.RSAPrivateKey, challenge: bytes
) -> bytes:
    """Sign a challenge nonce with the private key (PKCS#1 v1.5 + SHA-256)."""
    if not isinstance(challenge, bytes) or not challenge:
        raise IdentityError("challenge must be non-empty bytes")
    return private_key.sign(challenge, padding.PKCS1v15(), hashes.SHA256())


def verify_challenge(
    public_key: rsa.RSAPublicKey, challenge: bytes, signature: bytes
) -> bool:
    """Verify a challenge signature; returns False (never raises) on mismatch."""
    try:
        public_key.verify(signature, challenge, padding.PKCS1v15(), hashes.SHA256())
        return True
    except (InvalidSignature, ValueError):
        return False


__all__ = [
    "DEFAULT_IDENTITY_DIR",
    "PRIVATE_KEY_FILENAME",
    "PUBLIC_KEY_FILENAME",
    "RSA_KEY_SIZE",
    "IdentityError",
    "generate_identity",
    "save_identity",
    "load_identity",
    "load_public_key",
    "public_key_fingerprint",
    "sign_challenge",
    "verify_challenge",
]
