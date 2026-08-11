from __future__ import annotations

import re
import stat
from pathlib import Path

from demon_lucy.modules.research_map.documents import (
    ARTIFACT_ID_RE,
    QUESTION_ID_RE,
    ResearchMapError,
    contains_markdown_table,
    count_h1_headings,
    extract_h1,
    is_external_target,
    markdown_image_targets,
    markdown_targets,
    read_document,
    section_prefix,
    single_link_target,
    timestamp_field_is_exact,
)
from demon_lucy.modules.research_map.models import ValidationResult
from demon_lucy.modules.research_map.questions import render_questions_body


ALLOWED_STATUSES = {"open", "parked", "done"}


def _resolve_target(source: Path, target: str) -> Path:
    return (source.parent / target.split("#", 1)[0]).resolve()


def validate_map(map_dir: Path) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    unresolved = map_dir.expanduser().absolute()
    if unresolved.is_symlink():
        return ValidationResult(
            errors=(f"map directory must not be a symlink: {unresolved}",)
        )
    if not unresolved.is_dir():
        return ValidationResult(errors=(f"map directory does not exist: {unresolved}",))
    map_dir = unresolved.resolve()

    index_path = map_dir / "index.md"
    questions_path = map_dir / "questions.md"
    nodes_dir = map_dir / "b-nodes"
    legacy_nodes_dir = map_dir / "nodes"
    artifacts_dir = map_dir / "artifacts"
    attachments_dir = map_dir / ".attach"
    for path, expected_type in (
        (index_path, "file"),
        (questions_path, "file"),
        (nodes_dir, "directory"),
    ):
        if not path.exists():
            errors.append(f"missing required path: {path}")
        elif path.is_symlink():
            errors.append(f"required path must not be a symlink: {path}")
        elif expected_type == "file" and not path.is_file():
            errors.append(f"required path must be a regular file: {path}")
        elif expected_type == "directory" and not path.is_dir():
            errors.append(f"required path must be a directory: {path}")
    if legacy_nodes_dir.exists() or legacy_nodes_dir.is_symlink():
        errors.append(f"legacy nodes/ is not allowed; use b-nodes/: {legacy_nodes_dir}")
    if artifacts_dir.is_symlink():
        errors.append(f"artifacts/ must not be a symlink: {artifacts_dir}")
    elif artifacts_dir.exists() and not artifacts_dir.is_dir():
        errors.append(f"artifacts/ must be a directory: {artifacts_dir}")
    if attachments_dir.is_symlink():
        errors.append(f".attach/ must not be a symlink: {attachments_dir}")
    elif attachments_dir.exists() and not attachments_dir.is_dir():
        errors.append(f".attach/ must be a directory: {attachments_dir}")
    elif attachments_dir.is_dir():
        entries = list(attachments_dir.iterdir())
        if not entries:
            errors.append(".attach/ must not exist while it is empty")
        for path in entries:
            if path.is_symlink() or not path.is_file():
                errors.append(
                    f"attachments must be regular files in flat .attach/: {path}"
                )
    if errors:
        return ValidationResult(errors=tuple(errors), warnings=tuple(warnings))

    documents: dict[Path, tuple[dict, str, str]] = {}

    def load(path: Path) -> tuple[dict, str, str] | None:
        if path in documents:
            return documents[path]
        try:
            documents[path] = read_document(path)
        except ResearchMapError as exc:
            errors.append(str(exc))
            return None
        return documents[path]

    index_document = load(index_path)
    questions_document = load(questions_path)
    index_navigation_body = ""
    if index_document:
        data, body, text = index_document
        if data.get("type") != "research-map":
            errors.append("index.md must have type: research-map")
        for field in ("created", "updated"):
            if not timestamp_field_is_exact(text, field):
                errors.append(f"index.md has invalid or missing {field}")
        if count_h1_headings(body) != 1:
            errors.append("index.md must contain exactly one H1 title")
        headings = re.findall(r"^## (.+?)\s*$", body, re.MULTILINE)
        if headings.count("Seed") != 1 or not headings or headings[-1] != "Seed":
            errors.append("index.md must end with exactly one ## Seed section")
        index_navigation_body = section_prefix(body, "Seed")
        if any(heading.casefold().startswith("current focus") for heading in headings):
            errors.append("index.md must not contain a current-focus section")

    node_paths: list[Path] = []
    node_directories: list[Path] = []
    for path in sorted(nodes_dir.rglob("*")):
        if path.is_symlink():
            errors.append(f"node-tree path must not be a symlink: {path}")
        elif path.is_dir():
            node_directories.append(path)
        elif path.is_file() and path.suffix == ".md":
            node_paths.append(path)
        else:
            errors.append(f"b-nodes/ may contain only node Markdown files: {path}")

    nodes_by_id: dict[str, Path] = {}
    node_data: dict[Path, dict] = {}
    for path in node_paths:
        document = load(path)
        if not document:
            continue
        data, body, text = document
        node_data[path] = data
        question_id = str(data.get("id", ""))
        if not QUESTION_ID_RE.fullmatch(question_id):
            errors.append(f"invalid question ID in {path}: {question_id!r}")
            continue
        if question_id in nodes_by_id:
            errors.append(
                f"duplicate question ID {question_id}: "
                f"{nodes_by_id[question_id]} and {path}"
            )
        nodes_by_id[question_id] = path
        if data.get("type") != "question":
            errors.append(f"{path} must have type: question")
        if data.get("status") not in ALLOWED_STATUSES:
            errors.append(f"invalid status in {path}: {data.get('status')!r}")
        if not path.stem.startswith(f"{question_id}_"):
            errors.append(f"filename must start with {question_id}_: {path}")
        if not extract_h1(body):
            errors.append(f"missing H1 question in {path}")
        if count_h1_headings(body) != 1:
            errors.append(f"node must contain exactly one H1 question: {path}")
        headings = re.findall(r"^## (.+?)\s*$", body, re.MULTILINE)
        if "Child Questions" in headings and headings[-1] != "Child Questions":
            errors.append(f"Child Questions must be the final section: {path}")
        for field in ("created", "updated"):
            if not timestamp_field_is_exact(text, field):
                errors.append(f"{path} has invalid or missing {field}")

    for question_id, path in nodes_by_id.items():
        parts = question_id.split(".")
        parent_value = node_data[path].get("parent")
        if len(parts) == 1:
            if path.parent != nodes_dir:
                errors.append(f"root node must be directly inside b-nodes/: {path}")
            if parent_value is not None:
                errors.append(f"root node must not have parent: {path}")
            continue
        expected_parent_id = ".".join(parts[:-1])
        expected_parent_path = nodes_by_id.get(expected_parent_id)
        if expected_parent_path is None:
            errors.append(
                f"node {path} has no parent node with ID {expected_parent_id}"
            )
            continue
        expected_directory = expected_parent_path.with_suffix("")
        if path.parent.resolve() != expected_directory.resolve():
            errors.append(
                f"node {question_id} must be directly inside "
                f"{expected_directory.relative_to(map_dir)}"
            )
        target = single_link_target(parent_value)
        if not target:
            errors.append(f"child node must have Markdown parent link: {path}")
            continue
        parent_path = _resolve_target(path, target)
        if parent_path != expected_parent_path.resolve():
            errors.append(
                f"parent link in {path} must target {expected_parent_path.name}"
            )
            continue
        if not parent_path.is_file():
            errors.append(f"broken parent link in {path}: {target}")
            continue
        parent_document = load(parent_path)
        if not parent_document:
            continue
        parent_id = str(parent_document[0].get("id", ""))
        if parent_id != expected_parent_id:
            errors.append(
                f"parent ID mismatch in {path}: expected {expected_parent_id}, "
                f"got {parent_id}"
            )
        backlinks = {
            _resolve_target(parent_path, item)
            for item in markdown_targets(parent_document[1])
            if not is_external_target(item)
        }
        if path.resolve() not in backlinks:
            errors.append(f"parent {parent_path} does not link child {path.name}")

    allowed_directories = {path.with_suffix("").resolve() for path in node_paths}
    for directory in node_directories:
        if directory.resolve() not in allowed_directories:
            errors.append(
                f"node directory must use the stem of its owning node: {directory}"
            )
            continue
        has_direct_child = any(
            child.is_file() and not child.is_symlink() and child.suffix == ".md"
            for child in directory.iterdir()
        )
        if not has_direct_child:
            errors.append(f"node directory has no direct child nodes: {directory}")

    artifact_paths = sorted(artifacts_dir.rglob("*.md"))
    artifact_numbers: dict[int, Path] = {}
    for path in artifact_paths:
        if path.is_symlink():
            errors.append(f"artifact must not be a symlink: {path}")
        if path.parent != artifacts_dir:
            errors.append(f"artifact must remain in flat artifacts/: {path}")
        document = load(path)
        if not document:
            continue
        data, body, text = document
        artifact_id = str(data.get("id", ""))
        match = ARTIFACT_ID_RE.fullmatch(artifact_id)
        if not match:
            errors.append(f"invalid artifact ID in {path}: {artifact_id!r}")
            continue
        number = int(match.group("number"))
        if number in artifact_numbers:
            errors.append(f"duplicate artifact ID {artifact_id}")
        artifact_numbers[number] = path
        if data.get("type") != "artifact":
            errors.append(f"{path} must have type: artifact")
        if not path.stem.lower().startswith(f"{artifact_id.lower()}-"):
            errors.append(f"filename must start with {artifact_id.lower()}-: {path}")
        if not timestamp_field_is_exact(text, "created"):
            errors.append(f"{path} has invalid or missing created")
        if count_h1_headings(body) != 1:
            errors.append(f"artifact must contain exactly one H1 title: {path}")
        if path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
            warnings.append(f"artifact is writable; preserve it unchanged: {path}")
    if artifact_numbers:
        missing = sorted(set(range(1, max(artifact_numbers) + 1)) - artifact_numbers.keys())
        if missing:
            errors.append(
                "missing artifact IDs: " + ", ".join(f"A{item}" for item in missing)
            )

    for path in [index_path, questions_path, *node_paths, *artifact_paths]:
        document = load(path)
        if not document:
            continue
        checked_text = index_navigation_body if path == index_path else document[2]
        if contains_markdown_table(checked_text):
            errors.append(f"Markdown table is not allowed: {path}")
        for target in markdown_image_targets(checked_text):
            if is_external_target(target):
                errors.append(f"image must be stored locally in .attach/: {target}")
            elif _resolve_target(path, target).parent != attachments_dir.resolve():
                errors.append(f"image must be stored directly in .attach/: {target}")
        for target in markdown_targets(checked_text):
            if not is_external_target(target) and not _resolve_target(path, target).exists():
                errors.append(f"broken link in {path}: {target}")

    branch_entries: dict[str, tuple[str, Path]] = {}
    branch_pattern = re.compile(
        r"^([1-9]\d*)\s+-\s+.+\s+\[(open|parked|done)\]\(([^)]+)\):\s*$",
        re.MULTILINE,
    )
    for match in branch_pattern.finditer(index_navigation_body):
        question_id, status, target = match.groups()
        if question_id in branch_entries:
            errors.append(f"duplicate branch entry in index.md: {question_id}")
        branch_entries[question_id] = (status, _resolve_target(index_path, target))
        following = index_navigation_body[match.end() :]
        if re.match(r"^\n\*\s+\S.*(?:\n|$)", following) is None:
            errors.append(
                f"root branch entry is missing a non-empty summary: {question_id}"
            )
    for question_id, path in nodes_by_id.items():
        if "." in question_id:
            continue
        entry = branch_entries.get(question_id)
        if not entry:
            errors.append(f"root node is missing from index.md: {path.name}")
            continue
        shown_status, shown_path = entry
        if shown_path != path.resolve():
            errors.append(f"index.md points {question_id} to the wrong file")
        actual_status = str(node_data[path].get("status", ""))
        if shown_status != actual_status:
            errors.append(
                f"index.md status mismatch for {question_id}: "
                f"{shown_status} != {actual_status}"
            )
    for question_id in branch_entries:
        if question_id not in nodes_by_id:
            errors.append(f"index.md references unknown root node: {question_id}")

    if questions_document:
        data, body, text = questions_document
        if data.get("type") != "questions":
            errors.append("questions.md must have type: questions")
        for field in ("created", "updated"):
            if not timestamp_field_is_exact(text, field):
                errors.append(f"questions.md has invalid or missing {field}")
        try:
            if body.strip() != render_questions_body(map_dir).strip():
                errors.append("questions.md is stale")
        except ResearchMapError as exc:
            errors.append(str(exc))

    separate_seed = map_dir / "seed.md"
    if separate_seed.exists() or separate_seed.is_symlink():
        errors.append("separate seed.md is not allowed; use the final Seed section")
    return ValidationResult(errors=tuple(errors), warnings=tuple(warnings))
