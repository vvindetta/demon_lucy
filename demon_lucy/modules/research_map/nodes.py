from __future__ import annotations

import os
import re
from pathlib import Path

from demon_lucy.modules.research_map.documents import (
    FRONTMATTER_RE,
    QUESTION_ID_RE,
    ResearchMapError,
    ensure_timestamp,
    now_timestamp,
    read_document,
    single_line,
    slugify,
)
from demon_lucy.modules.research_map.models import ResearchMapStatus
from demon_lucy.modules.research_map.storage import (
    atomic_write_text_if_changed,
    publish_exclusive_text,
    remove_empty_directory,
)


def _replace_updated(text: str, timestamp: str, path: Path) -> str:
    frontmatter = FRONTMATTER_RE.match(text)
    if not frontmatter:
        raise ResearchMapError(f"missing YAML frontmatter in {path}")
    header, replacements = re.subn(
        r"^updated:\s*.*$",
        f"updated: {timestamp}",
        frontmatter.group("header"),
        count=1,
        flags=re.MULTILINE,
    )
    if replacements != 1:
        raise ResearchMapError(f"missing updated field in {path}")
    return (
        text[: frontmatter.start("header")]
        + header
        + text[frontmatter.end("header") :]
    )


def read_nodes(map_dir: Path) -> dict[str, Path]:
    nodes_dir = map_dir / "b-nodes"
    if nodes_dir.is_symlink() or not nodes_dir.is_dir():
        raise ResearchMapError(f"missing or unsafe nodes directory: {nodes_dir}")

    nodes: dict[str, Path] = {}
    for path in sorted(nodes_dir.rglob("*.md")):
        if path.is_symlink() or not path.is_file():
            raise ResearchMapError(f"node must be a regular file: {path}")
        data, _, _ = read_document(path)
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


def _node_slug(label: str) -> str:
    return slugify(label).replace("-", "_")


def _relative_link(source: Path, target: Path) -> str:
    return Path(os.path.relpath(target, source.parent)).as_posix()


def append_child(
    parent_path: Path,
    child_id: str,
    child_label: str,
    child_path: Path,
    timestamp: str,
) -> str:
    _, body, text = read_document(parent_path)
    headings = re.findall(r"^## (.+?)\s*$", body, re.MULTILINE)
    if "Child Questions" in headings and headings[-1] != "Child Questions":
        raise ResearchMapError(f"Child Questions must be final in {parent_path}")

    link = (
        f"- [{child_id} - {child_label}]"
        f"({_relative_link(parent_path, child_path)})"
    )
    updated_body = body.rstrip()
    if "Child Questions" in headings:
        updated_body += f"\n{link}\n"
    else:
        updated_body += f"\n\n## Child Questions\n\n{link}\n"

    updated = _replace_updated(text, timestamp, parent_path)
    match = FRONTMATTER_RE.match(updated)
    if match is None:
        raise ResearchMapError(f"missing YAML frontmatter in {parent_path}")
    return updated[: match.end()] + updated_body


def append_root_entry(
    index_path: Path,
    question_id: str,
    label: str,
    summary: str,
    node_path: Path,
    status: ResearchMapStatus,
    timestamp: str,
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
        f"{question_id} - {label} [{status.value}]"
        f"({_relative_link(index_path, node_path)}):\n* {summary}"
    )
    updated_body = (
        body[: seed_match.start()].rstrip()
        + "\n\n"
        + entry
        + "\n\n"
        + body[seed_match.start() :].lstrip()
    )
    updated = _replace_updated(text, timestamp, index_path)
    match = FRONTMATTER_RE.match(updated)
    if match is None:
        raise ResearchMapError(f"missing YAML frontmatter in {index_path}")
    return updated[: match.end()] + updated_body


def create_node(
    *,
    map_dir: Path,
    question: str,
    label: str,
    parent: str | None,
    summary: str | None,
    status: ResearchMapStatus,
    timestamp: str | None = None,
) -> Path:
    safe_question = single_line(question, "question")
    safe_label = single_line(label, "label")
    if any(character in safe_label for character in "[]"):
        raise ResearchMapError("label must not contain square brackets")
    safe_parent = parent.strip() if parent else None
    if safe_parent and not QUESTION_ID_RE.fullmatch(safe_parent):
        raise ResearchMapError(f"invalid parent ID: {parent!r}")
    if safe_parent is None:
        if summary is None:
            raise ResearchMapError("root node summary is required")
        safe_summary = single_line(summary, "root node summary")
    else:
        if summary is not None and summary.strip():
            raise ResearchMapError("child node must not have summary")
        safe_summary = None
    value_timestamp = ensure_timestamp(timestamp) if timestamp else now_timestamp()

    nodes = read_nodes(map_dir)
    question_id = next_question_id(nodes, safe_parent)
    filename = f"{question_id}_{_node_slug(safe_label)}.md"
    if safe_parent:
        parent_path = nodes[safe_parent]
        path = parent_path.with_suffix("") / filename
    else:
        path = map_dir / "b-nodes" / filename
    fields = [
        "---",
        f'id: "{question_id}"',
        "type: question",
        f"status: {status.value}",
        f"created: {value_timestamp}",
        f"updated: {value_timestamp}",
    ]
    if safe_parent:
        fields.append(
            f'parent: "[{safe_parent}]({_relative_link(path, parent_path)})"'
        )
    fields.extend(["---", "", f"# {safe_question}", ""])
    document = "\n".join(fields)

    if safe_parent:
        owner_path = nodes[safe_parent]
        owner_document = append_child(
            owner_path,
            question_id,
            safe_label,
            path,
            value_timestamp,
        )
    else:
        owner_path = map_dir / "index.md"
        owner_document = append_root_entry(
            owner_path,
            question_id,
            safe_label,
            safe_summary or "",
            path,
            status,
            value_timestamp,
        )

    created_directory = False
    try:
        if not path.parent.exists():
            path.parent.mkdir()
            created_directory = True
        if path.parent.is_symlink() or not path.parent.is_dir():
            raise ResearchMapError(f"unsafe node directory: {path.parent}")
        publish_exclusive_text(path, document, mode=0o644)
        atomic_write_text_if_changed(owner_path, owner_document)
    except BaseException:
        path.unlink(missing_ok=True)
        if created_directory:
            remove_empty_directory(path.parent)
        raise
    return path


def reconcile_root_entries(
    map_dir: Path,
    *,
    timestamp: str | None = None,
) -> dict[str, int]:
    """Refresh derivable root status/targets without changing labels or summaries."""
    index_path = map_dir / "index.md"
    nodes = read_nodes(map_dir)
    root_state: dict[str, tuple[str, str]] = {}
    for question_id, path in nodes.items():
        if "." in question_id:
            continue
        data, _, _ = read_document(path)
        try:
            status = ResearchMapStatus(str(data.get("status", "")))
        except ValueError as exc:
            raise ResearchMapError(f"invalid status in {path}: {data.get('status')!r}") from exc
        root_state[question_id] = (
            status.value,
            path.relative_to(map_dir).as_posix(),
        )

    _, body, text = read_document(index_path)
    pattern = re.compile(
        r"^(?P<prefix>[1-9]\d*\s+-\s+.+\s+\[)"
        r"(?P<status>open|parked|done)"
        r"(?P<middle>\]\()(?P<target>[^)]+)(?P<suffix>\):\s*)$",
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

    updated_body = pattern.sub(replace_entry, body)
    if updated_body == body:
        return {}
    value_timestamp = ensure_timestamp(timestamp) if timestamp else now_timestamp()
    updated = _replace_updated(text, value_timestamp, index_path)
    frontmatter = FRONTMATTER_RE.match(updated)
    if frontmatter is None:
        raise ResearchMapError(f"missing YAML frontmatter in {index_path}")
    document = updated[: frontmatter.end()] + updated_body
    changed = atomic_write_text_if_changed(index_path, document)
    return {str(index_path.resolve()): 1} if changed else {}
