from __future__ import annotations

import os
from typing import Optional

from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.lib.path import canonical_path
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from lucy_notes_manager.modules.today import Today


class DropDir(AbstractModule):
    name: str = "dropdir"
    priority: int = 24

    template: Template = [
        (
            "--dropdir-today-clean-paths",
            str,
            [],
            "Directories where moved today-now files are immediately archived into today-past. "
            "Example: --dropdir-today-clean-paths cleanup ~/Notes/cleanup",
            False,
        ),
    ]

    @staticmethod
    def _is_under(path_value: str, root_value: str) -> bool:
        path_abs = canonical_path(path_value)
        root_abs = canonical_path(root_value)
        return path_abs == root_abs or path_abs.startswith(root_abs + os.sep)

    def _matches_selector(self, file_path: str, raw_selector: str) -> bool:
        selector = str(raw_selector).strip()
        if not selector:
            return False

        file_dir = canonical_path(os.path.dirname(file_path))
        expanded = os.path.expanduser(selector)

        if os.path.isabs(expanded):
            return self._is_under(file_dir, expanded)

        if os.sep in selector:
            candidate = os.path.join(os.path.dirname(file_path), expanded)
            return self._is_under(file_dir, candidate)

        # Directory name selector: match any parent component.
        dir_components = [part for part in file_dir.split(os.sep) if part]
        return selector in dir_components

    def _path_in_drop_targets(self, file_path: str, selectors: list[str]) -> bool:
        for selector in selectors:
            if self._matches_selector(file_path=file_path, raw_selector=selector):
                return True
        return False

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

    @staticmethod
    def _move_back_to_source(
        system: System,
        destination_path: str,
    ) -> tuple[str, Optional[IgnoreMap]]:
        src_raw = str(getattr(system.event, "src_path", "") or "").strip()
        if not src_raw:
            return destination_path, None

        src_path = canonical_path(src_raw)
        dest_path = canonical_path(destination_path)
        if src_path == dest_path:
            return dest_path, None

        if not os.path.exists(dest_path):
            return dest_path, None

        if os.path.exists(src_path):
            return dest_path, None

        try:
            os.rename(dest_path, src_path)
        except OSError:
            return dest_path, None

        return src_path, {dest_path: 1, src_path: 1}

    @staticmethod
    def _find_today_module(system: System) -> Optional[Today]:
        for module in system.modules:
            if isinstance(module, Today):
                return module
        return None

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        selectors = list(ctx.config.get("dropdir_today_clean_paths", []))
        if not selectors:
            return None

        file_path = canonical_path(ctx.path)
        if not self._path_in_drop_targets(file_path=file_path, selectors=selectors):
            return None

        action_path, move_back_changed = self._move_back_to_source(
            system=system,
            destination_path=file_path,
        )

        today_module = self._find_today_module(system)
        if today_module is None:
            return move_back_changed

        action_ctx = Context(
            path=action_path,
            config=ctx.config,
            arg_lines=ctx.arg_lines,
        )
        resolved = today_module._resolve_paths(action_ctx)
        if not resolved:
            return move_back_changed
        now_path, _past_path = resolved

        if canonical_path(now_path) != canonical_path(action_path):
            return move_back_changed

        today_changed = today_module.archive_now_to_past(action_ctx, force=True)
        return self._merge_ignore_maps(move_back_changed, today_changed)
