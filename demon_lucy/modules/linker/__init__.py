from __future__ import annotations

from pathlib import Path

from demon_lucy.lib.args.models import KnownArg, Template
from demon_lucy.lib.path import find_parent_with
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)
from demon_lucy.modules.linker.markdown import (
    move_targets_for_edited_links,
    update_moved_links,
)
from demon_lucy.modules.linker.root import cleanup_top_links, create_top_link


class Linker(AbstractModule):
    name: str = "linker"
    priority: int = 22

    template: Template = [
        KnownArg(
            name="linker-root",
            value_type=bool,
            default=False,
            description=(
                "Create a link in the repository root with the current note "
                "filename. Windows falls back to a hard link when symlink "
                "privilege is unavailable."
            ),
        ),
        KnownArg(
            name="linker-auto-clean-root-links",
            value_type=bool,
            default=False,
            description=(
                "If enabled and --linker-root is not set, delete managed root links."
            ),
        ),
        KnownArg(
            name="linker-ignore",
            value_type=str,
            default=[],
            description="Ignore files/links for linker actions. Supports basename or absolute/repo-relative path.",
        ),
        KnownArg(
            name="linker-auto-update-md-links",
            value_type=bool,
            default=False,
            description="If enabled, keep markdown links and target files in sync both ways: moved files update links, edited links move target files.",
        ),
    ]

    @staticmethod
    def _merge_changes(
        left: dict[str, int] | None,
        right: dict[str, int] | None,
    ) -> dict[str, int] | None:
        if not left and not right:
            return None
        merged: dict[str, int] = {}
        for source in (left or {}, right or {}):
            for path_value, times in source.items():
                merged[path_value] = merged.get(path_value, 0) + times
        return merged or None

    def _apply(
        self,
        ctx: Context,
        system: System,
    ) -> dict[str, int] | None:
        use_link_top = ctx.args.require("linker-root").value
        auto_cleanup = ctx.args.require("linker-auto-clean-root-links").value
        ignore_selectors = ctx.args.require("linker-ignore").value

        if not use_link_top and not auto_cleanup:
            return None

        source_path = str(Path(ctx.path).absolute())
        if Path(source_path).is_dir():
            return None

        repo_root = find_parent_with(source_path, ".git")
        if not repo_root:
            return None

        if use_link_top:
            return create_top_link(
                source_path=source_path,
                repo_root=repo_root,
                ignore_selectors=ignore_selectors,
                operating_system=system.operating_system,
                event_id=ctx.event_id,
            )

        return cleanup_top_links(
            repo_root=repo_root,
            source_path=source_path,
            ignore_selectors=ignore_selectors,
            template=self.template,
            operating_system=system.operating_system,
        )

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        changed = self._apply(ctx, system)
        return ModuleResult(context=ctx, changed=changed) if changed else None

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        link_changed = self._apply(ctx, system)
        edited_links_changed = None
        if ctx.args.require("linker-auto-update-md-links").value:
            edited_links_changed = move_targets_for_edited_links(
                markdown_path=ctx.path,
                ignore_selectors=ctx.args.require("linker-ignore").value,
            )
        changed = self._merge_changes(link_changed, edited_links_changed)
        return ModuleResult(context=ctx, changed=changed) if changed else None

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        link_changed = self._apply(ctx, system)
        moved_links_changed = None
        if (
            ctx.event is not None
            and ctx.args.require("linker-auto-update-md-links").value
        ):
            moved_links_changed = update_moved_links(
                event=ctx.event,
                ignore_selectors=ctx.args.require("linker-ignore").value,
            )
        changed = self._merge_changes(link_changed, moved_links_changed)
        return ModuleResult(context=ctx, changed=changed) if changed else None
