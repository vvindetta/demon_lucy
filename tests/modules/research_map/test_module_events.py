from pathlib import Path

from watchdog.events import FileModifiedEvent

from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.abstract_module import AbstractModule, Context, System
from demon_lucy.modules.research_map import ResearchMap
from demon_lucy.modules.research_map.config import RESEARCH_MAP_TEMPLATE
from demon_lucy.modules.research_map.maps import init_map
from demon_lucy.modules.research_map.models import ResearchMapStatus
from demon_lucy.modules.research_map.nodes import create_node


def _context(root: Path, path: Path, event) -> Context:
    args = parse_args(
        args=["--research-map-root", str(root)],
        template=RESEARCH_MAP_TEMPLATE,
    )
    return Context(
        path=str(path),
        args=args,
        run_mode="daemon",
        event_id="evt-1",
        event=event,
    )


def test_opened_event_has_no_automatic_handler() -> None:
    assert type(ResearchMap()).opened is AbstractModule.opened


def test_node_auto_pass_is_idempotent_and_refreshes_root_status(
    tmp_path: Path,
) -> None:
    root = tmp_path / "maps"
    root.mkdir()
    (root / "index.md").write_text("# Maps\n\n## Active\n", encoding="utf-8")
    init_map(
        root=root,
        map_name="lucy_map",
        title="Lucy",
        goal="Goal",
        seed="Seed",
        registry_summary="Agent summary",
        timestamp="2026-08-08 12:00",
    )
    node = create_node(
        map_dir=root / "lucy_map",
        question="Question?",
        label="Question",
        parent=None,
        summary="Branch summary",
        status=ResearchMapStatus.OPEN,
        timestamp="2026-08-08 12:01",
    )
    node.write_text(
        node.read_text(encoding="utf-8").replace("status: open", "status: done"),
        encoding="utf-8",
    )
    module = ResearchMap()
    ctx = _context(root, node, FileModifiedEvent(str(node)))
    first = module.modified(
        ctx,
        System(global_template=RESEARCH_MAP_TEMPLATE, modules=[module]),
    )
    questions = root / "lucy_map" / "questions.md"
    index = root / "lucy_map" / "index.md"
    questions_mtime = questions.stat().st_mtime_ns
    index_mtime = index.stat().st_mtime_ns
    second = module.modified(
        ctx,
        System(global_template=RESEARCH_MAP_TEMPLATE, modules=[module]),
    )

    assert first is not None
    assert first.changed
    assert second is None
    assert questions.stat().st_mtime_ns == questions_mtime
    assert index.stat().st_mtime_ns == index_mtime
    assert "## Done" in questions.read_text(encoding="utf-8")
    assert "1 - Question [done](b-nodes/1_question.md):" in index.read_text(
        encoding="utf-8"
    )


def test_registry_refresh_preserves_summary_and_is_idempotent(tmp_path: Path) -> None:
    root = tmp_path / "maps"
    root.mkdir()
    (root / "index.md").write_text("# Maps\n\n## Active\n", encoding="utf-8")
    init_map(
        root=root,
        map_name="lucy_map",
        title="Old title",
        goal="Goal",
        seed="Seed",
        registry_summary="Keep this summary",
        timestamp="2026-08-08 12:00",
    )
    index = root / "lucy_map" / "index.md"
    index.write_text(
        index.read_text(encoding="utf-8").replace("# Old title", "# New title"),
        encoding="utf-8",
    )
    module = ResearchMap()
    ctx = _context(root, index, FileModifiedEvent(str(index)))
    first = module.modified(
        ctx,
        System(global_template=RESEARCH_MAP_TEMPLATE, modules=[module]),
    )
    registry = root / "index.md"
    registry_mtime = registry.stat().st_mtime_ns
    second = module.modified(
        ctx,
        System(global_template=RESEARCH_MAP_TEMPLATE, modules=[module]),
    )

    assert first is not None
    assert first.changed == {str(registry.resolve()): 1}
    assert second is None
    assert registry.stat().st_mtime_ns == registry_mtime
    assert "[New title](lucy_map/index.md) - Keep this summary" in registry.read_text(
        encoding="utf-8"
    )
