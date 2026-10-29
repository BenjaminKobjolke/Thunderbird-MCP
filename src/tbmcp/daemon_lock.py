"""Singleton lock for the local daemon process."""

from __future__ import annotations

import os
from pathlib import Path

from . import ipc


class DaemonLock:
    """Atomic "only one daemon" guarantee.

    Checking `DaemonInfo` is not enough: it is written *after* the sockets are bound,
    so two daemons started within the same second both see an empty advertisement and
    both bind. The add-on then attaches to one while `serve` talks to the other, and
    every tool reports "Thunderbird is not connected" with both processes healthy.

    `O_CREAT | O_EXCL` is atomic on every platform we target, so exactly one process
    wins the create. A lock left behind by a crash is detected by checking whether the
    recorded pid is still alive.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def acquire(cls) -> DaemonLock | None:
        path = ipc.state_dir() / "daemon.lock"
        for _ in range(2):
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if cls._holder_alive(path):
                    return None
                # Stale: the previous holder died without releasing it.
                try:
                    path.unlink()
                except OSError:
                    return None
                continue
            except OSError:
                return None
            try:
                os.write(fd, str(os.getpid()).encode("ascii"))
            finally:
                os.close(fd)
            ipc._restrict_permissions(path)
            return cls(path)
        return None

    @classmethod
    def acquire_forcibly(cls) -> DaemonLock:
        """Take the lock regardless of who holds it.

        Only for the recovery path: a live holder that never advertised is wedged, and
        refusing to start behind it would leave the bridge permanently down.
        """
        path = ipc.state_dir() / "daemon.lock"
        path.write_text(str(os.getpid()), encoding="ascii")
        ipc._restrict_permissions(path)
        return cls(path)

    @staticmethod
    def _holder_alive(path: Path) -> bool:
        try:
            pid = int(path.read_text(encoding="ascii").strip() or 0)
        except (OSError, ValueError):
            # An unreadable or half-written lock is not evidence of a live daemon.
            return False
        if pid <= 0:
            return False
        return ipc.DaemonInfo(
            version=ipc.PROTOCOL_VERSION, port=0, token="", pid=pid, profile=""
        ).is_alive()

    def holder(self) -> int | None:
        try:
            return int(self.path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        """Remove the lock, but only if it is still ours."""
        if self.holder() == os.getpid():
            try:
                self.path.unlink()
            except OSError:
                pass
