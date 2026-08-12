# TODO — Phase 10–11: User Experience & CLI

> Part of the [LocalNetwork Ecosystem implementation plan](../TODO.md).
> Each task is a checkbox. Work through phases in order; items within a phase
> can be parallelized. Testing tasks are listed alongside the feature they test.
> See DESIGN.md §11 for the full UX design specification.

---

## Phase 10 — User Experience & Ease of Use

- [ ] 10.1 **Setup wizard** — `client/setup_wizard.py`
  - Interactive terminal wizard on first launch (or when no config exists)
  - Four paths: Join network, Create network, Set up hub, Set up proxy
  - Each path asks max 3-4 simple questions in plain language
  - Auto-detects platform and shows only relevant options
  - Generates config file at the end so subsequent launches skip the wizard
  - `--skip-wizard` flag for automated deployments

- [ ] 10.2 **Plain language error messages** — `common/errors.py`
  - `UserFacingError` base class: title, plain_description, suggestions (list), severity
  - Error catalog: `ConnectionRefused`, `AuthFailed`, `TunnelFailed`, `PortInUse`, `ConfigInvalid`, `PlatformUnsupported`, `FirewallBlock`
  - Each error maps technical exceptions to user-friendly messages
  - Severity levels: info, success, warning, error, critical — with consistent icons
  - `format_for_terminal()` — colored output with suggestions
  - `format_for_web()` — JSON with title, description, suggestions array

- [ ] 10.3 **Status indicator** — `client/status_indicator.py`
  - System tray / menu bar icon (platform-specific: `pystray` on Windows/Linux, `rumps` on macOS)
  - Four states: green (all good), yellow (degraded), red (disconnected), gray (idle)
  - Hover tooltip: network name, peer count, connection type
  - Right-click menu: Open dashboard, Share service, Quit
  - Falls back to terminal status line if GUI not available (Termux, headless)

- [ ] 10.4 **Friendly logging** — `common/logging.py`
  - Dual output: machine logs (JSON) + human logs (colored terminal)
  - Human log format: `[time] [icon] Plain language message`
  - Hide technical details unless `--verbose`
  - Examples:
    - `12:34:56 ✅ Connected to "My Network" — 3 peers online`
    - `12:35:01 ⚠️ Direct connection to Alice failed — using relay (slightly slower)`
    - `12:35:10 ℹ️ Bob shared a new service: Minecraft (port 25565)`

- [ ] 10.5 **Web panel user-friendly labels**
  - All technical labels replaced with plain language throughout web templates
  - Consistent terminology: use the terminology map from DESIGN.md §11.2
  - Contextual help `?` icon on every page
  - "Having trouble?" link opens the troubleshooting wizard
  - Progressive disclosure: advanced options hidden behind "Show advanced" toggle

- [ ] 10.6 **One-click workflows in web panels**
  - "Share a service" button: pick type → enter port → done
  - "Connect to a service" button: browse available services → click → mapped
  - "Invite someone" button: generates shareable invite code
  - "Test my connection" button: runs diagnostics, shows simple pass/fail
  - "Fix connection issues" button: guided step-by-step troubleshooting

---

## Phase 11 — CLI & UX Polish

- [ ] 11.1 Rich status display: `localnetwork-cli status`
  - Show online/offline
  - List networks and peer count
  - Tunnel states (CONNECTED vs RELAY vs CONNECTING)
  - Virtual IP

- [ ] 11.2 Colored/logged output with levels: `--verbose`, `--quiet`
- [ ] 11.3 Daemon mode: `localnetwork-client --daemon` (fork to background, PID file)
- [ ] 11.4 `--version` flag
- [ ] 11.5 Configuration file: `~/.localnetwork/config.yaml` or `.env` format
- [ ] 11.6 Log to file: `~/.localnetwork/client.log`, `~/.localnetwork/server.log`
