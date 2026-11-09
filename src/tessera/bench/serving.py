"""Running ``tessera serve`` for the benchmark, and what its processes use."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from tessera.config import load_config


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@contextmanager
def serving(path: Path, *, workers: int = 1) -> Iterator[tuple[str, subprocess.Popen]]:
    """``tessera serve`` on a free port for the organization in ``path``, until the block ends;
    yields its address and process."""
    import httpx

    config_path = path / "tessera.toml"
    port = free_port()
    # The server reads the same ledger as the steps before it, wherever it is.
    env = {**os.environ, "TESSERA_LEDGER_URL": load_config(config_path).ledger_url}
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tessera",
            "--config",
            str(config_path),
            "serve",
            "--port",
            str(port),
            "--workers",
            str(workers),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        with httpx.Client(base_url=base, timeout=10) as client:
            for _ in range(1200):
                if server.poll() is not None:
                    raise RuntimeError("tessera serve stopped before it was ready")
                try:
                    if client.get("/api/v1/health/ready").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError("tessera serve did not start")
        if workers > 1:  # every process up, not only the first to answer
            for _ in range(600):
                if len(processes(server.pid)) > workers:
                    break
                time.sleep(0.1)
            time.sleep(3.0)  # and through its start, before it takes requests
        yield base, server
    finally:
        server.terminate()
        try:
            server.wait(timeout=30)
        except subprocess.TimeoutExpired:
            server.kill()


def processes(pid: int) -> list[int]:
    """The process and everything it started (a server's worker processes)."""
    try:
        table = subprocess.check_output(["ps", "-A", "-o", "pid=,ppid="], text=True)
    except (OSError, subprocess.SubprocessError):
        return [pid]
    children: dict[int, list[int]] = {}
    for line in table.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, todo = [], [pid]
    while todo:
        current = todo.pop()
        found.append(current)
        todo.extend(children.get(current, []))
    return found


def _ps(pids: list[int], field: str) -> list[str]:
    try:
        out = subprocess.check_output(
            ["ps", "-o", f"{field}=", "-p", ",".join(str(p) for p in pids)], text=True
        )
    except (OSError, subprocess.SubprocessError):
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def memory_mb(pid: int) -> float | None:
    """Resident memory of the server and its processes together."""
    values = _ps(processes(pid), "rss")
    try:
        return round(sum(int(v) for v in values) / 1024, 1) if values else None
    except ValueError:
        return None


def cpu_seconds(pid: int) -> float | None:
    """Processor time used so far by the server and its processes together."""
    values = _ps(processes(pid), "time")
    if not values:
        return None
    total = 0.0
    try:
        for value in values:  # [[days-]hours:]minutes:seconds[.fraction]
            days, _, clock = value.rpartition("-")
            seconds = 0.0
            for part in clock.split(":"):
                seconds = seconds * 60 + float(part)
            total += seconds + (int(days) * 86400 if days else 0)
    except ValueError:
        return None
    return total


__all__ = ["cpu_seconds", "free_port", "memory_mb", "processes", "serving"]
