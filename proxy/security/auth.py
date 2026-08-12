"""HTTP Basic authentication (DESIGN.md Phase 20).

Supports htpasswd-style files with bcrypt-hashed passwords.
Returns 401 with WWW-Authenticate header on failure.
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

log = logging.getLogger("localnetwork.proxy.auth")


class BasicAuth:
    """HTTP Basic auth validator with htpasswd file support."""

    def __init__(self, htpasswd_path: str = "", realm: str = "Restricted") -> None:
        self.realm = realm
        self._users: dict[str, str] = {}  # username → bcrypt hash
        self._plain_users: dict[str, str] = {}  # username → plaintext (for testing)

        if htpasswd_path and os.path.exists(htpasswd_path):
            self._load_htpasswd(htpasswd_path)

    def _load_htpasswd(self, path: str) -> None:
        """Load credentials from an htpasswd file."""
        try:
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if ":" not in line:
                        continue
                    user, _, pwd_hash = line.partition(":")
                    self._users[user] = pwd_hash
        except OSError as exc:
            log.warning("cannot read htpasswd file %s: %r", path, exc)

    def set_user(self, username: str, password: str) -> None:
        """Set a user with a plaintext password (for programmatic use)."""
        self._plain_users[username] = password

    def check(self, authorization_header: Optional[str]) -> bool:
        """Validate an Authorization header.

        Args:
            authorization_header: The value of the Authorization HTTP header.

        Returns:
            True if the credentials are valid.
        """
        if not authorization_header:
            return False
        if not authorization_header.startswith("Basic "):
            return False

        try:
            encoded = authorization_header[6:]
            decoded = base64.b64decode(encoded).decode("utf-8")
            if ":" not in decoded:
                return False
            username, _, password = decoded.partition(":")
        except (ValueError, UnicodeDecodeError):
            return False

        # Check plain users first
        if username in self._plain_users:
            return self._plain_users[username] == password

        # Check bcrypt htpasswd users
        if username in self._users:
            try:
                import bcrypt
                return bcrypt.checkpw(password.encode("utf-8"), self._users[username].encode("utf-8"))
            except ImportError:
                return False
            except ValueError:
                return False

        return False

    def authenticate_header(self) -> str:
        """Return the WWW-Authenticate header value."""
        return f'Basic realm="{self.realm}"'


__all__ = ["BasicAuth"]
