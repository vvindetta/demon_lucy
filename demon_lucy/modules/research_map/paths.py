from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from demon_lucy.lib.path import path_inside_no_symlinks
from demon_lucy.modules.research_map.documents import NODE_FILENAME_RE, ResearchMapError

TMP_ROOT = Path("/tmp")
MAP_NAME_RE = re.compile(r"[\w]+(?:-[\w]+)*_map", re.UNICODE)


@dataclass(frozen=True)
class PutTarget:
    relative_path: Path
    overwrite: bool


def validate_map_name(value: str) -> str:
    name = value.strip()
    parts = Path(name).parts
    if (
        not parts
        or Path(name).is_absolute()
        or any(
            part.startswith(".") or not re.fullmatch(r"[\w-]+", part) for part in parts
        )
        or not MAP_NAME_RE.fullmatch(parts[-1])
    ):
        raise ResearchMapError(
            "map name must be a safe relative path ending in <name>_map"
        )
    return name


def resolve_root(value: str) -> Path:
    unresolved = Path(value).expanduser().absolute()
    if unresolved.is_symlink():
        raise ResearchMapError(f"research map root must not be a symlink: {unresolved}")
    if not unresolved.is_dir():
        raise ResearchMapError(
            f"research map root must be an existing directory: {unresolved}"
        )
    return unresolved.resolve()


def discover_map_dirs(root: Path) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for current, directories, _ in os.walk(root, followlinks=False):
        directories[:] = sorted(
            name
            for name in directories
            if not name.startswith(".") and not (Path(current) / name).is_symlink()
        )
        for name in directories[:]:
            candidate = Path(current) / name
            if MAP_NAME_RE.fullmatch(name):
                found[candidate.relative_to(root).as_posix()] = candidate
                directories.remove(name)
    return found


def resolve_map_dir(root: Path, name: str, *, must_exist: bool) -> Path:
    safe_name = validate_map_name(name)
    try:
        candidate = Path(path_inside_no_symlinks(str(root), safe_name))
    except ValueError as exc:
        raise ResearchMapError(str(exc)) from exc
    if must_exist and not candidate.is_dir():
        raise ResearchMapError(f"map does not exist: {candidate}")
    if not must_exist and os.path.lexists(candidate):
        raise ResearchMapError(f"map path already exists: {candidate}")
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root) or resolved == root:
        raise ResearchMapError(f"map escapes research map root: {candidate}")
    return resolved


def safe_tmp_file(value: str) -> Path:
    unresolved = Path(value).expanduser().absolute()
    if unresolved.is_symlink():
        raise ResearchMapError(f"source must not be a symlink: {unresolved}")
    try:
        source = unresolved.resolve(strict=True)
    except OSError as exc:
        raise ResearchMapError(f"cannot resolve source: {unresolved}: {exc}") from exc
    if not source.is_file():
        raise ResearchMapError(f"source must be a regular file: {source}")
    tmp_root = TMP_ROOT.resolve()
    try:
        source.relative_to(tmp_root)
    except ValueError as exc:
        raise ResearchMapError(f"source must be below {tmp_root}: {source}") from exc
    return source


def classify_put_target(value: str) -> PutTarget:
    relative = Path(value)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ResearchMapError(f"unsafe research map target: {value}")
    if relative.parts == ("index.md",):
        return PutTarget(relative_path=relative, overwrite=True)
    if (
        len(relative.parts) >= 2
        and relative.parts[0] == "b-nodes"
        and NODE_FILENAME_RE.fullmatch(relative.name)
    ):
        return PutTarget(relative_path=relative, overwrite=True)
    if (
        relative.parts[0] in {"index.md", "questions.md", "seed.md", "artifacts"}
        or (len(relative.parts) == 1 and relative.name in {"b-nodes", ".attach"})
        or any(part.startswith(".") and part != ".attach" for part in relative.parts)
    ):
        raise ResearchMapError(
            "target is reserved; questions are derived and artifacts are immutable"
        )
    return PutTarget(relative_path=relative, overwrite=False)


def map_name_for_path(root: Path, value: str) -> str | None:
    candidate = Path(value).expanduser().absolute().resolve(strict=False)
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    if not relative.parts:
        return None
    for index, part in enumerate(relative.parts):
        if MAP_NAME_RE.fullmatch(part):
            name = Path(*relative.parts[: index + 1]).as_posix()
            try:
                return validate_map_name(name)
            except ResearchMapError:
                return None
    return None
