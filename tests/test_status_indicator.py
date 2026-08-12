"""Tests for the platform-aware status indicator (Phase 10)."""

from __future__ import annotations

from types import SimpleNamespace

from client.status_indicator import (
    DEFAULT_WEB_PORT,
    STATE_GRAY,
    STATE_GREEN,
    SystemTrayIndicator,
    _STATUS_TEXT,
    _web_port,
    _web_url,
)


def _daemon(web_port):
    """Build a minimal daemon stand-in with a config.web_port attribute."""
    return SimpleNamespace(config=SimpleNamespace(web_port=web_port))


class TestWebUrlHelpers:
    def test_default_port_when_no_daemon(self):
        assert _web_port(None) == DEFAULT_WEB_PORT
        assert _web_url(None) == "http://localhost:54002"

    def test_url_with_path(self):
        assert _web_url(None, "/services") == "http://localhost:54002/services"

    def test_custom_port_from_daemon(self):
        daemon = _daemon(64002)
        assert _web_port(daemon) == 64002
        assert _web_url(daemon, "/services") == "http://localhost:64002/services"

    def test_missing_or_zero_port_falls_back_to_default(self):
        assert _web_url(_daemon(None)) == "http://localhost:54002"
        assert _web_url(_daemon(0)) == "http://localhost:54002"

    def test_daemon_without_config(self):
        daemon = SimpleNamespace()  # no config attribute
        assert _web_url(daemon, "/services") == "http://localhost:54002/services"


class TestShareService:
    def test_share_service_opens_services_page(self, monkeypatch):
        opened = []
        monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
        indicator = SystemTrayIndicator()
        indicator.set_daemon(_daemon(64002))
        indicator._share_service(None, None)
        assert opened == ["http://localhost:64002/services"]

    def test_open_dashboard_uses_daemon_port(self, monkeypatch):
        opened = []
        monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
        indicator = SystemTrayIndicator()
        indicator.set_daemon(_daemon(64002))
        indicator._open_dashboard(None, None)
        assert opened == ["http://localhost:64002"]


class TestStateConstants:
    def test_state_constants(self):
        assert STATE_GREEN == "green"
        assert STATE_GRAY == "gray"

    def test_status_text_mapping(self):
        assert _STATUS_TEXT[STATE_GREEN] == "🟢 Connected"
        assert _STATUS_TEXT[STATE_GRAY] == "⚪ Idle"
