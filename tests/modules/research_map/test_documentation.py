import re
from pathlib import Path


def test_research_map_module_has_no_hardcoded_cyrillic_format() -> None:
    module_root = Path(__file__).resolve().parents[3] / "demon_lucy/modules/research_map"
    texts = [
        path.read_text(encoding="utf-8")
        for path in module_root.rglob("*")
        if path.is_file() and path.suffix in {".py", ".md"}
    ]

    assert not any(re.search(r"[А-Яа-яЁё]", text) for text in texts)
