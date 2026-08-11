from __future__ import annotations

from pathlib import Path

from demon_lucy.modules.research_map.documents import (
    ResearchMapError,
    ensure_timestamp,
    extract_h1,
    extract_section_items,
    format_timestamp,
    node_label,
    now_timestamp,
    question_sort_key,
    read_document,
    rebase_markdown_links,
    timestamp_field_is_exact,
)
from demon_lucy.modules.research_map.nodes import read_nodes
from demon_lucy.modules.research_map.storage import atomic_write_text_if_changed


def render_questions_body(map_dir: Path) -> str:
    groups: dict[str, list[tuple[tuple[int, ...], str, str, str, list[str]]]] = {
        "open": [],
        "parked": [],
        "done": [],
    }
    for path in read_nodes(map_dir).values():
        data, body, _ = read_document(path)
        question_id = str(data.get("id", ""))
        status = str(data.get("status", ""))
        if status not in groups:
            raise ResearchMapError(f"invalid status {status!r} in {path}")
        question = extract_h1(body)
        if not question:
            raise ResearchMapError(f"missing H1 question in {path}")
        destination = map_dir / "questions.md"
        items = [
            rebase_markdown_links(question, path, destination),
            *(
                rebase_markdown_links(item, path, destination)
                for item in extract_section_items(body, "Questions")
            ),
        ]
        groups[status].append(
            (
                question_sort_key(question_id),
                question_id,
                node_label(path, question_id),
                path.relative_to(map_dir).as_posix(),
                items,
            )
        )

    headings = {
        "open": "Open",
        "parked": "Parked",
        "done": "Done",
    }
    lines = ["# Questions"]
    for status in ("open", "parked", "done"):
        entries = sorted(groups[status], key=lambda entry: entry[0])
        if not entries:
            continue
        lines.extend(["", f"## {headings[status]}", ""])
        for _, question_id, label, relative, items in entries:
            lines.append(f"{question_id} - {label} [{status}]({relative}):")
            lines.extend(f"* {item}" for item in items)
            lines.append("")
        while lines and lines[-1] == "":
            lines.pop()
    return "\n".join(lines) + "\n"


def rebuild_questions(
    map_dir: Path,
    *,
    timestamp: str | None = None,
) -> dict[str, int]:
    questions_path = map_dir / "questions.md"
    if questions_path.is_symlink() or not questions_path.is_file():
        raise ResearchMapError(
            f"questions.md must be a regular non-symlink file: {questions_path}"
        )
    data, existing_body, existing_text = read_document(questions_path)
    expected_body = render_questions_body(map_dir)
    metadata_valid = (
        data.get("type") == "questions"
        and "created" in data
        and "updated" in data
        and timestamp_field_is_exact(existing_text, "created")
        and timestamp_field_is_exact(existing_text, "updated")
    )
    if metadata_valid and existing_body.strip() == expected_body.strip():
        return {}

    value_timestamp = ensure_timestamp(timestamp) if timestamp else now_timestamp()
    try:
        created = format_timestamp(data.get("created", value_timestamp))
    except ResearchMapError:
        created = value_timestamp
    document = (
        "---\n"
        "type: questions\n"
        f"created: {created}\n"
        f"updated: {value_timestamp}\n"
        "---\n\n"
        f"{expected_body}"
    )
    changed = atomic_write_text_if_changed(questions_path, document)
    return {str(questions_path.resolve()): 1} if changed else {}
