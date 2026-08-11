from __future__ import annotations

import logging
import os
from pathlib import Path

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)
from demon_lucy.modules.research_map.artifacts import create_artifact
from demon_lucy.modules.research_map.config import (
    RESEARCH_MAP_TEMPLATE,
    command_from_args,
)
from demon_lucy.modules.research_map.documents import (
    ResearchMapError,
    extract_h1,
    read_document,
)
from demon_lucy.modules.research_map.maps import init_map
from demon_lucy.modules.research_map.models import (
    InitMapCommand,
    NewArtifactCommand,
    NewNodeCommand,
    PutCommand,
    RebuildCommand,
    RegisterMapCommand,
    ResearchMapCommand,
    ValidateCommand,
)
from demon_lucy.modules.research_map.nodes import create_node, reconcile_root_entries
from demon_lucy.modules.research_map.paths import (
    classify_put_target,
    discover_map_dirs,
    map_name_for_path,
    resolve_map_dir,
    resolve_root,
    safe_tmp_file,
)
from demon_lucy.modules.research_map.questions import rebuild_questions
from demon_lucy.modules.research_map.registry import (
    read_registry,
    register_map,
    update_registry_entry,
)
from demon_lucy.modules.research_map.storage import atomic_copy
from demon_lucy.modules.research_map.validation import validate_map


logger = logging.getLogger(__name__)


class ResearchMap(AbstractModule):
    name = "research_map"
    priority = 60
    template = RESEARCH_MAP_TEMPLATE

    def _root(self, args: ParsedArgs) -> Path:
        value = str(args.require("research-map-root").value).strip()
        if not value:
            raise ResearchMapError("--research-map-root is required")
        return resolve_root(value)

    @staticmethod
    def _merge_changed(target: dict[str, int], source: dict[str, int]) -> None:
        for path, count in source.items():
            target[path] = target.get(path, 0) + count

    def _reconcile_map(self, root: Path, map_dir: Path) -> dict[str, int]:
        entries = {entry.map_name: entry for entry in read_registry(root)}
        if map_dir.name not in entries:
            raise ResearchMapError(
                f"map is not registered: {map_dir.name}; use --research-map-register"
            )
        _, body, _ = read_document(map_dir / "index.md")
        label = extract_h1(body)
        if not label:
            raise ResearchMapError(f"missing H1 title in {map_dir / 'index.md'}")
        changed: dict[str, int] = {}
        self._merge_changed(changed, reconcile_root_entries(map_dir))
        self._merge_changed(changed, rebuild_questions(map_dir))
        self._merge_changed(
            changed,
            update_registry_entry(root, map_name=map_dir.name, label=label),
        )
        return changed

    def _validate_or_raise(self, ctx: Context, map_dir: Path) -> None:
        entries = {entry.map_name for entry in read_registry(map_dir.parent)}
        if map_dir.name not in entries:
            raise ResearchMapError(f"map is not registered: {map_dir.name}")
        result = validate_map(map_dir)
        for warning in result.warnings:
            logger.warning(
                log_record(
                    "research_map.validation_warning",
                    id=ctx.event_id,
                    module=self.name,
                    path=map_dir,
                    reason=warning,
                )
            )
        if not result.is_valid:
            details = "; ".join(result.errors[:3])
            if len(result.errors) > 3:
                details += f"; and {len(result.errors) - 3} more"
            raise ResearchMapError(f"research map validation failed: {details}")

    def _run_command(
        self,
        ctx: Context,
        command: ResearchMapCommand,
    ) -> dict[str, int]:
        root = self._root(ctx.args)
        changed: dict[str, int] = {}

        if isinstance(command, InitMapCommand):
            self._merge_changed(
                changed,
                init_map(
                    root=root,
                    map_name=command.map_name,
                    title=command.title,
                    goal=command.goal,
                    seed=command.seed,
                    registry_summary=command.registry_summary,
                ),
            )
            map_dir = resolve_map_dir(root, command.map_name, must_exist=True)
        else:
            map_dir = resolve_map_dir(root, command.map_name, must_exist=True)
            if isinstance(command, RegisterMapCommand):
                self._merge_changed(
                    changed,
                    register_map(
                        root,
                        map_name=command.map_name,
                        label=command.label,
                        summary=command.summary,
                    ),
                )
            elif isinstance(command, NewNodeCommand):
                path = create_node(
                    map_dir=map_dir,
                    question=command.question,
                    label=command.label,
                    parent=command.parent,
                    summary=command.summary,
                    status=command.status,
                )
                changed[str(path.resolve())] = 1
            elif isinstance(command, NewArtifactCommand):
                body_path = safe_tmp_file(command.body_path)
                try:
                    body = body_path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    raise ResearchMapError(
                        f"cannot read artifact body as UTF-8: {body_path}: {exc}"
                    ) from exc
                path = create_artifact(
                    map_dir=map_dir,
                    title=command.title,
                    body=body,
                    question=command.question,
                )
                changed[str(path.resolve())] = 1
            elif isinstance(command, PutCommand):
                source = safe_tmp_file(command.source_path)
                target = classify_put_target(command.target)
                path = map_dir / target.relative_path
                atomic_copy(source, path, overwrite=target.overwrite)
                changed[str(path.resolve())] = 1
            elif isinstance(command, (RebuildCommand, ValidateCommand)):
                pass
            else:
                raise ResearchMapError(f"unsupported research map command: {command}")

        if not isinstance(command, ValidateCommand):
            self._merge_changed(changed, self._reconcile_map(root, map_dir))
        self._validate_or_raise(ctx, map_dir)
        return changed

    def cli(self, ctx: Context, system: System) -> ModuleResult | None:
        _ = system
        map_dir: Path | None = None
        try:
            command = command_from_args(ctx.args)
            root_value = str(ctx.args.require("research-map-root").value).strip()
            if root_value:
                map_dir = Path(root_value).expanduser().absolute() / command.map_name
            changed = self._run_command(ctx, command)
        except (ResearchMapError, OSError, UnicodeError) as exc:
            scope = map_dir or Path(ctx.path)
            message = str(exc)
            logger.error(
                log_record(
                    "research_map.operation_failed",
                    id=ctx.event_id,
                    module=self.name,
                    path=scope,
                    reason="command_failed",
                    error=message,
                )
            )
            safe_notify(
                name=f"research-map:{scope.resolve(strict=False)}",
                message=message,
                args=ctx.args,
                use_rare_mode=True,
            )
            raise ValueError(message) from exc

        logger.info(
            log_record(
                "research_map.command_done",
                id=ctx.event_id,
                module=self.name,
                path=map_dir,
                changed_paths=len(changed),
            )
        )
        return ModuleResult(context=ctx, changed=changed) if changed else None

    def _handle_event(self, ctx: Context) -> ModuleResult | None:
        event = ctx.event
        if event is None or event.is_directory:
            return None
        root = self._root(ctx.args)
        src = os.fsdecode(event.src_path)
        dest = os.fsdecode(getattr(event, "dest_path", ""))
        event_path = dest if event.event_type == "moved" and dest else src
        candidate = Path(event_path).expanduser().absolute().resolve(strict=False)
        root_registry_changed = candidate == root / "index.md"
        map_name = map_name_for_path(root, event_path)
        if not root_registry_changed and map_name is None:
            return None

        changed: dict[str, int] = {}
        map_dir = root / map_name if map_name else root
        rebuild = False
        try:
            if root_registry_changed:
                for current in discover_map_dirs(root).values():
                    self._validate_or_raise(ctx, current)
            elif map_name is not None:
                relative = candidate.relative_to(root / map_name)
                map_dir = resolve_map_dir(root, map_name, must_exist=True)
                if relative == Path("index.md") and event.event_type in {
                    "created",
                    "modified",
                    "moved",
                }:
                    _, body, _ = read_document(map_dir / "index.md")
                    label = extract_h1(body)
                    if not label:
                        raise ResearchMapError(
                            f"missing H1 title in {map_dir / 'index.md'}"
                        )
                    self._merge_changed(
                        changed,
                        update_registry_entry(
                            root,
                            map_name=map_name,
                            label=label,
                        ),
                    )
                rebuild = (
                    len(relative.parts) >= 2
                    and relative.parts[0] == "b-nodes"
                    and relative.suffix == ".md"
                )
                if rebuild:
                    self._merge_changed(
                        changed,
                        reconcile_root_entries(map_dir),
                    )
                    self._merge_changed(changed, rebuild_questions(map_dir))
                self._validate_or_raise(ctx, map_dir)
        except (ResearchMapError, OSError, UnicodeError) as exc:
            logger.error(
                log_record(
                    "research_map.validation_failed",
                    id=ctx.event_id,
                    module=self.name,
                    path=map_dir,
                    reason="automatic_maintenance",
                    error=exc,
                )
            )
            safe_notify(
                name=f"research-map:{map_dir.resolve(strict=False)}",
                message=str(exc),
                args=ctx.args,
                use_rare_mode=True,
            )
            return ModuleResult(context=ctx, changed=changed) if changed else None
        logger.info(
            log_record(
                "research_map.auto_done",
                id=ctx.event_id,
                module=self.name,
                path=map_dir,
                changed_paths=len(changed),
                rebuild_questions=rebuild,
            )
        )
        return ModuleResult(context=ctx, changed=changed) if changed else None

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        _ = system
        return self._handle_event(ctx)

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        _ = system
        return self._handle_event(ctx)

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        _ = system
        return self._handle_event(ctx)

    def deleted(self, ctx: Context, system: System) -> ModuleResult | None:
        _ = system
        return self._handle_event(ctx)
