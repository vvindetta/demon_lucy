from __future__ import annotations

import os
import shlex
from typing import Any, Optional, Protocol

from demon_lucy.modules.abstract_module import IgnoreMap


class _StatusFileHost(Protocol):
    _default_banner_speed_ms: int
    _default_banner_max_chars: int
    _default_animation_speed_ms: int

    def _parse_status_parts(self, values: list[str]) -> list[str]:
        ...

    def _normalize_banner_settings(
        self,
        text_value: Any,
        speed_ms_value: Any,
        max_chars_value: Any,
    ) -> tuple[str | None, int, int]:
        ...

    def _normalize_animation_settings(
        self,
        frames_value: Any,
        speed_ms_value: Any,
    ) -> tuple[list[str], int]:
        ...


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
                merged[path] = merged.get(path, 0) + int(times)
        return merged or None

    @staticmethod
    def _discover_status_dirs_from_path(path: str) -> list[str]:
        result: list[str] = []
        current = os.path.abspath(path)
        if not os.path.isdir(current):
            current = os.path.dirname(current)

        while True:
            for status_dir_name in (".status", ". status"):
                candidate = os.path.join(current, status_dir_name)
                if os.path.isdir(candidate):
                    result.append(os.path.abspath(candidate))
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

        return result

    def _status_from_file(
        self: _StatusFileHost,
        path: str,
    ) -> tuple[list[str], str | None, int, int, str, list[str], int]:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except (OSError, UnicodeDecodeError):
            return (
                [],
                None,
                self._default_banner_speed_ms,
                self._default_banner_max_chars,
                "",
                [],
                self._default_animation_speed_ms,
            )

        status_values: list[str] = []
        banner_text_value: Any = ""
        banner_speed_value: Any = self._default_banner_speed_ms
        banner_max_chars_value: Any = self._default_banner_max_chars
        status_prefix = ""
        ascii_animation_frames_value: list[str] = []
        ascii_animation_speed_value: Any = self._default_animation_speed_ms
        status_flags = (
            "--status",
            "--status-banner",
            "--status-banner-speed-milliseconds",
            "--status-banner-max-characters",
            "--status-prefix",
            "--status-animation",
            "--status-animation-speed-milliseconds",
            "--status-opened-events-disable",
        )
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if (
                "--status" not in stripped
                and "--status-banner" not in stripped
                and "--status-banner-speed-milliseconds" not in stripped
                and "--status-banner-max-characters" not in stripped
                and "--status-prefix" not in stripped
                and "--status-animation" not in stripped
                and "--status-animation-speed-milliseconds" not in stripped
                and "--status-opened-events-disable" not in stripped
            ):
                continue
            try:
                tokens = shlex.split(stripped, comments=False, posix=True)
            except ValueError:
                continue

            i = 0
            while i < len(tokens):
                token_head = tokens[i]
                if token_head not in status_flags:
                    i += 1
                    continue

                if token_head == "--status-prefix":
                    if i + 1 < len(tokens) and tokens[i + 1] not in status_flags:
                        status_prefix = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-banner":
                    if i + 1 < len(tokens) and tokens[i + 1] not in status_flags:
                        banner_text_value = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-banner-speed-milliseconds":
                    if i + 1 < len(tokens) and tokens[i + 1] not in status_flags:
                        banner_speed_value = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-banner-max-characters":
                    if i + 1 < len(tokens) and tokens[i + 1] not in status_flags:
                        banner_max_chars_value = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-animation-speed-milliseconds":
                    if i + 1 < len(tokens) and tokens[i + 1] not in status_flags:
                        ascii_animation_speed_value = tokens[i + 1]
                        i += 2
                    else:
                        i += 1
                    continue

                if token_head == "--status-opened-events-disable":
                    i += 1
                    continue

                if token_head == "--status-animation":
                    j = i + 1
                    while j < len(tokens):
                        token = tokens[j]
                        if token in status_flags:
                            break
                        ascii_animation_frames_value.append(token)
                        j += 1
                    i = j
                    continue

                j = i + 1
                while j < len(tokens):
                    token = tokens[j]
                    if token in status_flags:
                        break
                    status_values.append(token)
                    j += 1
                i = j

        parts = self._parse_status_parts(status_values)
        banner_text, banner_speed_ms, banner_max_chars = (
            self._normalize_banner_settings(
                banner_text_value,
                banner_speed_value,
                banner_max_chars_value,
            )
        )
        ascii_animation_frames, ascii_animation_speed_ms = (
            self._normalize_animation_settings(
                ascii_animation_frames_value,
                ascii_animation_speed_value,
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
