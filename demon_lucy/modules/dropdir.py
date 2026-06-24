from __future__ import annotations

import os
import time
from typing import Optional

from demon_lucy.lib.args.parser import Template
from demon_lucy.lib.path import canonical_path, path_is_inside
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from demon_lucy.modules.archive import Archive


class DropDir(AbstractModule):
    name: str = "dropdir"
    priority: int = 24

    template: Template = [
        (
            "--dropdir-archive-clean-paths",
            str,
            [],
            "Directories where moved archive source files are immediately archived into archive destination files. "
            "Example: --dropdir-archive-clean-paths cleanup ~/Notes/cleanup",
            False,
        ),
        (
            "--dropdir-archive-clean-delay-milliseconds",
            int,
            0,
            "Delay before triggering archive clean after instant move-back (milliseconds). "
            "Example: --dropdir-archive-clean-delay-milliseconds 1200",
            False,
        ),
    ]

    def _matches_selector(self, file_path: str, raw_selector: str) -> bool:
        selector = str(raw_selector).strip()
        if not selector:
            return False

        file_dir = canonical_path(os.path.dirname(file_path))
        expanded = os.path.expanduser(selector)

        if os.path.isabs(expanded):
            return path_is_inside(file_dir, expanded)

        if os.sep in selector:
            candidate = os.path.join(os.path.dirname(file_path), expanded)
            return path_is_inside(file_dir, candidate)

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
    def _find_archive_module(system: System) -> Optional[Archive]:
        for module in system.modules:
            if isinstance(module, Archive):
                return module
        return None

    @staticmethod
    def _delay_seconds_from_config(ctx: Context) -> float:
        raw_value = ctx.config.get("dropdir_archive_clean_delay_milliseconds", 0)
        try:
            delay_ms = int(raw_value)
        except (TypeError, ValueError):
            return 0.0
        if delay_ms <= 0:
            return 0.0
        return delay_ms / 1000.0

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        selectors = list(ctx.config.get("dropdir_archive_clean_paths", []))
        if not selectors:
            return None

        file_path = canonical_path(ctx.path)
        if not self._path_in_drop_targets(file_path=file_path, selectors=selectors):
            return None

        action_path, move_back_changed = self._move_back_to_source(
            system=system,
            destination_path=file_path,
        )

        archive_module = self._find_archive_module(system)
        if archive_module is None:
            return move_back_changed

        action_ctx = Context(
            path=action_path,
            config=ctx.config,
            arg_lines=ctx.arg_lines,
        )

        delay_seconds = self._delay_seconds_from_config(ctx)
        if delay_seconds > 0:
            time.sleep(delay_seconds)

        archive_changed = archive_module.archive_src_to_dest(action_ctx, force=True)
        return self._merge_ignore_maps(move_back_changed, archive_changed)
