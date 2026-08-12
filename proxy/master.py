"""Reverse proxy master process (DESIGN.md §7.1).

Responsibilities:
* Read and validate config
* Bind listen sockets (SO_REUSEPORT)
* Spawn and monitor worker processes
* Handle SIGHUP (graceful reload) and SIGINT/SIGTERM (shutdown)
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import signal
import socket
import sys
import time
from typing import Any, Dict, List, Optional

from proxy.config import ProxyConfig

log = logging.getLogger("localnetwork.proxy.master")

SO_REUSEPORT = 15  # not always in socket module


class MasterProcess:
    """Manages worker subprocesses for the reverse proxy."""

    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self._workers: List[multiprocessing.Process] = []
        self._listen_sockets: Dict[int, socket.socket] = {}  # port → socket
        self._running = False

    def start(self) -> None:
        """Bind sockets and spawn workers."""
        self._running = True

        # Bind HTTP/HTTPS listen sockets
        for port in self.config.http:
            self._bind(port, ssl=False)
        for port in self.config.https:
            self._bind(port, ssl=True)

        # Determine worker count
        worker_count = self.config.workers or os.cpu_count() or 1
        log.info("starting %d worker(s), %d listen socket(s)", worker_count, len(self._listen_sockets))

        # Spawn workers
        for i in range(worker_count):
            self._spawn_worker(i)

        # Set up signal handlers
        signal.signal(signal.SIGHUP, self._handle_reload)
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        log.info("master process ready (pid=%d)", os.getpid())

        # Monitor workers
        while self._running:
            self._reap_workers()
            time.sleep(1)

    def _bind(self, port: int, ssl: bool = False) -> None:
        """Bind a listen socket on *port* with SO_REUSEPORT."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, SO_REUSEPORT, 1)
        except (OSError, AttributeError):
            pass  # SO_REUSEPORT not available on all platforms
        sock.bind(("0.0.0.0", port))
        sock.listen(self.config.worker_connections)
        sock.setblocking(False)
        self._listen_sockets[port] = sock
        log.info("listening on 0.0.0.0:%d (%s)", port, "https" if ssl else "http")

    def _spawn_worker(self, worker_id: int) -> None:
        """Fork a new worker process."""
        proc = multiprocessing.Process(
            target=self._worker_entry,
            args=(worker_id, self.config, dict(self._listen_sockets)),
            name=f"lnproxy-worker-{worker_id}",
        )
        proc.daemon = False
        proc.start()
        self._workers.append(proc)
        log.info("spawned worker %d (pid=%d)", worker_id, proc.pid)

    @staticmethod
    def _worker_entry(
        worker_id: int,
        config: ProxyConfig,
        listen_sockets: Dict[int, socket.socket],
    ) -> None:
        """Entry point for worker subprocess."""
        from proxy.worker import WorkerProcess

        worker = WorkerProcess(worker_id, config, listen_sockets)
        try:
            worker.run()
        except KeyboardInterrupt:
            pass
        except Exception as exc:
            log.error("worker %d crashed: %r", worker_id, exc)
            sys.exit(1)

    def _reap_workers(self) -> None:
        """Restart any workers that have crashed."""
        for i, proc in enumerate(self._workers):
            if not proc.is_alive():
                log.warning("worker %d (pid=%d) died — restarting", i, proc.pid)
                self._workers[i] = multiprocessing.Process(
                    target=self._worker_entry,
                    args=(i, self.config, dict(self._listen_sockets)),
                )
                self._workers[i].start()

    def reload(self) -> None:
        """Graceful reload: spawn new workers with new config, kill old ones."""
        log.info("reloading configuration…")
        # New workers with new config
        old_workers = self._workers
        self._workers = []
        for i in range(len(old_workers)):
            self._spawn_worker(i)
        # Gracefully terminate old workers
        for proc in old_workers:
            proc.terminate()
            proc.join(timeout=10)
            if proc.is_alive():
                proc.kill()

    def shutdown(self) -> None:
        """Gracefully stop all workers and close listen sockets."""
        log.info("shutting down…")
        self._running = False

        # Tell workers to stop
        for proc in self._workers:
            proc.terminate()
        for proc in self._workers:
            proc.join(timeout=10)
            if proc.is_alive():
                proc.kill()

        # Close listen sockets
        for sock in self._listen_sockets.values():
            try:
                sock.close()
            except OSError:
                pass

        log.info("master process stopped")

    def _handle_reload(self, signum, frame) -> None:
        self.reload()

    def _handle_shutdown(self, signum, frame) -> None:
        self.shutdown()
        sys.exit(0)
