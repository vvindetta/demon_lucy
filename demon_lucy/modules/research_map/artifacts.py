from __future__ import annotations

from pathlib import Path

from demon_lucy.modules.research_map.documents import (
    ARTIFACT_ID_RE,
    QUESTION_ID_RE,
    ResearchMapError,
    contains_markdown_table,
    count_h1_headings,
    ensure_timestamp,
    extract_h1,
    is_external_target,
    markdown_image_targets,
    markdown_targets,
    now_timestamp,
    read_document,
    single_line,
    slugify,
    timestamp_field_is_exact,
)
from demon_lucy.modules.research_map.nodes import read_nodes
from demon_lucy.modules.research_map.storage import (
    publish_exclusive_text,
    remove_empty_directory,
)


def validate_artifact_content(
    map_dir: Path,
    path: Path,
    data: dict[str, object],
    body: str,
    text: str,
) -> str:
    artifact_id = str(data.get("id", ""))
    if not ARTIFACT_ID_RE.fullmatch(artifact_id):
        raise ResearchMapError(f"invalid artifact ID in {path}: {artifact_id!r}")
    if data.get("type") != "artifact":
        raise ResearchMapError(f"{path} must have type: artifact")
    if path.resolve().parent != (map_dir / "artifacts").resolve():
        raise ResearchMapError(f"artifact must remain in flat artifacts/: {path}")
    if not path.stem.lower().startswith(f"{artifact_id.lower()}-"):
        raise ResearchMapError(f"filename must start with {artifact_id.lower()}-: {path}")
    if not timestamp_field_is_exact(text, "created"):
        raise ResearchMapError(f"{path} has invalid or missing created")
    if not extract_h1(body):
        raise ResearchMapError(f"missing H1 title in {path}")
    if count_h1_headings(body) != 1:
        raise ResearchMapError(
            f"artifact must contain exactly one H1 title: {path}"
        )
    if contains_markdown_table(text):
        raise ResearchMapError(f"Markdown table is not allowed: {path}")

    attachments_root = (map_dir / ".attach").resolve()
    for target in markdown_image_targets(text):
        if is_external_target(target):
            raise ResearchMapError(f"image must be stored locally in .attach/: {target}")
        clean = target.split("#", 1)[0]
        if (path.parent / clean).resolve().parent != attachments_root:
            raise ResearchMapError(f"image must be stored directly in .attach/: {target}")
    for target in markdown_targets(text):
        if is_external_target(target):
            continue
        clean = target.split("#", 1)[0]
        if not (path.parent / clean).resolve().exists():
            raise ResearchMapError(f"broken link in {path}: {target}")
    return artifact_id


def scan_artifacts(map_dir: Path) -> dict[str, Path]:
    artifacts_dir = map_dir / "artifacts"
    if artifacts_dir.is_symlink():
        raise ResearchMapError(f"unsafe artifacts directory: {artifacts_dir}")
    if not artifacts_dir.exists():
        return {}
    if not artifacts_dir.is_dir():
        raise ResearchMapError(f"artifacts path must be a directory: {artifacts_dir}")

    found: dict[str, Path] = {}
    for path in sorted(artifacts_dir.glob("*.md")):
        if path.is_symlink():
            raise ResearchMapError(f"artifact must not be a symlink: {path}")
        data, body, text = read_document(path)
        artifact_id = validate_artifact_content(map_dir, path, data, body, text)
        if artifact_id in found:
            raise ResearchMapError(
                f"duplicate artifact ID {artifact_id}: {found[artifact_id]} and {path}"
            )
        found[artifact_id] = path
    return found


def next_artifact_number(map_dir: Path) -> int:
    found = scan_artifacts(map_dir)
    numbers = [
        int(match.group("number"))
        for artifact_id in found
        if (match := ARTIFACT_ID_RE.fullmatch(artifact_id)) is not None
    ]
    if numbers:
        expected = set(range(1, max(numbers) + 1))
        missing = sorted(expected - set(numbers))
        if missing:
            rendered = ", ".join(f"A{number}" for number in missing)
            raise ResearchMapError(f"artifact sequence has gaps: {rendered}")
    return max(numbers, default=0) + 1


def create_artifact(
    *,
    map_dir: Path,
    title: str,
    body: str,
    question: str | None,
    timestamp: str | None = None,
) -> Path:
    artifacts_dir = map_dir / "artifacts"
    if artifacts_dir.is_symlink():
        raise ResearchMapError(f"unsafe artifacts directory: {artifacts_dir}")
    if artifacts_dir.exists() and not artifacts_dir.is_dir():
        raise ResearchMapError(f"artifacts path must be a directory: {artifacts_dir}")

    safe_title = single_line(title, "title")
    clean_body = body.strip()
    if count_h1_headings(clean_body):
        raise ResearchMapError("artifact body must not contain another H1 heading")
    safe_question = question.strip() if question else None
    if safe_question and not QUESTION_ID_RE.fullmatch(safe_question):
        raise ResearchMapError(f"invalid question ID: {question!r}")
    if safe_question and safe_question not in read_nodes(map_dir):
        raise ResearchMapError(f"question does not exist: {safe_question}")

    value_timestamp = ensure_timestamp(timestamp) if timestamp else now_timestamp()
    number = next_artifact_number(map_dir)
    artifact_id = f"A{number}"
    parts = [artifact_id.lower()]
    if safe_question:
        parts.append(safe_question.lower())
    parts.append(slugify(safe_title))
    path = artifacts_dir / ("-".join(parts) + ".md")
    document = (
        "---\n"
        f"id: {artifact_id}\n"
        "type: artifact\n"
        f"created: {value_timestamp}\n"
        "---\n\n"
        f"# {safe_title}\n"
    )
    if clean_body:
        document += f"\n{clean_body}\n"
    artifact_body = document.split("---\n\n", 1)[1]
    validate_artifact_content(
        map_dir,
        path,
        {"id": artifact_id, "type": "artifact", "created": value_timestamp},
        artifact_body,
        document,
    )

    created_directory = False
    try:
        if not artifacts_dir.exists():
            artifacts_dir.mkdir()
            created_directory = True
        if artifacts_dir.is_symlink() or not artifacts_dir.is_dir():
            raise ResearchMapError(f"unsafe artifacts directory: {artifacts_dir}")
        publish_exclusive_text(path, document, mode=0o444)
    except BaseException:
        if created_directory:
            remove_empty_directory(artifacts_dir)
        raise
    return path
