"""
Cross-process single-instance guard.

Guarantees that only ONE bot process can run at a time. The lock is an OS-level
advisory lock on a lock file that is held for the entire process lifetime and
released automatically by the operating system when the process exits — even on
a crash or a forced kill. This makes it impossible for a restart to race into
two competing long-polling loops (which Telegram rejects with a 409 Conflict).

Works on Windows (msvcrt) and POSIX (fcntl). The acquired handle is kept in a
module-global so it is never garbage-collected / closed while the bot runs.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class AlreadyRunning(RuntimeError):
    """Raised when another instance already holds the lock."""


# Keep a reference for the whole process lifetime so the lock is never released.
_lock_handle = None


def acquire(lock_path: str = "./.bot.lock") -> None:
    """Acquire the exclusive single-instance lock or raise ``AlreadyRunning``."""
    global _lock_handle
    if _lock_handle is not None:
        return  # already acquired in this process

    p = Path(lock_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    f = open(p, "a+")

    try:
        if os.name == "nt":
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        f.close()
        raise AlreadyRunning(
            f"Another bot instance already holds the lock ({lock_path})."
        ) from exc

    # Record our PID for diagnostics (the lock itself is what matters).
    try:
        f.seek(0)
        f.truncate()
        f.write(str(os.getpid()))
        f.flush()
    except OSError:
        pass

    _lock_handle = f
    logger.info("Single-instance lock acquired (pid=%s, file=%s).", os.getpid(), lock_path)
