#!/usr/bin/env bash
# =============================================================================
# LocalNetwork Ecosystem — Development Runner (Linux / macOS)
# =============================================================================
#
# Usage:
#   ./scripts/run_dev.sh [command]
#
# Commands:
#   setup       Create virtualenv, install deps + this package (editable)
#   test        Run all tests
#   server      Start the mediation server on localhost:54000
#   client      Start a client daemon connected to localhost:54000
#   cli         Run the management CLI (pass args after `--`)
#   demo        Full demo: server + 2 clients in separate terminals
#   clean       Remove virtualenv and __pycache__ dirs
#   help        Show this message
#
# Examples:
#   ./scripts/run_dev.sh setup
#   ./scripts/run_dev.sh test
#   ./scripts/run_dev.sh server
#   ./scripts/run_dev.sh demo          # opens 3 terminal windows
#   ./scripts/run_dev.sh cli -- create mynet --password secret
#
# =============================================================================

set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

VENV="$ROOT/.venv"
PYTHON="${VENV}/bin/python"
PIP="${VENV}/bin/pip"
# `python3` may not exist on every system (e.g. some Windows/Git Bash setups)
PY_BIN="${PYTHON3:-}"
if [ -z "$PY_BIN" ]; then
    command -v python3 >/dev/null 2>&1 && PY_BIN=python3 || PY_BIN=python
fi

# ── helpers ──────────────────────────────────────────────────────────────

_ensure_venv() {
    if [ ! -f "$PYTHON" ]; then
        echo "🔧 Creating virtual environment..."
        "$PY_BIN" -m venv "$VENV"
        echo "📦 Installing dependencies + package (editable)..."
        "$PIP" install --upgrade pip -q
        "$PIP" install -r requirements.txt -r requirements-dev.txt -q
        "$PIP" install -e . -q
        echo "✅ Virtual environment ready."
        echo "   Console commands: localnetwork-server, localnetwork-client, localnetwork-cli"
    fi
}

_heading() {
    echo ""
    echo "════════════════════════════════════════════════════════════════"
    echo "  $1"
    echo "════════════════════════════════════════════════════════════════"
    echo ""
}

# ── commands ─────────────────────────────────────────────────────────────

cmd_setup() {
    _heading "SETUP"
    if [ -f "$PYTHON" ]; then
        echo "Virtual environment already exists at .venv"
        echo "Run './scripts/run_dev.sh clean' first to reinstall."
        exit 0
    fi
    _ensure_venv
    echo ""
    echo "Setup complete! Try:"
    echo "  ./scripts/run_dev.sh test"
    echo "  ./scripts/run_dev.sh demo"
}

cmd_test() {
    _ensure_venv
    _heading "RUNNING TESTS"
    "$PYTHON" -m pytest tests/ -v --tb=short
    echo ""
    echo "✅ All tests passed."
}

cmd_server() {
    _ensure_venv
    _heading "STARTING MEDIATION SERVER"
    echo "Server listening on tcp://0.0.0.0:54000"
    echo "Press Ctrl+C to stop."
    echo ""
    "$VENV/bin/localnetwork-server" --host 0.0.0.0 --port 54000 --log-level INFO
}

cmd_client() {
    _ensure_venv
    _heading "STARTING VPN CLIENT DAEMON"
    echo "Client connecting to localhost:54000"
    echo "Press Ctrl+C to stop."
    echo ""
    "$VENV/bin/localnetwork-client" --server localhost:54000 --log-level INFO
}

cmd_cli() {
    _ensure_venv
    "$VENV/bin/localnetwork-cli" --host localhost --port 54000 "$@"
}

cmd_demo() {
    _ensure_venv
    _heading "LAUNCHING DEMO — Server + 2 Clients"

    mkdir -p /tmp/ln-demo/identity-a /tmp/ln-demo/identity-b

    # Terminal 1: Server
    if command -v gnome-terminal &>/dev/null; then
        gnome-terminal -- bash -c \
            "cd '$ROOT' && '$VENV/bin/localnetwork-server' --host 0.0.0.0 --port 54000 --log-level INFO; exec bash"
    elif command -v xterm &>/dev/null; then
        xterm -T "LN Server" -e \
            "cd '$ROOT' && '$VENV/bin/localnetwork-server' --host 0.0.0.0 --port 54000 --log-level INFO" &
    elif command -v osascript &>/dev/null; then
        osascript -e "tell app \"Terminal\"
            do script \"cd '$ROOT' && '$VENV/bin/localnetwork-server' --host 0.0.0.0 --port 54000 --log-level INFO\"
        end tell"
    else
        echo "⚠️  No terminal emulator found. Starting server in background..."
        "$VENV/bin/localnetwork-server" --host 0.0.0.0 --port 54000 --log-level INFO &
        sleep 2
    fi

    sleep 1

    # Terminal 2: Client A
    if command -v gnome-terminal &>/dev/null; then
        gnome-terminal -- bash -c \
            "cd '$ROOT' && '$VENV/bin/localnetwork-client' --server localhost:54000 --identity-dir /tmp/ln-demo/identity-a --log-level INFO; exec bash"
    elif command -v xterm &>/dev/null; then
        xterm -T "LN Client A" -e \
            "cd '$ROOT' && '$VENV/bin/localnetwork-client' --server localhost:54000 --identity-dir /tmp/ln-demo/identity-a --log-level INFO" &
    elif command -v osascript &>/dev/null; then
        osascript -e "tell app \"Terminal\"
            do script \"cd '$ROOT' && '$VENV/bin/localnetwork-client' --server localhost:54000 --identity-dir /tmp/ln-demo/identity-a --log-level INFO\"
        end tell"
    fi

    sleep 1

    # Terminal 3: Client B
    if command -v gnome-terminal &>/dev/null; then
        gnome-terminal -- bash -c \
            "cd '$ROOT' && '$VENV/bin/localnetwork-client' --server localhost:54000 --identity-dir /tmp/ln-demo/identity-b --log-level INFO; exec bash"
    elif command -v xterm &>/dev/null; then
        xterm -T "LN Client B" -e \
            "cd '$ROOT' && '$VENV/bin/localnetwork-client' --server localhost:54000 --identity-dir /tmp/ln-demo/identity-b --log-level INFO" &
    elif command -v osascript &>/dev/null; then
        osascript -e "tell app \"Terminal\"
            do script \"cd '$ROOT' && '$VENV/bin/localnetwork-client' --server localhost:54000 --identity-dir /tmp/ln-demo/identity-b --log-level INFO\"
        end tell"
    fi

    echo ""
    echo "Demo launched!"
    echo "  Server   → tcp://localhost:54000"
    echo "  Client A → identity in /tmp/ln-demo/identity-a"
    echo "  Client B → identity in /tmp/ln-demo/identity-b"
    echo ""
    echo "In a 4th terminal, try:"
    echo "  ./scripts/run_dev.sh cli -- create mynet --password secret"
    echo "  ./scripts/run_dev.sh cli -- list"
    echo "  ./scripts/run_dev.sh cli -- status"
    echo ""
}

cmd_clean() {
    _heading "CLEANING UP"
    rm -rf "$VENV"
    find "$ROOT" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find "$ROOT" -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
    find "$ROOT" -type f -name '*.pyc' -delete 2>/dev/null || true
    echo "✅ Virtualenv and caches removed."
}

cmd_help() {
    echo "LocalNetwork Ecosystem — Development Runner"
    echo ""
    echo "Usage: ./scripts/run_dev.sh [command]"
    echo ""
    echo "Commands:"
    echo "  setup       Create virtualenv, install dependencies"
    echo "  test        Run all tests"
    echo "  server      Start the mediation server"
    echo "  client      Start a VPN client daemon"
    echo "  cli [args]  Run the management CLI"
    echo "  demo        Full demo: server + 2 clients in separate terminals"
    echo "  clean       Remove virtualenv and caches"
    echo "  help        Show this message"
    echo ""
    echo "Examples:"
    echo "  ./scripts/run_dev.sh setup    # first-time setup"
    echo "  ./scripts/run_dev.sh test     # run all tests"
    echo "  ./scripts/run_dev.sh demo     # full 3-terminal demo"
    echo ""
    echo "After setup + demo, in a 4th terminal:"
    echo "  ./scripts/run_dev.sh cli -- create mynet --password secret"
    echo "  ./scripts/run_dev.sh cli -- join <network-id> --password secret"
    echo "  ./scripts/run_dev.sh cli -- list"
    echo "  ./scripts/run_dev.sh cli -- status"
}

# ── main ─────────────────────────────────────────────────────────────────

case "${1:-help}" in
    setup)    cmd_setup ;;
    test)     cmd_test ;;
    server)   cmd_server ;;
    client)   cmd_client ;;
    cli)      shift; [ "${1:-}" = "--" ] && shift; cmd_cli "$@" ;;
    demo)     cmd_demo ;;
    clean)    cmd_clean ;;
    help|--help|-h) cmd_help ;;
    *)
        echo "Unknown command: $1"
        echo "Run './scripts/run_dev.sh help' for usage."
        exit 1
        ;;
esac
