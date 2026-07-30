from __future__ import annotations

import os
from typing import Optional, Protocol

from demon_lucy.lib.args.models import Template
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.args.sources import parse_note_args
from demon_lucy.lib.path import canonical_path
from demon_lucy.modules.abstract_module import IgnoreMap


class _StatusFileHost(Protocol):
    template: Template

    def _parse_status_parts(self, values: list[str]) -> list[str]: ...

    def _normalize_banner_settings(
        self,
        text: str,
        speed_milliseconds: int,
        max_characters: int,
    ) -> tuple[str | None, int, int]: ...

    def _normalize_animation_settings(
        self,
        frames: list[str],
        speed_milliseconds: int,
    ) -> tuple[list[str], int]: ...


class StatusFileMixin:
    @staticmethod
    def _merge_ignore_maps(
        left: Optional[IgnoreMap],
        right: Optional[IgnoreMap],
    ) -> Optional[IgnoreMap]:
        if not left and not right:
            return None
        merged: IgnoreMap = {}
        for source in (left or {}, right or {}):
            for path, times in source.items():
                if not times:
                    continue
                merged[path] = merged.get(path, 0) + times
        return merged or None

    @staticmethod
    def _discover_root_status_directories(watch_paths: list[str]) -> list[str]:
        result: list[str] = []
        for watch_path in watch_paths:
            candidate = os.path.join(canonical_path(watch_path), ".status")
            if os.path.isdir(candidate):
                result.append(canonical_path(candidate))

        deduped: list[str] = []
        seen: set[str] = set()
        for status_dir in result:
            if status_dir in seen:
                continue
            seen.add(status_dir)
            deduped.append(status_dir)
        return deduped

    def _status_from_file(
        self: _StatusFileHost,
        path: str,
    ) -> tuple[list[str], str | None, int, int, str, list[str], int]:
        args = parse_args(args=[], template=self.template).merged_with(
            parse_note_args(path, self.template)
        )
        parts = self._parse_status_parts(args.require("status").value)
        banner_text, banner_speed_ms, banner_max_chars = (
            self._normalize_banner_settings(
                args.require("status-banner").value,
                args.require("status-banner-speed-milliseconds").value,
                args.require("status-banner-max-characters").value,
            )
        )
        status_prefix = args.require("status-prefix").value
        ascii_animation_frames, ascii_animation_speed_ms = (
            self._normalize_animation_settings(
                args.require("status-animation").value,
                args.require("status-animation-speed-milliseconds").value,
            )
        )
        if banner_text and not status_prefix:
            status_prefix = "."
        return (
            parts,
            banner_text,
            banner_speed_ms,
            banner_max_chars,
            status_prefix,
            ascii_animation_frames,
            ascii_animation_speed_ms,
        )
