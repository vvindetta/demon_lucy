from __future__ import annotations

from typing import Any

from lucy_notes_manager.lib.args import flag_to_dest, parse_template_item


class StatusParsingMixin:
    @classmethod
    def _template_defaults(cls) -> dict[str, Any]:
        defaults: dict[str, Any] = {}
        for item in cls.template:
            flag, _typ, default, _desc, _required = parse_template_item(item)
            if isinstance(default, list):
                defaults[flag_to_dest(flag)] = list(default)
            else:
                defaults[flag_to_dest(flag)] = default
        return defaults

    @staticmethod
    def _is_date_token(token: str) -> bool:
        return (
            len(token) == 5
            and token[2] == "-"
            and token[:2].isdigit()
            and token[3:].isdigit()
        )

    @staticmethod
    def _is_time_token(token: str) -> bool:
        return (
            len(token) == 5
            and token[2] == ":"
            and token[:2].isdigit()
            and token[3:].isdigit()
        )

    @staticmethod
    def _is_time_seconds_token(token: str) -> bool:
        return (
            len(token) == 8
            and token[2] == ":"
            and token[5] == ":"
            and token[:2].isdigit()
            and token[3:5].isdigit()
            and token[6:].isdigit()
        )

    @staticmethod
    def _is_git_update_token(token: str) -> bool:
        if len(token) < 2:
            return False
        unit = token[-1].lower()
        return unit in ("m", "h") and token[:-1].isdigit()

    @staticmethod
    def _split_status_prefix(
        stem: str,
        status_prefix: str = "",
    ) -> tuple[dict[str, str], str]:
        tokens: dict[str, str] = {}
        text = stem.replace("\u200b", " ").strip().replace(" | ", " ")
        normalized_prefix = str(status_prefix or "")
        if normalized_prefix and text.startswith(normalized_prefix):
            text = text[len(normalized_prefix) :].lstrip()
        parts = text.split()
        consumed = 0

        for part in parts:
            if StatusParsingMixin._is_date_token(part):
                tokens.setdefault("d", part)
                consumed += 1
                continue
            if StatusParsingMixin._is_time_seconds_token(part):
                tokens.setdefault("ts", part)
                consumed += 1
                continue
            if StatusParsingMixin._is_time_token(part):
                tokens.setdefault("t", part)
                consumed += 1
                continue
            if StatusParsingMixin._is_git_update_token(part):
                tokens.setdefault("g_update", part)
                consumed += 1
                continue
            if part.isdigit():
                tokens.setdefault("g_sync", part)
                consumed += 1
                continue
            break

        clean_parts = parts[consumed:]
        clean = " ".join(clean_parts).strip() if clean_parts else ""
        return tokens, (clean or stem.strip())

    @staticmethod
    def _normalize_status_token(raw: str) -> str:
        return str(raw).strip().lower().replace("_", "-")

    def _parse_status_parts(self, values: list[str]) -> list[str]:
        parts: list[str] = []
        seen: set[str] = set()

        def _append(part: str) -> None:
            if part in seen:
                return
            seen.add(part)
            parts.append(part)

        i = 0
        while i < len(values):
            token = self._normalize_status_token(values[i])
            nxt = (
                self._normalize_status_token(values[i + 1])
                if i + 1 < len(values)
                else ""
            )

            if token in ("date", "d"):
                _append("date")
                i += 1
                continue

            if token in ("time", "t"):
                _append("time")
                i += 1
                continue

            if token in (
                "time-with-seconds",
                "time-seconds",
                "time-sec",
                "timesec",
                "ts",
            ):
                _append("time_with_seconds")
                i += 1
                continue

            if token in ("git", "g"):
                if nxt in ("update", "u"):
                    _append("git_update")
                    i += 2
                    continue
                _append("git_static")
                i += 1
                continue

            if token in ("git-update", "from-git", "update-git"):
                _append("git_update")
                i += 1
                continue

            i += 1

        return parts

    def _normalize_banner_settings(
        self,
        text_value: Any,
        speed_ms_value: Any,
        max_chars_value: Any,
    ) -> tuple[str | None, int, int]:
        banner_text = "" if text_value is None else str(text_value)
        try:
            speed_ms = int(speed_ms_value)
        except (TypeError, ValueError):
            speed_ms = self._default_banner_speed_ms
        try:
            max_chars = int(max_chars_value)
        except (TypeError, ValueError):
            max_chars = self._default_banner_max_chars

        safe_speed_ms = max(1, speed_ms)
        safe_max_chars = max(0, max_chars)
        if banner_text == "":
            return None, safe_speed_ms, safe_max_chars
        return banner_text, safe_speed_ms, safe_max_chars

    def _normalize_animation_settings(
        self,
        frames_value: Any,
        speed_ms_value: Any,
    ) -> tuple[list[str], int]:
        raw_frames: list[Any]
        if frames_value is None:
            raw_frames = []
        elif isinstance(frames_value, (list, tuple)):
            raw_frames = list(frames_value)
        else:
            raw_frames = [frames_value]

        frames = [str(frame) for frame in raw_frames if str(frame) != ""]

        try:
            speed_ms = int(speed_ms_value)
        except (TypeError, ValueError):
            speed_ms = self._default_animation_speed_ms
        safe_speed_ms = max(1, speed_ms)
        return frames, safe_speed_ms
