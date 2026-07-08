from __future__ import annotations

from pathlib import Path
from typing import Optional

from demon_lucy.lib.args.parser import Template
from demon_lucy.lib.path import find_parent_with
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
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
        (
            "--linker-root",
            bool,
            False,
            "Create symlink in repository root with the same filename as current note.",
            False,
        ),
        (
            "--linker-auto-clean-root-links",
            bool,
            False,
            "If enabled and --linker-root is not set, delete all symlinks from repository root.",
            False,
        ),
        (
            "--linker-ignore",
            str,
            [],
            "Ignore files/links for linker actions. Supports basename or absolute/repo-relative path.",
            False,
        ),
        (
            "--linker-auto-update-md-links",
            bool,
            False,
            "If enabled, keep markdown links and target files in sync both ways: moved files update links, edited links move target files.",
            False,
        ),
    ]

    @staticmethod
    def _merge_ignore_maps(
        left: Optional[IgnoreMap],
        right: Optional[IgnoreMap],
    ) -> Optional[IgnoreMap]:
        if not left and not right:
            return None
        merged: IgnoreMap = {}
        for source in (left or {}, right or {}):
            for path_value, times in source.items():
                if not times:
                    continue
                merged[path_value] = merged.get(path_value, 0) + int(times)
        return merged or None

    def _apply(self, *, path: str, config: dict) -> Optional[IgnoreMap]:
        use_link_top = bool(config["linker_root"])
        auto_cleanup = bool(config["linker_auto_clean_root_links"])
        ignore_selectors = list(config["linker_ignore"])

        if not use_link_top and not auto_cleanup:
            return None

        source_path = str(Path(path).absolute())
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
            )

        if auto_cleanup:
            return cleanup_top_links(
                repo_root=repo_root,
                ignore_selectors=ignore_selectors,
                template=self.template,
            )

        return None

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        _ = system
        return self._apply(path=ctx.path, config=ctx.config)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        _ = system
        link_changed = self._apply(path=ctx.path, config=ctx.config)
        edited_links_changed = None
        if bool(ctx.config["linker_auto_update_md_links"]):
            edited_links_changed = move_targets_for_edited_links(
                markdown_path=ctx.path,
                config=ctx.config,
            )
        return self._merge_ignore_maps(link_changed, edited_links_changed)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        link_changed = self._apply(path=ctx.path, config=ctx.config)
        moved_links_changed = None
        if bool(ctx.config["linker_auto_update_md_links"]):
            moved_links_changed = update_moved_links(
                event=system.event,
                config=ctx.config,
            )
        return self._merge_ignore_maps(link_changed, moved_links_changed)
