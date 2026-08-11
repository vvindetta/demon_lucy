from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

from demon_lucy.modules.research_map.documents import ResearchMapError


def _ensure_safe_parent(path: Path) -> None:
    if path.parent.is_symlink():
        raise ResearchMapError(f"target directory must not be a symlink: {path.parent}")
    if not path.parent.is_dir():
        raise ResearchMapError(f"target directory must be a directory: {path.parent}")


def _ensure_safe_target(path: Path) -> None:
    if path.is_symlink():
        raise ResearchMapError(f"target must not be a symlink: {path}")
    _ensure_safe_parent(path)
    if path.exists() and not path.is_file():
        raise ResearchMapError(f"target must be a regular file: {path}")


def atomic_write_text_if_changed(path: Path, content: str) -> bool:
    _ensure_safe_target(path)
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return False

    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return True


def publish_exclusive_text(path: Path, content: str, *, mode: int) -> None:
    _ensure_safe_target(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.link(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def atomic_copy(source: Path, target: Path, *, overwrite: bool) -> bool:
    if source.is_symlink() or not source.is_file():
        raise ResearchMapError(f"source must be a regular non-symlink file: {source}")

    created_parent = False
    if not target.parent.exists():
        if target.parent.parent.is_symlink() or not target.parent.parent.is_dir():
            raise ResearchMapError(
                f"target parent must be a safe directory: {target.parent.parent}"
            )
        target.parent.mkdir()
        created_parent = True
    try:
        _ensure_safe_target(target)
        if overwrite:
            if not target.is_file():
                raise ResearchMapError(
                    f"mutable target must already be a regular file: {target}"
                )
            mode = stat.S_IMODE(target.stat().st_mode)
        else:
            if os.path.lexists(target):
                raise FileExistsError(target)
            mode = 0o644

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
        )
        temporary = Path(temporary_name)
        try:
            with source.open("rb") as input_handle, os.fdopen(
                descriptor, "wb"
            ) as output_handle:
                descriptor = -1
                while chunk := input_handle.read(1024 * 1024):
                    output_handle.write(chunk)
                output_handle.flush()
                os.fsync(output_handle.fileno())
            os.chmod(temporary, mode)
            if overwrite:
                os.replace(temporary, target)
            else:
                os.link(temporary, target)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
    except BaseException:
        if created_parent:
            remove_empty_directory(target.parent)
        raise
    return True


def remove_empty_directory(path: Path) -> None:
    try:
        path.rmdir()
    except FileNotFoundError:
        return
    except OSError:
        if path.exists() and not any(path.iterdir()):
            raise
