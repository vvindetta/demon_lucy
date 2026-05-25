from __future__ import annotations

import os
import subprocess
import time
from datetime import datetime
from typing import Optional

from lucy_notes_manager.lib.path import find_parent_with
from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class Today(AbstractModule):
    name: str = "today"
    priority: int = 25

    template: Template = [
        (
            "--today-now-path",
            str,
            "now.md",
            "Path of active note file to archive when stale. Default: now.md",
            True,
        ),
        (
            "--today-past-path",
            str,
            "past.md",
            "Path of archive file. Default: past.md",
            True,
        ),
        (
            "--today-idle-hours",
            float,
            12.0,
            "Archive now file when its last modification age is >= this many hours. Default: 12",
            False,
        ),
        (
            "--today-past",
            bool,
            False,
            "Force move now file to past on this event.",
            False,
        ),
        (
            "--today-force-filesystem-mtime",
            bool,
            False,
            "Force OS filesystem mtime checks even inside Git repositories.",
            False,
        ),
    ]

    @staticmethod
    def _normalize_archive_body(text: str, max_blank_lines: int = 3) -> str:
        lines = text.splitlines()
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

    def _resolve_paths(self, ctx: Context) -> tuple[str, str] | None:
        now_selector = str(ctx.config["today_now_path"]).strip()
        past_selector = str(ctx.config["today_past_path"]).strip()

        event_path = os.path.abspath(ctx.path)
        event_dir = os.path.dirname(event_path)

        now_expanded = os.path.expanduser(now_selector)
        past_expanded = os.path.expanduser(past_selector)

        now_is_abs = os.path.isabs(now_expanded)
        past_is_abs = os.path.isabs(past_expanded)

        now_path = os.path.abspath(now_expanded) if now_is_abs else ""
        past_path = os.path.abspath(past_expanded) if past_is_abs else ""

        if now_is_abs and not past_is_abs:
            base_dir = os.path.dirname(now_path)
        elif past_is_abs and not now_is_abs:
            base_dir = os.path.dirname(past_path)
        else:
            base_dir = event_dir

        if not now_is_abs:
            now_path = os.path.abspath(os.path.join(base_dir, now_expanded))
        if not past_is_abs:
            past_path = os.path.abspath(os.path.join(base_dir, past_expanded))

        if now_path == past_path:
            return None
        return now_path, past_path

    def _git_last_activity_timestamp(self, now_path: str) -> Optional[float]:
        repo_root = find_parent_with(now_path, ".git")
        if not repo_root:
            return None

        rel_path = os.path.relpath(now_path, repo_root)
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

    def _last_activity_timestamp(self, ctx: Context, now_path: str) -> Optional[float]:
        if not ctx.config["today_force_filesystem_mtime"]:
            git_timestamp = self._git_last_activity_timestamp(now_path)
            if git_timestamp is not None:
                return git_timestamp

        try:
            return os.path.getmtime(now_path)
        except OSError:
            return None

    def _is_stale(self, ctx: Context, now_path: str, idle_hours: float) -> bool:
        last_activity = self._last_activity_timestamp(ctx, now_path)
        if last_activity is None:
            return False
        age_seconds = time.time() - float(last_activity)
        return age_seconds >= max(0.0, float(idle_hours)) * 3600.0

    def _append_entry(self, past_path: str, entry: str) -> bool:
        old_content = ""
        if os.path.exists(past_path):
            try:
                with open(past_path, "r", encoding="utf-8") as file_handle:
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
            with open(past_path, "a", encoding="utf-8") as file_handle:
                file_handle.write(sep + entry)
        except OSError:
            return False
        return True

    def _archive_if_needed(
        self, ctx: Context, force: bool = False
    ) -> Optional[IgnoreMap]:
        resolved = self._resolve_paths(ctx)
        if not resolved:
            return None
        now_path, past_path = resolved

        if not force and not self._is_stale(
            ctx, now_path, ctx.config["today_idle_hours"]
        ):
            return None

        try:
            with open(now_path, "r", encoding="utf-8") as now_handle:
                now_text = now_handle.read()
        except OSError:
            return None

        body = self._normalize_archive_body(now_text, max_blank_lines=3)
        if not body:
            return None

        date_label = datetime.now().strftime("%d.%m.%Y")
        entry = f"-- {date_label}\n{body}\n"

        if not self._append_entry(past_path, entry):
            return None

        try:
            with open(now_path, "w", encoding="utf-8") as now_handle:
                now_handle.write("")
        except OSError:
            return None

        return {now_path: 1, past_path: 1}

    def archive_now_to_past(
        self, ctx: Context, force: bool = False
    ) -> Optional[IgnoreMap]:
        return self._archive_if_needed(ctx, force=force)

    def opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_if_needed(ctx, force=bool(ctx.config["today_past"]))

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_if_needed(ctx, force=bool(ctx.config["today_past"]))

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_if_needed(ctx, force=bool(ctx.config["today_past"]))

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_if_needed(ctx, force=bool(ctx.config["today_past"]))
