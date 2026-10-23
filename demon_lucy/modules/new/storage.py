from __future__ import annotations

import os
import stat
from dataclasses import dataclass

from demon_lucy.lib.file_open import open_file_no_follow
from demon_lucy.lib.operating_system import OperatingSystem


@dataclass(frozen=True)
class CreatedFile:
    path: str
    content: bytes
    identity: os.stat_result


def rollback_created_file(created: CreatedFile, system: OperatingSystem) -> bool:
    """Remove only our still-unchanged regular file; preserve outside edits."""

    def matches(current: os.stat_result) -> bool:
        return stat.S_ISREG(current.st_mode) and (
            current.st_dev,
            current.st_ino,
            current.st_mtime_ns,
            current.st_size,
        ) == (
            created.identity.st_dev,
            created.identity.st_ino,
            created.identity.st_mtime_ns,
            created.identity.st_size,
        )

    try:
        if not matches(os.lstat(created.path)):
            return False
        fd = open_file_no_follow(created.path, os.O_RDONLY, operating_system=system)
        with os.fdopen(fd, "rb") as handle:
            if not matches(os.fstat(handle.fileno())):
                return False
            if handle.read() != created.content:
                return False
        if not matches(os.lstat(created.path)):
            return False
        os.unlink(created.path)
    except FileNotFoundError:
        return True
    return True
