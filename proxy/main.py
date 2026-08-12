"""Reverse proxy CLI entry point.

Provides ``localnetwork-proxy`` command.

Usage::

    localnetwork-proxy --config proxy.yaml
    localnetwork-proxy --validate-config proxy.yaml
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import threading
from typing import Optional

from proxy.config import load_config, ProxyConfig
from proxy.master import MasterProcess

__version__ = "0.1.0"

log = logging.getLogger("localnetwork.proxy")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="localnetwork-proxy",
        description="LocalNetwork Ecosystem — Reverse Proxy",
    )
    parser.add_argument(
        "--config", "-c",
        default="proxy.yaml",
        help="path to proxy configuration file (default: proxy.yaml)",
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=0,
        help="number of worker processes (default: auto/CPU count)",
    )
    parser.add_argument(
        "--validate-config",
        action="store_true",
        help="parse and validate the config file, then exit",
    )
    parser.add_argument(
        "--version", "-V",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser


def main(argv: Optional[list] = None) -> int:
    """Main entry point for the reverse proxy."""
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Load config
    try:
        config = load_config(args.config)
    except FileNotFoundError:
        print(f"Error: config file not found: {args.config}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: failed to parse config: {exc}", file=sys.stderr)
        return 1

    # Override worker count from CLI
    if args.workers:
        config.workers = args.workers

    # Validate only
    if args.validate_config:
        print(f"Configuration is valid ({len(config.upstreams)} upstream(s), "
              f"{len(config.locations)} location(s), "
              f"workers={config.workers or 'auto'})")
        return 0

    # Start the admin web panel (if enabled)
    if config.admin_port > 0:
        _start_admin_panel(config)

    # Start the reverse proxy
    master = MasterProcess(config)
    try:
        master.start()
    except KeyboardInterrupt:
        log.info("interrupted — shutting down")
        master.shutdown()
    except Exception as exc:
        log.error("fatal: %r", exc)
        return 1
    return 0


def _start_admin_panel(config: ProxyConfig) -> None:
    """Start the admin web panel in a background daemon thread."""
    from aiohttp import web

    from proxy.status import StatusCollector
    from proxy.web.app import create_app

    def run_admin() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        app = create_app(status_collector=StatusCollector(), config=config)
        runner = web.AppRunner(app)
        loop.run_until_complete(runner.setup())
        site = web.TCPSite(runner, "0.0.0.0", config.admin_port)
        loop.run_until_complete(site.start())
        log.info("proxy admin panel listening on 0.0.0.0:%d", config.admin_port)
        try:
            loop.run_forever()
        finally:
            loop.run_until_complete(runner.cleanup())
            loop.close()

    thread = threading.Thread(target=run_admin, daemon=True, name="lnproxy-admin")
    thread.start()


if __name__ == "__main__":
    sys.exit(main())
