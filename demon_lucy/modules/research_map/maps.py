from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from demon_lucy.modules.research_map.documents import ResearchMapError, single_line
from demon_lucy.modules.research_map.paths import resolve_map_dir, resolve_root

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


def init_map(
    *,
    root: Path,
    map_name: str,
    title: str,
    goal: str,
    seed: str,
) -> dict[str, int]:
    root = resolve_root(str(root))
    map_dir = resolve_map_dir(root, map_name, must_exist=False)
    if not seed.strip():
        raise ResearchMapError("seed must not be empty")
    replacements = {
        "TITLE": single_line(title, "title"),
        "GOAL": single_line(goal, "goal"),
        "SEED": "\n".join(
            "> " + line if line else ">" for line in seed.strip().splitlines()
        ),
    }
    map_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{map_dir.name}.", dir=map_dir.parent))
    try:
        os.chmod(staging, 0o755)
        (staging / "b-nodes").mkdir()
        for name in ("index.md", "questions.md"):
            content = (TEMPLATE_DIR / name).read_text(encoding="utf-8")
            for key, value in replacements.items():
                content = content.replace(f"{{{{{key}}}}}", value)
            (staging / name).write_text(content, encoding="utf-8")
        resolve_map_dir(root, map_name, must_exist=False)
        os.rename(staging, map_dir)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return {str(map_dir / name): 1 for name in ("index.md", "questions.md")}
