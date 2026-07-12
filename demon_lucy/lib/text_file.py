from __future__ import annotations

import os
import stat
import tempfile


def detect_newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    if "\n" in text:
        return "\n"
    if "\r" in text:
        return "\r"
    return "\n"


def normalize_newlines(text: str, newline: str) -> str:
    if newline not in {"\n", "\r\n", "\r"}:
        raise ValueError("unsupported newline")
    return text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)


def write_text_atomic(path: str, text: str) -> None:
    directory = os.path.dirname(path) or "."
    mode: int | None = None
    try:
        mode = stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    except OSError:
        pass

    fd, temp_path = tempfile.mkstemp(
        prefix="." + os.path.basename(path) + ".",
        suffix=".tmp",
        dir=directory,
    )
    try:
        if mode is not None:
            os.chmod(temp_path, mode)
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            fd = -1
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
