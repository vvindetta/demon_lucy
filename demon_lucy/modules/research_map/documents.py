from __future__ import annotations

import os
import re
from collections.abc import Iterator
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token


FRONTMATTER_RE = re.compile(
    r"\A---\r?\n(?P<header>.*?)\r?\n---(?:\r?\n|\Z)", re.DOTALL
)
QUESTION_ID_RE = re.compile(r"\A(?P<parts>[1-9]\d*(?:\.[1-9]\d*)*)\Z")
ARTIFACT_ID_RE = re.compile(r"\AA(?P<number>\d+)\Z")
SINGLE_LINK_RE = re.compile(r"\A\[[^\]]+\]\(([^)]+)\)\Z")
TIMESTAMP_RE = re.compile(r"\A\d{4}-\d{2}-\d{2} \d{2}:\d{2}\Z")
MARKDOWN = MarkdownIt("commonmark").enable("table")


class ResearchMapError(RuntimeError):
    pass


def single_line(value: str, field: str) -> str:
    result = value.strip()
    if not result or "\n" in result or "\r" in result:
        raise ResearchMapError(f"{field} must be one non-empty line")
    return result


def now_timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def ensure_timestamp(value: str) -> str:
    if not TIMESTAMP_RE.fullmatch(value):
        raise ResearchMapError(
            f"invalid timestamp {value!r}; expected YYYY-MM-DD HH:MM"
        )
    try:
        datetime.strptime(value, "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ResearchMapError(
            f"invalid timestamp {value!r}; expected YYYY-MM-DD HH:MM"
        ) from exc
    return value


def format_timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d 00:00")
    return ensure_timestamp(str(value))


def read_document(path: Path) -> tuple[dict[str, Any], str, str]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ResearchMapError(f"cannot read {path}: {exc}") from exc

    match = FRONTMATTER_RE.match(text)
    if not match:
        raise ResearchMapError(f"missing YAML frontmatter in {path}")

    try:
        data = yaml.safe_load(match.group("header")) or {}
    except yaml.YAMLError as exc:
        raise ResearchMapError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ResearchMapError(f"frontmatter must be a mapping in {path}")

    return data, text[match.end() :], text


def extract_h1(body: str) -> str | None:
    for line in body.splitlines():
        if not line.strip():
            continue
        if line.startswith("# ") and line[2:].strip():
            return line[2:].strip()
        return None
    return None


def extract_section_items(body: str, section: str) -> list[str]:
    lines = body.splitlines()
    start = next(
        (index + 1 for index, line in enumerate(lines) if line == f"## {section}"),
        None,
    )
    if start is None:
        return []

    items: list[str] = []
    current: list[str] | None = None
    for line in lines[start:]:
        if line.startswith("## "):
            break
        bullet = re.match(r"^[-*]\s+(.+)$", line)
        if bullet:
            if current:
                items.append(" ".join(current))
            current = [bullet.group(1).strip()]
        elif current and line.strip():
            current.append(line.strip())
    if current:
        items.append(" ".join(current))
    return items


def question_sort_key(question_id: str) -> tuple[int, ...]:
    match = QUESTION_ID_RE.fullmatch(question_id)
    if not match:
        raise ResearchMapError(f"invalid question ID {question_id!r}")
    return tuple(int(part) for part in match.group("parts").split("."))


def node_label(path: Path, question_id: str) -> str:
    prefix = f"{question_id}_"
    stem = path.stem
    label = stem[len(prefix) :] if stem.startswith(prefix) else stem
    label = re.sub(r"[-_]+", " ", label).strip()
    return label[:1].upper() + label[1:] if label else question_id


def contains_markdown_table(text: str) -> bool:
    return any(token.type == "table_open" for token in markdown_tokens(text))


def markdown_targets(text: str) -> list[str]:
    targets: list[str] = []
    for token in markdown_tokens(text):
        attribute = {"link_open": "href", "image": "src"}.get(token.type)
        if attribute and (target := token.attrGet(attribute)) is not None:
            targets.append(unquote(target))
    return targets


def markdown_image_targets(text: str) -> list[str]:
    return [
        unquote(target)
        for token in markdown_tokens(text)
        if token.type == "image"
        if (target := token.attrGet("src")) is not None
    ]


def count_h1_headings(text: str) -> int:
    return sum(
        token.type == "heading_open" and token.tag == "h1"
        for token in markdown_tokens(text)
    )


def markdown_tokens(text: str) -> Iterator[Token]:
    pending = list(reversed(MARKDOWN.parse(text)))
    while pending:
        token = pending.pop()
        yield token
        if token.children:
            pending.extend(reversed(token.children))


def single_link_target(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = SINGLE_LINK_RE.fullmatch(value.strip())
    return match.group(1) if match else None


def is_external_target(target: str) -> bool:
    return bool(re.match(r"\A(?:https?:|mailto:|file:|#)", target))


def timestamp_field_is_exact(text: str, field: str) -> bool:
    frontmatter = FRONTMATTER_RE.match(text)
    if not frontmatter:
        return False
    match = re.search(
        rf"^{re.escape(field)}:\s*(.+?)\s*$",
        frontmatter.group("header"),
        re.MULTILINE,
    )
    if not match:
        return False
    try:
        ensure_timestamp(match.group(1))
    except ResearchMapError:
        return False
    return True


def rebase_markdown_links(text: str, source: Path, destination: Path) -> str:
    pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")

    def replace(match: re.Match[str]) -> str:
        label, target = match.groups()
        if is_external_target(target):
            return match.group(0)
        path_part, separator, anchor = target.partition("#")
        resolved = (source.parent / path_part).resolve()
        relative = Path(os.path.relpath(resolved, destination.parent)).as_posix()
        suffix = f"#{anchor}" if separator else ""
        return f"[{label}]({relative}{suffix})"

    return pattern.sub(replace, text)


def section_prefix(body: str, section: str) -> str:
    match = re.search(rf"^## {re.escape(section)}\s*$", body, re.MULTILINE)
    return body[: match.start()] if match else body


def slugify(value: str) -> str:
    value = value.strip().lower().replace("_", "-")
    value = re.sub(r"[^\w-]+", "-", value, flags=re.UNICODE)
    value = re.sub(r"-+", "-", value).strip("-")
    if not value:
        raise ResearchMapError("title does not produce a usable filename")
    return value
