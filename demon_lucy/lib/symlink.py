from __future__ import annotations

import os
import stat
import tempfile


def replace_symlink_atomic(path: str, target: str, *, expected_target: str) -> bool:
    """Retarget an existing link without an unlink gap or following its target.

    Recheck its identity before replacement; this detects intervening edits but
    does not lock out external writers. Return False if the link has changed.
    """
    try:
        identity = os.lstat(path)
        if not stat.S_ISLNK(identity.st_mode) or os.readlink(path) != expected_target:
            return False
    except FileNotFoundError:
        return False

    with tempfile.TemporaryDirectory(
        prefix=".lucy-link-", dir=os.path.dirname(path) or "."
    ) as temporary_dir:
        temporary = os.path.join(temporary_dir, ".link")
        os.symlink(target, temporary)
        try:
            current = os.lstat(path)
            if (
                (
                    current.st_dev,
                    current.st_ino,
                    current.st_mtime_ns,
                    current.st_ctime_ns,
                )
                != (
                    identity.st_dev,
                    identity.st_ino,
                    identity.st_mtime_ns,
                    identity.st_ctime_ns,
                )
                or not stat.S_ISLNK(current.st_mode)
                or os.readlink(path) != expected_target
            ):
                return False
        except FileNotFoundError:
            return False
        os.replace(temporary, path)
    return True
