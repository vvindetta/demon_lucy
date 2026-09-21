from __future__ import annotations

import os
import stat
import tempfile


class SourceChangedError(OSError):
    """The destination no longer contains the expected source content."""


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


def write_text_atomic(
    path: str, text: str, *, expected_text: str | None = None
) -> None:
    write_bytes_atomic(
        path,
        text.encode("utf-8"),
        expected_content=(
            None if expected_text is None else expected_text.encode("utf-8")
        ),
    )


def write_bytes_atomic(
    path: str, content: bytes, *, expected_content: bytes | None = None
) -> None:
    """Replace a file, optionally rechecking its content just before replacement.

    The check detects intervening edits; it is not a lock against external writers.
    """
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
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if expected_content is not None:
            try:
                with open(path, "rb") as source:
                    current_content = source.read()
            except FileNotFoundError as exc:
                raise SourceChangedError(f"Source was removed: {path}") from exc
            if current_content != expected_content:
                raise SourceChangedError(f"Source changed: {path}")
        os.replace(temp_path, path)
    except Exception:
        if fd >= 0:
            os.close(fd)
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise
