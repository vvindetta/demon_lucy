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

        today_module = self._find_today_module(system)
        if today_module is None:
            return None

        resolved = today_module._resolve_paths(ctx)
        if not resolved:
            return None
        now_path, _past_path = resolved

        if canonical_path(now_path) != file_path:
            return None

        return today_module.archive_now_to_past(ctx, force=True)
