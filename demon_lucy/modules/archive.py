from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime
from typing import Optional

from demon_lucy.lib.path import canonical_path, find_parent_git_repo, find_parent_with
from demon_lucy.lib.args import Template, delete_args_from_string
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)

logger = logging.getLogger(__name__)


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
            "Selectors may be relative to the event file directory, or absolute "
            "inside the allowed root. The config file path is never archived or "
            "used as a destination. Example: --archive-pair src.md archive.md 12",
            False,
        ),
        (
            "--archive-default-dest-path",
            str,
            "archive.md",
            "Fallback destination archive file when --archive-pair is not provided. "
            "Uses the same archive path restrictions as --archive-pair.",
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

        if len(pair_values) < 2:
            if not allow_event_fallback:
                return None
            default_dest_selector = str(ctx.config["archive_default_dest_path"]).strip()
            if not default_dest_selector:
                return None
            return os.path.basename(ctx.path), default_dest_selector, idle_hours

        src_selector = pair_values[0]
        dest_selector = pair_values[1]
        if len(pair_values) >= 3:
            try:
                idle_hours = float(int(pair_values[2]))
            except (TypeError, ValueError):
                logger.warning(
                    "archive_pair has invalid idle-hours token %r; using default idle-hours value",
                    pair_values[2],
                )
        if len(pair_values) > 3:
            logger.warning(
                "archive_pair has extra trailing tokens; ignoring: %r",
                pair_values[3:],
            )

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

    @staticmethod
    def _is_within_root(path_value: str, root_value: str) -> bool:
        path_abs = canonical_path(path_value)
        root_abs = canonical_path(root_value)
        try:
            return os.path.commonpath([path_abs, root_abs]) == root_abs
        except ValueError:
            return False

    @staticmethod
    def _selector_has_parent_reference(selector: str) -> bool:
        separators = [os.sep]
        if os.altsep:
            separators.append(os.altsep)

        normalized = selector
        for separator in separators:
            normalized = normalized.replace(separator, os.sep)

        return any(part == ".." for part in normalized.split(os.sep))

    def _resolve_safe_selector(
        self,
        *,
        selector: str,
        base_dir: str,
        allowed_root: str,
    ) -> str | None:
        raw_selector = str(selector).strip()
        if not raw_selector:
            return None
        if raw_selector.startswith("~"):
            return None

        expanded_selector = os.path.expanduser(raw_selector)
        if not os.path.isabs(expanded_selector) and self._selector_has_parent_reference(
            expanded_selector
        ):
            return None

        candidate_path = (
            os.path.abspath(expanded_selector)
            if os.path.isabs(expanded_selector)
            else os.path.abspath(os.path.join(base_dir, expanded_selector))
        )
        if os.path.islink(candidate_path):
            return None

        resolved_path = canonical_path(candidate_path)
        if not self._is_within_root(resolved_path, allowed_root):
            return None
        return resolved_path

    def _event_path_is_inside_configured_watch_roots(self, ctx: Context) -> bool:
        raw_watch_paths = ctx.config.get("sys_watch_paths")
        if not raw_watch_paths:
            return True

        values = raw_watch_paths if isinstance(raw_watch_paths, list) else [raw_watch_paths]
        event_path = canonical_path(ctx.path)
        for value in values:
            watch_path = str(value).strip()
            if not watch_path:
                continue
            if self._is_within_root(event_path, canonical_path(watch_path)):
                return True
        return False

    def _archive_allowed_root(self, ctx: Context) -> str | None:
        if not self._event_path_is_inside_configured_watch_roots(ctx):
            return None
        repo_root = find_parent_git_repo(ctx.path)
        if repo_root:
            return canonical_path(repo_root)
        return canonical_path(os.path.dirname(ctx.path))

    def _archive_config_path(self, ctx: Context) -> str | None:
        raw_config_path = ctx.config.get("sys_config_path")
        if raw_config_path is None:
            return None
        config_path = str(raw_config_path).strip()
        if not config_path:
            return None
        return canonical_path(config_path)

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

        event_path = canonical_path(ctx.path)
        event_dir = os.path.dirname(event_path)
        allowed_root = self._archive_allowed_root(ctx)
        if allowed_root is None:
            return None

        src_path = self._resolve_safe_selector(
            selector=src_selector,
            base_dir=event_dir,
            allowed_root=allowed_root,
        )
        dest_path = self._resolve_safe_selector(
            selector=dest_selector,
            base_dir=event_dir,
            allowed_root=allowed_root,
        )
        if not src_path or not dest_path:
            return None

        if src_path == dest_path:
            return None
        config_path = self._archive_config_path(ctx)
        if config_path and (src_path == config_path or dest_path == config_path):
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

    def _append_entry(self, dest_path: str, entry: str) -> tuple[bool, bool]:
        if os.path.islink(dest_path):
            return False, False

        old_content = ""
        if os.path.exists(dest_path):
            old_content_value = self._read_text_no_follow(dest_path)
            if old_content_value is None:
                return False, False
            old_content = old_content_value

        if entry and entry in old_content:
            return True, False

        sep = ""
        if old_content:
            if not old_content.endswith("\n"):
                sep = "\n\n"
            elif not old_content.endswith("\n\n"):
                sep = "\n"

        file_descriptor: int | None = None
        open_flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_NOFOLLOW"):
            open_flags |= os.O_NOFOLLOW

        try:
            file_descriptor = os.open(dest_path, open_flags, 0o666)
            with os.fdopen(file_descriptor, "a", encoding="utf-8") as file_handle:
                file_descriptor = None
                file_handle.write(sep + entry)
        except OSError:
            if file_descriptor is not None:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
            return False, False
        return True, True

    @staticmethod
    def _read_text_no_follow(path_value: str) -> str | None:
        if os.path.islink(path_value):
            return None

        file_descriptor: int | None = None
        open_flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            open_flags |= os.O_NOFOLLOW

        try:
            file_descriptor = os.open(path_value, open_flags)
            with os.fdopen(file_descriptor, "r", encoding="utf-8") as src_handle:
                file_descriptor = None
                return src_handle.read()
        except (OSError, UnicodeDecodeError):
            if file_descriptor is not None:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
            return None

    @staticmethod
    def _truncate_source_file(src_path: str) -> bool:
        if os.path.islink(src_path):
            return False

        file_descriptor: int | None = None
        open_flags = os.O_WRONLY | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            open_flags |= os.O_NOFOLLOW

        try:
            file_descriptor = os.open(src_path, open_flags)
            os.close(file_descriptor)
            return True
        except OSError:
            if file_descriptor is not None:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
            return False

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

        src_text = self._read_text_no_follow(src_path)
        if src_text is None:
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

        append_ok, appended = self._append_entry(dest_path, entry)
        if not append_ok:
            return None

        if not self._truncate_source_file(src_path):
            return None

        changed: IgnoreMap = {src_path: 1}
        if appended:
            changed[dest_path] = 1
        return changed

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
