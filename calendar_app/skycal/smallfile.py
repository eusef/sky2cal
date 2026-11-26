"""Reading a small file from the calendar's state folder safely.

Anything there could have been replaced (by a link to /dev/zero, a FIFO, a huge file), and the
calendar and the settings page (as root) both read from it, so a read must neither hang nor use
up memory. Shared by skycal and skyadmin; imports nothing heavy."""
from __future__ import annotations

import os
import stat
from pathlib import Path


def read_capped(path: Path, limit: int) -> tuple[bytes, float]:
    """(the file's bytes, when it was written). Only a regular file, opened without following a
    link (O_NOFOLLOW) and without waiting for a writer (O_NONBLOCK, for a FIFO), and only when it
    holds at most `limit` bytes. Raises FileNotFoundError when there is none, OSError or
    ValueError for anything else."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as f:
        info = os.fstat(f.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"{Path(path).name} is not a regular file")
        if info.st_size > limit:
            raise ValueError(f"{Path(path).name} is too large")
        data = f.read(limit + 1)                  # the size can grow after the check
    if len(data) > limit:
        raise ValueError(f"{Path(path).name} is too large")
    return data, info.st_mtime
