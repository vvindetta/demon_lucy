from __future__ import annotations

import os

from demon_lucy.lib.runtime_system import RuntimeSystem

_WINDOWS_RESERVED_FILENAME_STEMS = {
    "AUX",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "CON",
    "CONIN$",
    "CONOUT$",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
    "NUL",
    "PRN",
}


class StatusRenderingMixin:
    @staticmethod
    def _rotate_banner_text(text: str, offset: int) -> str:
        if not text:
            return ""
        if len(text) == 1:
            return text
        safe_offset = offset % len(text)
        if safe_offset == 0:
            return text
        return text[safe_offset:] + text[:safe_offset]

    @staticmethod
    def _render_banner_frame(text: str, offset: int, max_chars: int) -> str:
        if not text:
            return ""
        if max_chars <= 0:
            return StatusRenderingMixin._rotate_banner_text(text, offset)

        width = max(1, int(max_chars))
        # Scroll left until text fully disappears into spaces, then restart.
        # Example (text=Working, width=4): Work -> orki -> rkin -> king -> ing  -> ng   -> g    -> "    " -> ...
        stream = text + (" " * width)
        cycle_len = len(stream)
        step = offset % cycle_len
        return stream[step : step + width].ljust(width)

    @staticmethod
    def _sanitize_filename_text(
        name_text: str,
        *,
        runtime_system: RuntimeSystem,
    ) -> str:
        safe_name = str(name_text)
        for item in {"/", "\\", "\x00"}:
            safe_name = safe_name.replace(item, "_")

        if runtime_system != "windows":
            return safe_name

        safe_name = "".join(
            "_" if character in '<>:"|?*' or ord(character) < 32 else character
            for character in safe_name
        ).rstrip(" .")
        device_stem = safe_name.split(".", 1)[0].rstrip(" ").upper()
        if device_stem in _WINDOWS_RESERVED_FILENAME_STEMS:
            safe_name = f"_{safe_name}"
        return safe_name

    @staticmethod
    def _filename_max_bytes(dir_path: str) -> int:
        try:
            value = int(os.pathconf(dir_path, "PC_NAME_MAX"))
            return max(32, value)
        except (AttributeError, OSError, ValueError):
            return 255

    @staticmethod
    def _truncate_utf8_to_bytes(text: str, max_bytes: int) -> str:
        if max_bytes <= 0:
            return ""

        encoded = text.encode("utf-8")
        if len(encoded) <= max_bytes:
            return text

        clipped = encoded[:max_bytes]
        while clipped:
            try:
                return clipped.decode("utf-8")
            except UnicodeDecodeError as exc:
                clipped = clipped[: exc.start]
        return ""

    def _make_filename_candidate(
        self,
        dir_path: str,
        name_text: str,
        *,
        runtime_system: RuntimeSystem,
    ) -> str:
        sanitized = self._sanitize_filename_text(
            name_text,
            runtime_system=runtime_system,
        )
        max_bytes = self._filename_max_bytes(dir_path)
        clipped = self._truncate_utf8_to_bytes(sanitized, max_bytes)
        if runtime_system == "windows":
            clipped = clipped.rstrip(" .")
        if clipped.strip():
            return clipped
        return "-" if runtime_system == "windows" else " - "

    def _pick_available_new_path(
        self,
        *,
        old_path: str,
        dir_path: str,
        base_name: str,
    ) -> str | None:
        candidate_path = os.path.abspath(os.path.join(dir_path, base_name))
        if candidate_path == old_path or not os.path.exists(candidate_path):
            return candidate_path

        max_bytes = self._filename_max_bytes(dir_path)
        for index in range(2, 1000):
            suffix = f" ({index})"
            suffix_bytes = len(suffix.encode("utf-8"))
            if suffix_bytes >= max_bytes:
                return None

            prefix = self._truncate_utf8_to_bytes(base_name, max_bytes - suffix_bytes)
            if not prefix.strip():
                continue
            candidate_name = f"{prefix}{suffix}"
            candidate_path = os.path.abspath(os.path.join(dir_path, candidate_name))
            if candidate_path == old_path or not os.path.exists(candidate_path):
                return candidate_path

        return None
