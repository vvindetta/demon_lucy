from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from demon_lucy.modules.research_map.documents import ResearchMapError


TMP_ROOT = Path("/tmp")
MAP_NAME_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*_map")


@dataclass(frozen=True)
class PutTarget:
    relative_path: Path
    overwrite: bool


def validate_map_name(value: str) -> str:
    name = value.strip()
    if not MAP_NAME_RE.fullmatch(name):
        raise ResearchMapError(
            "map name must use lowercase ASCII words separated by hyphens "
            "and end in _map"
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
    for candidate in sorted(root.iterdir(), key=lambda path: path.name):
        if candidate.is_symlink() or not candidate.is_dir():
            continue
        try:
            name = validate_map_name(candidate.name)
        except ResearchMapError:
            continue
        resolved = candidate.resolve()
        if resolved.parent == root:
            found[name] = resolved
    return found


def resolve_map_dir(root: Path, name: str, *, must_exist: bool) -> Path:
    safe_name = validate_map_name(name)
    candidate = root / safe_name
    if candidate.is_symlink():
        raise ResearchMapError(f"map directory must not be a symlink: {candidate}")
    if must_exist and not candidate.is_dir():
        raise ResearchMapError(f"map does not exist: {candidate}")
    if not must_exist and os.path.lexists(candidate):
        raise ResearchMapError(f"map path already exists: {candidate}")
    resolved = candidate.resolve(strict=False)
    if resolved.parent != root:
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
        and relative.suffix == ".md"
        and relative.name != ".md"
    ):
        return PutTarget(relative_path=relative, overwrite=True)
    if (
        len(relative.parts) == 2
        and relative.parts[0] == ".attach"
        and relative.name not in {".", ".."}
    ):
        return PutTarget(relative_path=relative, overwrite=False)
    raise ResearchMapError(
        "target must be index.md, b-nodes/<node-path>.md, or .attach/<name>; "
        "questions are derived and artifacts are immutable"
    )


def map_name_for_path(root: Path, value: str) -> str | None:
    candidate = Path(value).expanduser().absolute().resolve(strict=False)
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    if not relative.parts:
        return None
    try:
        return validate_map_name(relative.parts[0])
    except ResearchMapError:
        return None
