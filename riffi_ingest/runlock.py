"""One fetch at a time. Two runs at once would double the request rate to each site (each process has its
own per-site spacing) and could split one event into two stories. The lock is an OS file lock, so it is
released automatically if the process dies - no stale lock to clean up by hand."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def run_lock(path: str | Path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+b")  # noqa: SIM115 - held open for the lock's lifetime
    try:
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise AlreadyRunning(f"another fetch is already running (lock: {path})") from exc
        yield
    finally:
        handle.close()  # closing the file releases the lock on both systems
