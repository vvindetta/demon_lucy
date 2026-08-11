from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from demon_lucy.modules.research_map.documents import ResearchMapError, single_line
from demon_lucy.modules.research_map.paths import resolve_map_dir, validate_map_name
from demon_lucy.modules.research_map.storage import atomic_write_text_if_changed


ACTIVE_HEADING_RE = re.compile(r"^## Active[ \t]*$", re.MULTILINE)
NEXT_HEADING_RE = re.compile(r"^## ", re.MULTILINE)
ENTRY_RE = re.compile(
    r"^- \[(?P<label>[^\]\r\n]+)\]"
    r"\((?P<map_name>[a-z0-9]+(?:-[a-z0-9]+)*_map)/index\.md\)"
    r" - (?P<summary>[^\r\n]+)$"
)


@dataclass(frozen=True)
class RegistryEntry:
    label: str
    map_name: str
    summary: str


@dataclass(frozen=True)
class _RegistryDocument:
    prefix: str
    suffix: str
    entries: tuple[RegistryEntry, ...]

    def render(self) -> str:
        lines = [
            f"- [{entry.label}]({entry.map_name}/index.md) - {entry.summary}"
            for entry in self.entries
        ]
        body = "\n" + "\n".join(lines) + "\n" if lines else "\n"
        return self.prefix + body + self.suffix


def _registry_path(root: Path) -> Path:
    path = root / "index.md"
    if path.is_symlink() or not path.is_file():
        raise ResearchMapError(f"registry must be a regular non-symlink file: {path}")
    return path


def _read_document(root: Path) -> _RegistryDocument:
    path = _registry_path(root)
    text = path.read_text(encoding="utf-8")
    heading = ACTIVE_HEADING_RE.search(text)
    if heading is None:
        raise ResearchMapError("registry is missing '## Active'")
    line_end = text.find("\n", heading.end())
    body_start = len(text) if line_end < 0 else line_end + 1
    next_heading = NEXT_HEADING_RE.search(text, body_start)
    body_end = len(text) if next_heading is None else next_heading.start()
    body = text[body_start:body_end]

    entries: list[RegistryEntry] = []
    names: set[str] = set()
    for line in body.splitlines():
        if not line.strip():
            continue
        match = ENTRY_RE.fullmatch(line)
        if match is None:
            raise ResearchMapError(f"invalid active registry entry: {line!r}")
        map_name = validate_map_name(match.group("map_name"))
        if map_name in names:
            raise ResearchMapError(f"duplicate registry target: {map_name}/index.md")
        names.add(map_name)
        entries.append(
            RegistryEntry(
                label=match.group("label"),
                map_name=map_name,
                summary=match.group("summary"),
            )
        )
    return _RegistryDocument(
        prefix=text[:body_start],
        suffix=text[body_end:],
        entries=tuple(entries),
    )


def read_registry(root: Path) -> tuple[RegistryEntry, ...]:
    return _read_document(root).entries


def render_registration(
    root: Path,
    *,
    map_name: str,
    label: str,
    summary: str,
) -> str:
    safe_name = validate_map_name(map_name)
    safe_label = single_line(label, "registry label")
    if any(character in safe_label for character in "[]"):
        raise ResearchMapError("registry label must not contain square brackets")
    safe_summary = single_line(summary, "registry summary")
    document = _read_document(root)
    if any(entry.map_name == safe_name for entry in document.entries):
        raise ResearchMapError(f"map is already registered: {safe_name}")
    return replace(
        document,
        entries=(
            *document.entries,
            RegistryEntry(
                label=safe_label,
                map_name=safe_name,
                summary=safe_summary,
            ),
        ),
    ).render()


def register_map(
    root: Path,
    *,
    map_name: str,
    label: str,
    summary: str,
) -> dict[str, int]:
    resolve_map_dir(root, map_name, must_exist=True)
    content = render_registration(
        root,
        map_name=map_name,
        label=label,
        summary=summary,
    )
    path = _registry_path(root)
    changed = atomic_write_text_if_changed(path, content)
    return {str(path.resolve()): 1} if changed else {}


def update_registry_entry(
    root: Path,
    *,
    map_name: str,
    label: str,
) -> dict[str, int]:
    safe_name = validate_map_name(map_name)
    resolve_map_dir(root, safe_name, must_exist=True)
    safe_label = single_line(label, "registry label")
    if any(character in safe_label for character in "[]"):
        raise ResearchMapError("registry label must not contain square brackets")

    document = _read_document(root)
    found = False
    entries: list[RegistryEntry] = []
    for entry in document.entries:
        if entry.map_name != safe_name:
            entries.append(entry)
            continue
        found = True
        entries.append(
            RegistryEntry(
                label=safe_label,
                map_name=safe_name,
                summary=entry.summary,
            )
        )
    if not found:
        raise ResearchMapError(f"map is not registered: {safe_name}")

    path = _registry_path(root)
    changed = atomic_write_text_if_changed(
        path,
        replace(document, entries=tuple(entries)).render(),
    )
    return {str(path.resolve()): 1} if changed else {}
