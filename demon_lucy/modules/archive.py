from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from typing import Optional

from demon_lucy.lib.path import find_parent_with
from demon_lucy.lib.args import Template, delete_args_from_string
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class Archive(AbstractModule):
    name: str = "archive"
    priority: int = 25

    template: Template = [
        (
            "--archive",
            bool,
            False,
            "Force archive move for this event.",
            False,
        ),
        (
            "--archive-pair",
            str,
            [],
            "Archive pair settings: <src> <dest> [idle_hours_int]. "
            "Example: --archive-pair src.md archive.md 12",
            False,
        ),
        (
            "--archive-default-dest-path",
            str,
            "archive.md",
            "Fallback destination archive file when --archive-pair is not provided.",
            False,
        ),
        (
            "--archive-idle-hours",
            float,
            12.0,
            "Archive source file when its last modification age is >= this many hours. Default: 12",
            False,
        ),
        (
            "--archive-date-prefix",
            str,
            "-- ",
            "Text inserted before archive date in history header. Default: '-- '.",
            False,
        ),
        (
            "--archive-date-suffix",
            str,
            "",
            "Text appended right after archive date in history header.",
            False,
        ),
        (
            "--archive-force-filesystem-mtime",
            bool,
            False,
            "Force OS filesystem mtime checks even inside Git repositories.",
            False,
        ),
    ]

    @staticmethod
    def _strip_archive_command_lines(lines: list[str]) -> list[str]:
        """
        Remove archive-trigger flag segments using the shared args helper
        so archive command flags do not get copied into history.
        """
        result: list[str] = []
        for line in lines:
            cleaned = delete_args_from_string(
                line + "\n",
                ["--archive", "--archive-pair", "--archive-default-dest-path"],
            ).rstrip("\n")
            if cleaned.strip() or not line.strip():
                result.append(cleaned)
        return result

    @staticmethod
    def _effective_archive_settings(
        ctx: Context, *, allow_event_fallback: bool = False
    ) -> tuple[str, str, float] | None:
        try:
            idle_hours = float(ctx.config["archive_idle_hours"])
        except (TypeError, ValueError):
            return None

        raw_pair = ctx.config["archive_pair"]
        pair_values: list[str] = []
        if isinstance(raw_pair, list):
            for value in raw_pair:
                token = str(value).strip()
                if token:
                    pair_values.append(token)
        elif isinstance(raw_pair, str):
            token = raw_pair.strip()
            if token:
                pair_values.append(token)

        if len(pair_values) not in (2, 3):
            if not allow_event_fallback:
                return None
            default_dest_selector = str(ctx.config["archive_default_dest_path"]).strip()
            if not default_dest_selector:
                return None
            return ctx.path, default_dest_selector, idle_hours

        src_selector = pair_values[0]
        dest_selector = pair_values[1]
        if len(pair_values) == 3:
            try:
                idle_hours = float(int(pair_values[2]))
            except (TypeError, ValueError):
                return None

        return src_selector, dest_selector, idle_hours

    @staticmethod
    def _normalize_archive_body(text: str, max_blank_lines: int = 3) -> str:
        lines = text.splitlines()
        lines = Archive._strip_archive_command_lines(lines)
        if not lines:
            return ""

        start = 0
        while start < len(lines) and not lines[start].strip():
            start += 1

        end = len(lines) - 1
        while end >= start and not lines[end].strip():
            end -= 1

        if start > end:
            return ""

        core = lines[start : end + 1]
        result: list[str] = []
        blank_run = 0

        for line in core:
            if line.strip():
                blank_run = 0
                result.append(line)
                continue

            blank_run += 1
            if blank_run <= max_blank_lines:
                result.append("")

        return "\n".join(result)

    def _resolve_paths(
        self,
        ctx: Context,
        *,
        src_selector: str | None = None,
        dest_selector: str | None = None,
    ) -> tuple[str, str] | None:
        if src_selector is None or dest_selector is None:
            settings = self._effective_archive_settings(ctx)
            if not settings:
                return None
            src_selector, dest_selector, _idle_hours = settings

        event_path = os.path.abspath(ctx.path)
        event_dir = os.path.dirname(event_path)

        src_expanded = os.path.expanduser(src_selector)
        dest_expanded = os.path.expanduser(dest_selector)

        src_is_abs = os.path.isabs(src_expanded)
        dest_is_abs = os.path.isabs(dest_expanded)

        src_path = os.path.abspath(src_expanded) if src_is_abs else ""
        dest_path = os.path.abspath(dest_expanded) if dest_is_abs else ""

        if src_is_abs and not dest_is_abs:
            base_dir = os.path.dirname(src_path)
        elif dest_is_abs and not src_is_abs:
            base_dir = os.path.dirname(dest_path)
        else:
            base_dir = event_dir

        if not src_is_abs:
            src_path = os.path.abspath(os.path.join(base_dir, src_expanded))
        if not dest_is_abs:
            dest_path = os.path.abspath(os.path.join(base_dir, dest_expanded))

        if src_path == dest_path:
            return None
        return src_path, dest_path

    def _git_last_activity_timestamp(self, src_path: str) -> Optional[float]:
        repo_root = find_parent_with(src_path, ".git")
        if not repo_root:
            return None

        rel_path = os.path.relpath(src_path, repo_root)
        try:
            status_result = subprocess.run(
                ["git", "status", "--porcelain", "--", rel_path],
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        if status_result.returncode != 0:
            return None

        # If file has uncommitted changes, mtime is the fresher signal.
        if (status_result.stdout or "").strip():
            return None

        try:
            log_result = subprocess.run(
                ["git", "log", "-1", "--format=%ct", "--", rel_path],
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=2.0,
            )
        except (OSError, subprocess.SubprocessError):
            return None

        if log_result.returncode != 0:
            return None

        timestamp_raw = (log_result.stdout or "").strip()
        if not timestamp_raw:
            return None

        try:
            return float(timestamp_raw)
        except ValueError:
            return None

    def _last_activity_timestamp(self, ctx: Context, src_path: str) -> Optional[float]:
        if not ctx.config["archive_force_filesystem_mtime"]:
            git_timestamp = self._git_last_activity_timestamp(src_path)
            if git_timestamp is not None:
                return git_timestamp

        try:
            return os.path.getmtime(src_path)
        except OSError:
            return None

    def _is_stale(self, ctx: Context, src_path: str, idle_hours: float) -> bool:
        last_activity = self._last_activity_timestamp(ctx, src_path)
        if last_activity is None:
            return False
        age_seconds = time.time() - float(last_activity)
        return age_seconds >= max(0.0, float(idle_hours)) * 3600.0

    def _append_entry(self, dest_path: str, entry: str) -> bool:
        old_content = ""
        if os.path.exists(dest_path):
            try:
                with open(dest_path, "r", encoding="utf-8") as file_handle:
                    old_content = file_handle.read()
            except OSError:
                return False

        sep = ""
        if old_content:
            if not old_content.endswith("\n"):
                sep = "\n\n"
            elif not old_content.endswith("\n\n"):
                sep = "\n"

        try:
            with open(dest_path, "a", encoding="utf-8") as file_handle:
                file_handle.write(sep + entry)
        except OSError:
            return False
        return True

    def _archive_if_needed(
        self, ctx: Context, force: bool = False
    ) -> Optional[IgnoreMap]:
        settings = self._effective_archive_settings(
            ctx,
            allow_event_fallback=force,
        )
        if not settings:
            return None
        src_selector, dest_selector, idle_hours = settings

        resolved = self._resolve_paths(
            ctx,
            src_selector=src_selector,
            dest_selector=dest_selector,
        )
        if not resolved:
            return None
        src_path, dest_path = resolved

        if not force and not self._is_stale(ctx, src_path, idle_hours):
            return None

        try:
            with open(src_path, "r", encoding="utf-8") as src_handle:
                src_text = src_handle.read()
        except OSError:
            return None

        body = self._normalize_archive_body(src_text, max_blank_lines=3)
        if not body:
            return None

        date_label = datetime.now().strftime("%d.%m.%Y")
        entry = (
            f"{ctx.config['archive_date_prefix']}"
            f"{date_label}"
            f"{ctx.config['archive_date_suffix']}\n{body}\n"
        )

        if not self._append_entry(dest_path, entry):
            return None

        try:
            with open(src_path, "w", encoding="utf-8") as src_handle:
                src_handle.write("")
        except OSError:
            return None

        return {src_path: 1, dest_path: 1}

    def archive_src_to_dest(
        self, ctx: Context, force: bool = False
    ) -> Optional[IgnoreMap]:
        return self._archive_if_needed(ctx, force=force)

    def opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        force = bool(ctx.config["archive"])
        return self._archive_if_needed(ctx, force=force)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        force = bool(ctx.config["archive"])
        return self._archive_if_needed(ctx, force=force)

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        force = bool(ctx.config["archive"])
        return self._archive_if_needed(ctx, force=force)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        force = bool(ctx.config["archive"])
        return self._archive_if_needed(ctx, force=force)
