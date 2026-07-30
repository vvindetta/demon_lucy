from __future__ import annotations


class StatusParsingMixin:
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
        if status_prefix and text.startswith(status_prefix):
            text = text[len(status_prefix) :].lstrip()
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
        return raw.strip().lower()

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

    @staticmethod
    def _normalize_banner_settings(
        text: str,
        speed_milliseconds: int,
        max_characters: int,
    ) -> tuple[str | None, int, int]:
        return (
            text or None,
            max(1, speed_milliseconds),
            max(0, max_characters),
        )

    @staticmethod
    def _normalize_animation_settings(
        frames: list[str],
        speed_milliseconds: int,
    ) -> tuple[list[str], int]:
        return [frame for frame in frames if frame], max(
            1,
            speed_milliseconds,
        )
