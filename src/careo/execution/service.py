"""Managed local services: start a trusted command, wait for readiness, stop it.

Like one-shot runs, a service gets its own process group, which is SIGKILLed
on stop. Stop is idempotent and is also called from an atexit hook and from a
SIGTERM handler installed by the runner, so an interrupted run does not leave
services behind. This is not a sandbox.
"""

from __future__ import annotations

import atexit
import contextlib
import os
import signal
import socket
import subprocess
import time
import weakref
from collections.abc import Sequence
from pathlib import Path

from careo.adapters.http import Endpoint, request

_LIVE: weakref.WeakSet[ManagedService] = weakref.WeakSet()


class ServiceError(Exception):
    pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class ManagedService:
    def __init__(
        self,
        name: str,
        argv: Sequence[str],
        cwd: Path,
        env: dict[str, str],
        endpoint: Endpoint,
        ready_path: str,
        ready_timeout: float,
        log_path: Path,
    ) -> None:
        self.name = name
        self.argv = list(argv)
        self.cwd = cwd
        self.env = env
        self.endpoint = endpoint
        self.ready_path = ready_path
        self.ready_timeout = ready_timeout
        self.log_path = log_path
        self.proc: subprocess.Popen[bytes] | None = None

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    def start(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        log = self.log_path.open("ab")
        try:
            self.proc = subprocess.Popen(
                self.argv,
                cwd=self.cwd,
                env=self.env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=os.name == "posix",
                close_fds=True,
            )
        except OSError as exc:
            raise ServiceError(f"{self.name}: cannot start {self.argv[0]}: {exc}") from None
        finally:
            log.close()
        _LIVE.add(self)
        deadline = time.monotonic() + self.ready_timeout
        last = ""
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                self.stop()
                raise ServiceError(
                    f"{self.name}: exited with code {self.proc.returncode} before becoming ready "
                    f"(log: {self.log_path})"
                )
            r = request(
                self.endpoint,
                "GET",
                self.endpoint.base_path + self.ready_path,
                timeout_seconds=1.0,
                max_response_bytes=65536,
            )
            if r.status == "ok" and r.code is not None and 200 <= r.code < 300:
                return
            last = r.error or f"HTTP {r.code}"
            time.sleep(0.05)
        self.stop()
        raise ServiceError(
            f"{self.name}: not ready within {self.ready_timeout}s ({last}); log: {self.log_path}"
        )

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        proc = self.proc
        if proc is None:
            return
        if os.name == "posix":
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(proc.pid, signal.SIGKILL)
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=10)
        _LIVE.discard(self)


def stop_all() -> None:
    for svc in list(_LIVE):
        svc.stop()


atexit.register(stop_all)
