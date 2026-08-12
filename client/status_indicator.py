"""Platform-aware status indicator.

DESIGN.md §10.3: System tray icon on desktop platforms (Windows/Linux/macOS)
with four states; falls back to terminal status line on headless/Termux.

States:
* 🟢 green  — all good (connected, tunnels healthy)
* 🟡 yellow — degraded (some tunnels relay, or suspect peers)
* 🔴 red    — disconnected from server
* ⚪ gray   — idle (not connected yet, starting up)
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any, Optional

log = logging.getLogger("localnetwork.client.status")

STATE_GREEN = "green"
STATE_YELLOW = "yellow"
STATE_RED = "red"
STATE_GRAY = "gray"

_STATUS_TEXT = {
    STATE_GREEN: "🟢 Connected",
    STATE_YELLOW: "🟡 Degraded",
    STATE_RED: "🔴 Disconnected",
    STATE_GRAY: "⚪ Idle",
}

# Default local admin panel port (matches client/config.py).
DEFAULT_WEB_PORT = 54002


def _web_port(daemon: Any) -> int:
    """Resolve the client web panel port from the daemon config."""
    if daemon is None:
        return DEFAULT_WEB_PORT
    config = getattr(daemon, "config", None)
    port = getattr(config, "web_port", None)
    return port or DEFAULT_WEB_PORT


def _web_url(daemon: Any, path: str = "") -> str:
    """Build a URL into the local web admin panel."""
    return f"http://localhost:{_web_port(daemon)}{path}"


class StatusIndicator:
    """Abstract status indicator.

    Platform-specific subclasses handle GUI (system tray) or terminal output.
    """

    def __init__(self) -> None:
        self._state: str = STATE_GRAY
        self._tooltip: str = "LocalNetwork — starting…"
        self._daemon: Any = None  # ClientDaemon reference for context

    @property
    def state(self) -> str:
        return self._state

    def set_daemon(self, daemon: Any) -> None:
        """Give the indicator access to daemon state."""
        self._daemon = daemon

    def update(
        self,
        state: Optional[str] = None,
        tooltip: Optional[str] = None,
    ) -> None:
        """Update the indicator state and tooltip."""
        if state is not None:
            self._state = state
        if tooltip is not None:
            self._tooltip = tooltip
        self._render()

    def _render(self) -> None:
        """Render the current state (override in subclasses)."""
        pass

    def start(self) -> None:
        """Start displaying the indicator."""
        self.update(STATE_GRAY, "LocalNetwork — starting…")

    def stop(self) -> None:
        """Stop displaying the indicator."""
        pass


class TerminalIndicator(StatusIndicator):
    """Fallback indicator that prints a status line to the terminal."""

    def __init__(self, interval: float = 30.0) -> None:
        super().__init__()
        self._interval = interval
        self._task: Optional[asyncio.Task] = None

    def _render(self) -> None:
        """Print current status line."""
        text = _STATUS_TEXT.get(self._state, self._state)
        print(f"\r{text}  {self._tooltip}  ", end="", flush=True)

    def start(self) -> None:
        super().start()
        self._render()

    def stop(self) -> None:
        print()  # Newline after status line
        super().stop()


class SystemTrayIndicator(StatusIndicator):
    """System tray / menu bar indicator using pystray (optional dependency)."""

    def __init__(self) -> None:
        super().__init__()
        self._icon = None
        self._available = False
        try:
            import pystray
            from PIL import Image, ImageDraw

            self._available = True
            self._pystray = pystray
            self._Image = Image
            self._ImageDraw = ImageDraw
        except ImportError:
            self._available = False

    def _render(self) -> None:
        """Update the system tray icon and menu."""
        if not self._available or self._icon is None:
            return
        self._icon.title = self._tooltip
        # Icon color based on state
        colors = {
            STATE_GREEN: (34, 197, 94),
            STATE_YELLOW: (245, 158, 11),
            STATE_RED: (239, 68, 68),
            STATE_GRAY: (128, 128, 128),
        }
        color = colors.get(self._state, (128, 128, 128))
        img = self._make_icon_image(color)
        self._icon.icon = img

    def _make_icon_image(self, color: tuple) -> Any:
        """Create a 64x64 solid-colored circle icon."""
        if not self._available:
            return None
        img = self._Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = self._ImageDraw.Draw(img)
        draw.ellipse([4, 4, 60, 60], fill=color)
        return img

    def start(self) -> None:
        if not self._available:
            log.debug("pystray not available — using terminal indicator")
            return
        super().start()
        try:
            menu = self._pystray.Menu(
                self._pystray.MenuItem(
                    "Open Dashboard", self._open_dashboard, default=True
                ),
                self._pystray.MenuItem("Share Service…", self._share_service),
                self._pystray.Menu.SEPARATOR,
                self._pystray.MenuItem("Quit", self._quit),
            )
            img = self._make_icon_image((128, 128, 128))
            self._icon = self._pystray.Icon(
                "LocalNetwork",
                img,
                "LocalNetwork",
                menu,
            )
            self._icon.run_detached()
        except Exception as exc:
            log.debug("system tray failed: %r — using terminal", exc)
            self._available = False

    def _open_dashboard(self, icon, item) -> None:
        """Open the web dashboard."""
        import webbrowser
        webbrowser.open(_web_url(self._daemon))

    def _share_service(self, icon, item) -> None:
        """Open the service sharing page of the local admin panel."""
        import webbrowser
        webbrowser.open(_web_url(self._daemon, "/services"))

    def _quit(self, icon, item) -> None:
        """Request daemon shutdown."""
        if self._icon is not None:
            self._icon.stop()
        if self._daemon is not None:
            self._daemon.request_shutdown()

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
        super().stop()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def create_status_indicator() -> StatusIndicator:
    """Create the best status indicator for the current environment.

    Prefers system tray when available (desktop OS with pystray installed).
    Falls back to terminal status line on headless, Termux, or when pystray
    is not installed.
    """
    # Headless / Termux / no display → terminal
    if not sys.stdout.isatty() or not _has_display():
        return TerminalIndicator()

    # Try system tray
    tray = SystemTrayIndicator()
    if tray._available:
        return tray
    return TerminalIndicator()


def _has_display() -> bool:
    """Check if a graphical display is available."""
    import os

    if sys.platform == "linux":
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    if sys.platform == "darwin":
        return True  # macOS always has a display
    if sys.platform == "win32":
        return True  # Windows always has a display
    return False


__all__ = [
    "StatusIndicator",
    "TerminalIndicator",
    "SystemTrayIndicator",
    "create_status_indicator",
    "STATE_GREEN",
    "STATE_YELLOW",
    "STATE_RED",
    "STATE_GRAY",
    "DEFAULT_WEB_PORT",
    "_web_port",
    "_web_url",
]
