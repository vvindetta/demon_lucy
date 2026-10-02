from __future__ import annotations

import logging
import os
from pathlib import Path

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.path import path_inside_no_symlinks
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
    map_name_for_path,
    resolve_map_dir,
    resolve_root,
    safe_tmp_file,
)
from demon_lucy.modules.research_map.questions import rebuild_questions
from demon_lucy.modules.research_map.registry import register_map
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

    def _reconcile_map(self, map_dir: Path) -> dict[str, int]:
        changed: dict[str, int] = {}
        self._merge_changed(changed, reconcile_root_entries(map_dir))
        self._merge_changed(changed, rebuild_questions(map_dir))
        return changed

    def _validate_or_raise(self, ctx: Context, map_dir: Path) -> None:
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
                    title=command.title,
                    label=command.label,
                    parent=command.parent,
                    summary=command.summary,
                    status=command.status,
                    node_type=command.node_type,
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
                try:
                    path = Path(
                        path_inside_no_symlinks(str(map_dir), str(target.relative_path))
                    )
                except ValueError as exc:
                    raise ResearchMapError(str(exc)) from exc
                atomic_copy(source, path, overwrite=target.overwrite)
                changed[str(path.resolve())] = 1
            elif isinstance(command, (RebuildCommand, ValidateCommand)):
                pass
            else:
                raise ResearchMapError(f"unsupported research map command: {command}")

        if not isinstance(command, ValidateCommand):
            self._merge_changed(changed, self._reconcile_map(map_dir))
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
        if event is None:
            return None
        root = self._root(ctx.args)
        src = os.fsdecode(event.src_path)
        dest = os.fsdecode(getattr(event, "dest_path", ""))
        map_names = {
            name
            for path in (src, dest)
            if path
            if (name := map_name_for_path(root, path)) is not None
        }
        if not map_names:
            return None

        changed: dict[str, int] = {}
        map_dir = root
        try:
            for map_name in sorted(map_names):
                if not (root / map_name).is_dir():
                    continue
                map_dir = resolve_map_dir(root, map_name, must_exist=True)
                self._merge_changed(changed, self._reconcile_map(map_dir))
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
            return ModuleResult(context=ctx, changed=changed) if changed else None
        logger.info(
            log_record(
                "research_map.auto_done",
                id=ctx.event_id,
                module=self.name,
                path=map_dir,
                changed_paths=len(changed),
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
