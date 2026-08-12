"""Reverse proxy CLI entry point.

Provides ``localnetwork-proxy`` command.

Usage::

    localnetwork-proxy --config proxy.yaml
    localnetwork-proxy --validate-config proxy.yaml
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
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


if __name__ == "__main__":
    sys.exit(main())
