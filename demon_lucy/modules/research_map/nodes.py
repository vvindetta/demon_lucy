from __future__ import annotations

import os
import re
from pathlib import Path

from demon_lucy.modules.research_map.documents import (
    FRONTMATTER_RE,
    QUESTION_ID_RE,
    ResearchMapError,
    node_label,
    question_sort_key,
    read_document,
    read_node_document,
    single_line,
    slugify,
)
from demon_lucy.modules.research_map.models import NodeType, ResearchMapStatus
from demon_lucy.modules.research_map.storage import (
    atomic_write_text_if_changed,
    publish_exclusive_text,
    remove_empty_directory,
)


def read_nodes(map_dir: Path) -> dict[str, Path]:
    nodes_dir = map_dir / "b-nodes"
    if nodes_dir.is_symlink() or not nodes_dir.is_dir():
        raise ResearchMapError(f"missing or unsafe nodes directory: {nodes_dir}")

    nodes: dict[str, Path] = {}
    for path in sorted(nodes_dir.rglob("*.md")):
        if path.is_symlink() or not path.is_file():
            raise ResearchMapError(f"node must be a regular file: {path}")
        document = read_node_document(path)
        if document is None:
            continue
        data, _, _ = document
        question_id = str(data.get("id", ""))
        if not QUESTION_ID_RE.fullmatch(question_id):
            raise ResearchMapError(f"invalid question ID in {path}: {question_id!r}")
        if question_id in nodes:
            raise ResearchMapError(f"duplicate question ID {question_id}")
        nodes[question_id] = path
    return nodes


def next_question_id(nodes: dict[str, Path], parent: str | None) -> str:
    if parent:
        if parent not in nodes:
            raise ResearchMapError(f"parent does not exist: {parent}")
        parent_parts = parent.split(".")
        children = []
        for question_id in nodes:
            parts = question_id.split(".")
            if len(parts) == len(parent_parts) + 1 and parts[:-1] == parent_parts:
                children.append(int(parts[-1]))
        return f"{parent}.{max(children, default=0) + 1}"

    roots = [int(question_id) for question_id in nodes if "." not in question_id]
    return str(max(roots, default=0) + 1)


def _relative_link(source: Path, target: Path) -> str:
    return Path(os.path.relpath(target, source.parent)).as_posix()


def append_child(
    parent_path: Path,
    child_id: str,
    child_label: str,
    child_path: Path,
) -> str:
    _, body, text = read_document(parent_path)
    link = (
        f"- [{child_id} - {child_label}]" f"({_relative_link(parent_path, child_path)})"
    )
    updated_body = body.rstrip() + f"\n\n{link}\n"

    match = FRONTMATTER_RE.match(text)
    if match is None:
        raise ResearchMapError(f"missing YAML frontmatter in {parent_path}")
    return text[: match.end()] + updated_body


def append_root_entry(
    index_path: Path,
    question_id: str,
    label: str,
    summary: str,
    node_path: Path,
    status: str,
) -> str:
    _, body, text = read_document(index_path)
    headings = re.findall(r"^## (.+?)\s*$", body, re.MULTILINE)
    if headings.count("Seed") != 1 or not headings or headings[-1] != "Seed":
        raise ResearchMapError("index.md must end with exactly one ## Seed section")
    if "Main Branches" not in headings:
        raise ResearchMapError("index.md is missing ## Main Branches")
    seed_match = re.search(r"^## Seed\s*$", body, re.MULTILINE)
    if seed_match is None:
        raise ResearchMapError("index.md is missing ## Seed")
    entry = (
        f"{question_id} - {label} [{status}]"
        f"({_relative_link(index_path, node_path)}):"
    )
    if summary:
        entry += f"\n* {summary}"
    updated_body = (
        body[: seed_match.start()].rstrip()
        + "\n\n"
        + entry
        + "\n\n"
        + body[seed_match.start() :].lstrip()
    )
    match = FRONTMATTER_RE.match(text)
    if match is None:
        raise ResearchMapError(f"missing YAML frontmatter in {index_path}")
    return text[: match.end()] + updated_body


def create_node(
    *,
    map_dir: Path,
    title: str,
    label: str,
    parent: str | None,
    summary: str | None,
    status: ResearchMapStatus,
    node_type: NodeType = NodeType.NODE,
) -> Path:
    safe_title = (
        single_line(title, "title") if title or node_type is NodeType.NODE else ""
    )
    safe_label = single_line(label, "label")
    if any(character in safe_label for character in "[]"):
        raise ResearchMapError("label must not contain square brackets")
    safe_parent = parent.strip() if parent else None
    if safe_parent and not QUESTION_ID_RE.fullmatch(safe_parent):
        raise ResearchMapError(f"invalid parent ID: {parent!r}")
    if safe_parent is None:
        safe_summary = single_line(summary, "root node summary") if summary else None
    else:
        if summary is not None and summary.strip():
            raise ResearchMapError("child node must not have summary")
        safe_summary = None

    nodes = read_nodes(map_dir)
    question_id = next_question_id(nodes, safe_parent)
    filename = f"{question_id}_{slugify(safe_label).replace('-', '_')}.md"
    if safe_parent:
        parent_path = nodes[safe_parent]
        path = parent_path.with_suffix("") / filename
    else:
        path = map_dir / "b-nodes" / filename
    fields = [
        "---",
        f'id: "{question_id}"',
        f"type: {node_type.value}",
    ]
    if node_type is NodeType.NODE:
        fields.append(f"status: {status.value}")
    if safe_parent:
        fields.append(f'parent: "[{safe_parent}]({_relative_link(path, parent_path)})"')
    fields.extend(["---", "", safe_title, ""])
    document = "\n".join(fields)

    parent_is_conspect = (
        safe_parent and read_document(nodes[safe_parent])[0].get("type") == "conspect"
    )
    if safe_parent and not parent_is_conspect:
        owner_path = nodes[safe_parent]
        owner_document = append_child(
            owner_path,
            question_id,
            safe_label,
            path,
        )
    else:
        owner_path = map_dir / "index.md"
        owner_document = append_root_entry(
            owner_path,
            question_id,
            safe_label,
            safe_summary or "",
            path,
            "conspect" if node_type is NodeType.CONSPECT else status.value,
        )

    created_directory = False
    published = False
    try:
        if not path.parent.exists():
            path.parent.mkdir()
            created_directory = True
        if path.parent.is_symlink() or not path.parent.is_dir():
            raise ResearchMapError(f"unsafe node directory: {path.parent}")
        publish_exclusive_text(path, document, mode=0o644)
        published = True
        atomic_write_text_if_changed(owner_path, owner_document)
    except BaseException:
        if published:
            path.unlink(missing_ok=True)
        if created_directory:
            remove_empty_directory(path.parent)
        raise
    return path


def reconcile_root_entries(map_dir: Path) -> dict[str, int]:
    """Refresh derivable root status/targets without changing labels or summaries."""
    index_path = map_dir / "index.md"
    nodes = read_nodes(map_dir)
    root_state: dict[str, tuple[str, str]] = {}
    for question_id, path in nodes.items():
        data, _, _ = read_document(path)
        if data.get("type") == "conspect":
            root_state[question_id] = ("conspect", path.relative_to(map_dir).as_posix())
            continue
        try:
            status = ResearchMapStatus(str(data.get("status", "")))
        except ValueError as exc:
            raise ResearchMapError(
                f"invalid status in {path}: {data.get('status')!r}"
            ) from exc
        root_state[question_id] = (
            status.value,
            path.relative_to(map_dir).as_posix(),
        )

    _, body, text = read_document(index_path)
    pattern = re.compile(
        r"^(?P<prefix>[1-9]\d*(?:\.[1-9]\d*)*[ \t]+-[ \t]+.+[ \t]+\[)"
        r"(?P<status>open|parked|done|conspect)"
        r"(?P<middle>\]\()(?P<target>[^)]+)(?P<suffix>\):[ \t]*)$",
        re.MULTILINE,
    )

    def replace_entry(match: re.Match[str]) -> str:
        question_id = match.group("prefix").split(" ", 1)[0]
        state = root_state.get(question_id)
        if state is None:
            return match.group(0)
        status, target = state
        return (
            match.group("prefix")
            + status
            + match.group("middle")
            + target
            + match.group("suffix")
        )

    seed = re.search(r"^## Seed[ \t]*$", body, re.MULTILINE)
    if seed is None:
        raise ResearchMapError("index.md is missing ## Seed")
    navigation = pattern.sub(replace_entry, body[: seed.start()])
    frontmatter = FRONTMATTER_RE.match(text)
    if frontmatter is None:
        raise ResearchMapError(f"missing YAML frontmatter in {index_path}")
    indexed = {
        match.group("prefix").split(" ", 1)[0] for match in pattern.finditer(navigation)
    }
    for node_id in sorted(nodes, key=question_sort_key):
        if node_id in indexed:
            continue
        parent_id = node_id.rpartition(".")[0]
        if parent_id:
            parent_path = nodes.get(parent_id)
            if (
                parent_path is None
                or read_document(parent_path)[0].get("type") != "conspect"
            ):
                continue
        status, target = root_state[node_id]
        entry = f"{node_id} - {node_label(nodes[node_id], node_id)} [{status}]({target}):\n\n"
        navigation = navigation.rstrip() + "\n\n" + entry
    document = text[: frontmatter.end()] + navigation + body[seed.start() :]
    changed = atomic_write_text_if_changed(index_path, document)
    return {str(index_path.resolve()): 1} if changed else {}
