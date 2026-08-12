"""Server admin panel authentication.

Uses HTTP session cookies (aiohttp_session) for authentication.
Credentials: ``LNSERVER_ADMIN_USER`` / ``LNSERVER_ADMIN_PASS`` env vars.
On first launch with no env vars, a random password is generated.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from typing import Optional

from aiohttp import web

ADMIN_USER_ENV = "LNSERVER_ADMIN_USER"
ADMIN_PASS_ENV = "LNSERVER_ADMIN_PASS"
SESSION_KEY = "admin_authenticated"
SESSION_TIMEOUT = 3600  # 1 hour

# In-memory session store (simple dict; for production, use aiohttp_session)
_sessions: dict[str, float] = {}


def get_admin_credentials() -> tuple[str, str]:
    """Get admin credentials from env vars or generate defaults.

    Returns:
        ``(username, password)`` tuple.
    """
    user = os.getenv(ADMIN_USER_ENV, "admin")
    password = os.getenv(ADMIN_PASS_ENV, "")

    if not password:
        password = secrets.token_urlsafe(16)
        print(f"\n═══ LocalNetwork Server Admin ═══")
        print(f"  Username: {user}")
        print(f"  Password: {password}")
        print(f"  (Set {ADMIN_USER_ENV} and {ADMIN_PASS_ENV} env vars to override)")
        print(f"═════════════════════════════════\n")

    return user, password


def create_session() -> str:
    """Create a new admin session token.

    Returns:
        The session token string.
    """
    token = secrets.token_urlsafe(32)
    _sessions[token] = time.time()
    return token


def validate_session(token: Optional[str]) -> bool:
    """Check if a session token is valid.

    Args:
        token: The session token from the cookie.

    Returns:
        True if valid and not expired.
    """
    if not token or token not in _sessions:
        return False
    created = _sessions[token]
    if time.time() - created > SESSION_TIMEOUT:
        del _sessions[token]
        return False
    return True


def destroy_session(token: str) -> None:
    """Invalidate a session token."""
    _sessions.pop(token, None)


# ---- aiohttp middleware -----------------------------------------------------
@web.middleware
async def auth_middleware(request: web.Request, handler) -> web.Response:
    """Middleware that protects all panel routes with authentication.

    Allows login page and static assets through; everything else requires
    a valid session cookie.
    """
    path = request.path

    # Allow login page and static assets
    if path in ("/login",) or path.startswith("/static/"):
        return await handler(request)

    # Check session cookie
    token = request.cookies.get("ln_admin_session")
    if not validate_session(token):
        if path.startswith("/api/"):
            return web.json_response({"error": "unauthorized"}, status=401)
        raise web.HTTPFound("/login")

    return await handler(request)


# ---- Login/logout routes ----------------------------------------------------
async def login_page(request: web.Request) -> web.Response:
    """Render the login page."""
    import jinja2

    env = request.app["jinja_env"]
    template = env.get_template("login.html")
    error = request.query.get("error", "")
    html = template.render(error=error, panel_name="Server Admin")
    return web.Response(text=html, content_type="text/html")


async def login_handler(request: web.Request) -> web.Response:
    """Handle login form submission."""
    data = await request.post()
    username = data.get("username", "")
    password = data.get("password", "")

    admin_user, admin_pass = request.app["admin_credentials"]
    if username == admin_user and password == admin_pass:
        token = create_session()
        response = web.HTTPFound("/")
        response.set_cookie(
            "ln_admin_session",
            token,
            httponly=True,
            max_age=SESSION_TIMEOUT,
            samesite="Lax",
        )
        return response

    raise web.HTTPFound("/login?error=Invalid+credentials")


async def logout_handler(request: web.Request) -> web.Response:
    """Handle logout."""
    token = request.cookies.get("ln_admin_session")
    if token:
        destroy_session(token)
    response = web.HTTPFound("/login")
    response.del_cookie("ln_admin_session")
    return response
