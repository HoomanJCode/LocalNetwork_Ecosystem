"""Interactive setup wizard for first-time users.

DESIGN.md §10.1: On first launch (or when no config exists), guides the user
through a simple 3-4 question wizard to get started.

Supports four paths:
* Join an existing network
* Create a new network
* Set up a hub (for hub-and-spoke topology)
* Set up a proxy
"""

from __future__ import annotations

import os
import sys
from typing import Optional


def run_setup_wizard(identity_dir: str) -> dict:
    """Run the interactive setup wizard.

    Returns:
        A dict of configuration overrides to apply.
    """
    print()
    print("╔══════════════════════════════════════════╗")
    print("║   🚀 Welcome to LocalNetwork!            ║")
    print("║   Let's get you set up in a few steps.   ║")
    print("╚══════════════════════════════════════════╝")
    print()

    # Step 1: What do you want to do?
    print("What would you like to do?")
    print("  [1] Join an existing network")
    print("  [2] Create a new virtual network")
    print("  [3] Set up a hub (for hub-and-spoke)")
    print("  [4] Just start the client (configure later)")

    choice = _prompt("Enter choice", default="1", choices=["1", "2", "3", "4"])
    overrides: dict = {}

    if choice == "1":
        overrides.update(_wizard_join())
    elif choice == "2":
        overrides.update(_wizard_create())
    elif choice == "3":
        overrides.update(_wizard_hub())
    else:
        overrides.update(_wizard_minimal())

    # Step N: Server address
    print()
    server = _prompt(
        "Server address (host:port)",
        default="localhost:54000",
    )
    if ":" in server:
        host, _, port = server.rpartition(":")
        overrides["server_host"] = host
        overrides["server_port"] = int(port)
    else:
        overrides["server_host"] = server

    # Step N+1: TUN mode
    from client.platform_detection import detect_platform

    caps = detect_platform()
    if caps.tun_mode_enabled:
        print()
        print("TUN virtual network interface is available on your system.")
        enable_tun = _prompt_yes_no("Enable virtual LAN (TUN mode)?", default=True)
        overrides["tun_enabled"] = enable_tun
    else:
        overrides["tun_enabled"] = False
        if not caps.is_termux:
            print()
            print("⚠️  TUN mode is not available on this system.")
            print("   You can still use service exposure mode to share individual services.")

    # Summary
    print()
    print("═══ Configuration Summary ═══")
    for key, value in sorted(overrides.items()):
        print(f"  {key}: {value}")
    print()

    if _prompt_yes_no("Save this configuration?", default=True):
        return overrides
    else:
        print("Starting with defaults. Run the wizard again with: localnetwork-client --setup")
        return {}


def _wizard_join() -> dict:
    """Wizard path: join an existing network."""
    print()
    print("─── Join a Network ───")
    network_id = _prompt("Network ID (ask the network creator for this)")
    password = _prompt("Network password (if any)", default="", secret=True)
    return {"auto_join_network": network_id, "auto_join_password": password}


def _wizard_create() -> dict:
    """Wizard path: create a new network."""
    print()
    print("─── Create a Network ───")
    name = _prompt("Network name (e.g., 'my-home-network')")
    password = _prompt(
        "Network password (share this with people you invite)",
        default="",
    )
    print()
    print("Topology (how peers connect to each other):")
    print("  [1] Mesh — everyone connects to everyone (default)")
    print("  [2] Hub-and-spoke — all traffic through a central hub")
    print("  [3] Gateway — bridge to a physical LAN")
    topo_choice = _prompt("Topology", default="1", choices=["1", "2", "3"])
    topology_map = {"1": "mesh", "2": "hub_and_spoke", "3": "gateway"}
    return {
        "auto_create_network": name,
        "auto_create_password": password,
        "auto_create_topology": topology_map[topo_choice],
    }


def _wizard_hub() -> dict:
    """Wizard path: set up a hub."""
    print()
    print("─── Set Up a Hub ───")
    print("A hub relays traffic between all spoke clients.")
    print("Make sure this machine has a stable network connection.")
    network_name = _prompt("Network name", default="hub-network")
    password = _prompt("Network password", default="")
    return {
        "auto_create_network": network_name,
        "auto_create_password": password,
        "auto_create_topology": "hub_and_spoke",
        "is_hub": True,
    }


def _wizard_minimal() -> dict:
    """Wizard path: just start the client."""
    return {}


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------
def _prompt(
    question: str,
    default: str = "",
    choices: Optional[list] = None,
    secret: bool = False,
) -> str:
    """Prompt the user for input with an optional default and validation."""
    hint = f" [{default}]" if default else ""
    choices_hint = f" ({'/'.join(choices)})" if choices else ""
    full = f"> {question}{choices_hint}{hint}: "

    while True:
        try:
            if secret:
                import termios
                import tty

                # Simple password prompt without echo
                fd = sys.stdin.fileno()
                old = termios.tcgetattr(fd)
                try:
                    tty.setraw(fd)
                    sys.stdout.write(full)
                    sys.stdout.flush()
                    result = ""
                    while True:
                        ch = sys.stdin.read(1)
                        if ch in ("\n", "\r"):
                            break
                        if ch == "\x7f":  # backspace
                            if result:
                                result = result[:-1]
                                sys.stdout.write("\b \b")
                        else:
                            result += ch
                            sys.stdout.write("*")
                    sys.stdout.write("\n")
                finally:
                    termios.tcsetattr(fd, termios.TCSADRAIN, old)
            else:
                result = input(full).strip()

            if not result and default:
                return default
            if not result:
                continue
            if choices and result not in choices:
                print(f"  Please enter one of: {', '.join(choices)}")
                continue
            return result
        except (KeyboardInterrupt, EOFError):
            print()
            sys.exit(0)
        except (ImportError, termios.error):
            # Fallback for environments without termios
            result = input(full).strip()
            if not result and default:
                return default
            if choices and result not in choices:
                print(f"  Please enter one of: {', '.join(choices)}")
                continue
            return result


def _prompt_yes_no(question: str, default: bool = True) -> bool:
    """Prompt for a yes/no answer."""
    yn = "Y/n" if default else "y/N"
    result = _prompt(f"{question} ({yn})", default="y" if default else "n")
    return result.lower() in ("y", "yes")


# ---------------------------------------------------------------------------
# Config file generation
# ---------------------------------------------------------------------------
def generate_config_file(overrides: dict, path: str = "") -> str:
    """Generate a config file from wizard overrides.

    Returns the path to the generated config file.
    """
    import yaml

    if not path:
        path = os.path.expanduser("~/.localnetwork/config.yaml")

    os.makedirs(os.path.dirname(path), exist_ok=True)

    config = {
        "server": {
            "host": overrides.get("server_host", "localhost"),
            "port": overrides.get("server_port", 54000),
        },
        "client": {
            "tun_enabled": overrides.get("tun_enabled", False),
        },
    }

    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)

    return path


__all__ = ["run_setup_wizard", "generate_config_file"]
