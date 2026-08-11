from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from demon_lucy.modules.research_map.documents import (
    ResearchMapError,
    ensure_timestamp,
    now_timestamp,
    single_line,
)
from demon_lucy.modules.research_map.paths import resolve_map_dir, resolve_root
from demon_lucy.modules.research_map.registry import render_registration
from demon_lucy.modules.research_map.storage import atomic_write_text_if_changed


TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def _quote_seed(value: str) -> str:
    lines = value.strip().splitlines()
    if not lines:
        raise ResearchMapError("seed must not be empty")
    return "\n".join(">" if not line else f"> {line}" for line in lines)


def _render_template(name: str, replacements: dict[str, str]) -> str:
    template = TEMPLATE_DIR / name
    if template.is_symlink() or not template.is_file():
        raise ResearchMapError(f"missing research map template: {template}")
    content = template.read_text(encoding="utf-8")
    for key, value in replacements.items():
        content = content.replace(f"{{{{{key}}}}}", value)
    return content


def _rollback_initial_map(
    map_dir: Path,
    *,
    index_content: str,
    questions_content: str,
) -> bool:
    expected = {"index.md", "questions.md", "b-nodes"}
    if not map_dir.is_dir() or map_dir.is_symlink():
        return False
    if {path.name for path in map_dir.iterdir()} != expected:
        return False
    nodes = map_dir / "b-nodes"
    index = map_dir / "index.md"
    questions = map_dir / "questions.md"
    if nodes.is_symlink() or not nodes.is_dir() or any(nodes.iterdir()):
        return False
    if index.is_symlink() or questions.is_symlink():
        return False
    if not index.is_file() or not questions.is_file():
        return False
    if index.read_text(encoding="utf-8") != index_content:
        return False
    if questions.read_text(encoding="utf-8") != questions_content:
        return False
    index.unlink()
    questions.unlink()
    nodes.rmdir()
    map_dir.rmdir()
    return True


def init_map(
    *,
    root: Path,
    map_name: str,
    title: str,
    goal: str,
    seed: str,
    registry_summary: str,
    timestamp: str | None = None,
) -> dict[str, int]:
    resolved_root = resolve_root(str(root))
    map_dir = resolve_map_dir(resolved_root, map_name, must_exist=False)
    safe_title = single_line(title, "title")
    safe_goal = single_line(goal, "goal")
    value_timestamp = ensure_timestamp(timestamp) if timestamp else now_timestamp()
    quoted_seed = _quote_seed(seed)
    registry_content = render_registration(
        resolved_root,
        map_name=map_name,
        label=safe_title,
        summary=registry_summary,
    )
    replacements = {
        "TIMESTAMP": value_timestamp,
        "TITLE": safe_title,
        "GOAL": safe_goal,
        "SEED": quoted_seed,
    }
    index_content = _render_template("index.md", replacements)
    questions_content = _render_template("questions.md", replacements)

    staging = Path(
        tempfile.mkdtemp(prefix=f".{map_dir.name}.", dir=resolved_root)
    )
    try:
        os.chmod(staging, 0o755)
        (staging / "b-nodes").mkdir()
        (staging / "index.md").write_text(index_content, encoding="utf-8")
        (staging / "questions.md").write_text(questions_content, encoding="utf-8")
        os.rename(staging, map_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    registry_path = resolved_root / "index.md"
    try:
        atomic_write_text_if_changed(registry_path, registry_content)
    except BaseException as exc:
        if not _rollback_initial_map(
            map_dir,
            index_content=index_content,
            questions_content=questions_content,
        ):
            raise ResearchMapError(
                f"registry update failed and new map rollback was unsafe: {map_dir}"
            ) from exc
        raise

    return {
        str(registry_path.resolve()): 1,
        str((map_dir / "index.md").resolve()): 1,
        str((map_dir / "questions.md").resolve()): 1,
    }
