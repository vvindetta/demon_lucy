from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional

from demon_lucy.lib.args.line_edit import delete_args_from_string
from demon_lucy.lib.args.parser import Template
from demon_lucy.lib.path import canonical_path, find_parent_git_repo, find_parent_with
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)

logger = logging.getLogger(__name__)

_TEXT_MODE = "text"
_FILE_MODE = "file"
_OUTPUT_MODES = {_TEXT_MODE, _FILE_MODE}


@dataclass(frozen=True)
class ArchiveRequest:
    route: str
    output_mode: str
    src_selector: str
    dest_selector: str | None
    idle_hours: float
    force: bool


class Archive(AbstractModule):
    name: str = "archive"
    priority: int = 25

    template: Template = [
        (
            "--archive-pair",
            str,
            [],
            "Force archive through the configured --archive-auto-pair rule. "
            "Optional value: text or file.",
            False,
        ),
        (
            "--archive-local",
            str,
            [],
            "Force archive the current file beside itself. Optional value: text or file.",
            False,
        ),
        (
            "--archive-global",
            str,
            [],
            "Force archive the current file into the global archive destination. "
            "Optional value: text or file.",
            False,
        ),
        (
            "--archive-auto-pair",
            str,
            [],
            "Automatic pair archive rule: <src> <dest> [idle_hours] [text|file]. "
            "In text mode dest is an archive file; in file mode dest is a directory.",
            False,
        ),
        (
            "--archive-auto-local",
            str,
            [],
            "Automatic local archive rule: <src> [idle_hours] [text|file]. "
            "Text mode appends beside the source; file mode writes into .archive/.",
            False,
        ),
        (
            "--archive-auto-global",
            str,
            [],
            "Automatic global archive rule: <src> [idle_hours] [text|file]. "
            "Uses --archive-global-dest-path, or the Git repo root fallback.",
            False,
        ),
        (
            "--archive-default-mode",
            str,
            _TEXT_MODE,
            "Default archive output mode for rules without explicit mode: text or file.",
            False,
        ),
        (
            "--archive-global-dest-path",
            str,
            "",
            "Global archive destination. In text mode this is a file path; in file "
            "mode this is a directory path. If empty, text mode uses archive.md at "
            "the Git repo root, and file mode uses .archive/ at the Git repo root.",
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
            "Text inserted before archive date in text-mode history header. The date "
            "uses the source file's latest Git commit when available, otherwise "
            "today's date. Default: '-- '.",
            False,
        ),
        (
            "--archive-date-suffix",
            str,
            "",
            "Text appended right after archive date in text-mode history header.",
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

    _STRIP_FLAGS = [
        "--archive",
        "--archive-pair",
        "--archive-local",
        "--archive-global",
        "--archive-auto-pair",
        "--archive-auto-local",
        "--archive-auto-global",
        "--archive-default-mode",
        "--archive-global-dest-path",
        "--archive-default-dest-path",
    ]

    @staticmethod
    def _strip_archive_command_lines(lines: list[str]) -> list[str]:
        result: list[str] = []
        for line in lines:
            cleaned = delete_args_from_string(line + "\n", Archive._STRIP_FLAGS).rstrip(
                "\n"
            )
            if cleaned.strip() or not line.strip():
                result.append(cleaned)
        return result

    @staticmethod
    def _string_values(raw_value: object) -> list[str]:
        values = raw_value if isinstance(raw_value, list) else [raw_value]
        result: list[str] = []
        for value in values:
            if value is None:
                continue
            token = str(value).strip()
            if token:
                result.append(token)
        return result

    @staticmethod
    def _flag_present(ctx: Context, key: str) -> bool:
        return key in ctx.arg_lines or bool(Archive._string_values(ctx.config.get(key)))

    @staticmethod
    def _default_output_mode(ctx: Context) -> str | None:
        raw_mode = str(ctx.config["archive_default_mode"]).strip().lower()
        if raw_mode in _OUTPUT_MODES:
            return raw_mode
        logger.warning(
            "Unsupported --archive-default-mode %r; archive rule is ignored",
            ctx.config["archive_default_mode"],
        )
        return None

    @staticmethod
    def _default_idle_hours(ctx: Context) -> float | None:
        try:
            return float(ctx.config["archive_idle_hours"])
        except (TypeError, ValueError):
            logger.warning(
                "Unsupported --archive-idle-hours %r; archive rule is ignored",
                ctx.config.get("archive_idle_hours"),
            )
            return None

    @staticmethod
    def _parse_route_mode(
        *,
        flag: str,
        values: list[str],
        default_mode: str | None,
    ) -> str | None:
        if not values:
            return default_mode
        if len(values) == 1 and values[0].strip().lower() in _OUTPUT_MODES:
            return values[0].strip().lower()
        logger.warning("%s accepts only optional output mode: text or file", flag)
        return None

    @staticmethod
    def _parse_idle_and_mode(
        *,
        flag: str,
        values: list[str],
        default_idle_hours: float | None,
        default_mode: str | None,
    ) -> tuple[float, str] | None:
        idle_hours = default_idle_hours
        output_mode = default_mode

        for token in values:
            normalized = token.strip().lower()
            if normalized in _OUTPUT_MODES:
                output_mode = normalized
                continue
            try:
                idle_hours = float(token)
                continue
            except (TypeError, ValueError):
                logger.warning(
                    "%s has invalid trailing token %r; archive rule is ignored",
                    flag,
                    token,
                )
                return None

        if idle_hours is None or output_mode is None:
            return None
        return idle_hours, output_mode

    def _parse_auto_pair_request(self, ctx: Context) -> ArchiveRequest | None:
        values = self._string_values(ctx.config.get("archive_auto_pair"))
        if not values:
            return None
        if len(values) < 2:
            logger.warning("--archive-auto-pair requires <src> <dest>")
            return None

        parsed_tail = self._parse_idle_and_mode(
            flag="--archive-auto-pair",
            values=values[2:],
            default_idle_hours=self._default_idle_hours(ctx),
            default_mode=self._default_output_mode(ctx),
        )
        if parsed_tail is None:
            return None
        idle_hours, output_mode = parsed_tail
        return ArchiveRequest(
            route="pair",
            output_mode=output_mode,
            src_selector=values[0],
            dest_selector=values[1],
            idle_hours=idle_hours,
            force=False,
        )

    def _parse_auto_local_request(self, ctx: Context) -> ArchiveRequest | None:
        values = self._string_values(ctx.config.get("archive_auto_local"))
        if not values:
            return None
        parsed_tail = self._parse_idle_and_mode(
            flag="--archive-auto-local",
            values=values[1:],
            default_idle_hours=self._default_idle_hours(ctx),
            default_mode=self._default_output_mode(ctx),
        )
        if parsed_tail is None:
            return None
        idle_hours, output_mode = parsed_tail
        return ArchiveRequest(
            route="local",
            output_mode=output_mode,
            src_selector=values[0],
            dest_selector=None,
            idle_hours=idle_hours,
            force=False,
        )

    def _parse_auto_global_request(self, ctx: Context) -> ArchiveRequest | None:
        values = self._string_values(ctx.config.get("archive_auto_global"))
        if not values:
            return None
        parsed_tail = self._parse_idle_and_mode(
            flag="--archive-auto-global",
            values=values[1:],
            default_idle_hours=self._default_idle_hours(ctx),
            default_mode=self._default_output_mode(ctx),
        )
        if parsed_tail is None:
            return None
        idle_hours, output_mode = parsed_tail
        return ArchiveRequest(
            route="global",
            output_mode=output_mode,
            src_selector=values[0],
            dest_selector=None,
            idle_hours=idle_hours,
            force=False,
        )

    def _auto_requests(self, ctx: Context) -> list[ArchiveRequest]:
        requests: list[ArchiveRequest] = []
        for parser in (
            self._parse_auto_pair_request,
            self._parse_auto_local_request,
            self._parse_auto_global_request,
        ):
            request = parser(ctx)
            if request is not None:
                requests.append(request)
        return requests

    def _manual_requests(self, ctx: Context) -> list[ArchiveRequest]:
        route_specs = [
            ("pair", "archive_pair", "--archive-pair"),
            ("local", "archive_local", "--archive-local"),
            ("global", "archive_global", "--archive-global"),
        ]
        present_routes = [
            (route, key, flag)
            for route, key, flag in route_specs
            if self._flag_present(ctx, key)
        ]
        if not present_routes:
            return []
        if len(present_routes) > 1:
            logger.warning(
                "Archive accepts only one manual route at a time: %s",
                ", ".join(flag for _route, _key, flag in present_routes),
            )
            return []

        route, key, flag = present_routes[0]
        mode = self._parse_route_mode(
            flag=flag,
            values=self._string_values(ctx.config.get(key)),
            default_mode=self._default_output_mode(ctx),
        )
        if mode is None:
            return []

        if route == "pair":
            pair_request = self._parse_auto_pair_request(ctx)
            if pair_request is None:
                logger.warning("--archive-pair requires a configured --archive-auto-pair")
                return []
            return [replace(pair_request, output_mode=mode, force=True)]

        idle_hours = self._default_idle_hours(ctx)
        if idle_hours is None:
            return []
        return [
            ArchiveRequest(
                route=route,
                output_mode=mode,
                src_selector=os.path.basename(ctx.path),
                dest_selector=None,
                idle_hours=idle_hours,
                force=True,
            )
        ]

    def _requests_for_context(self, ctx: Context) -> list[ArchiveRequest]:
        manual_requests = self._manual_requests(ctx)
        if manual_requests:
            return manual_requests
        return self._auto_requests(ctx)

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

        values = (
            raw_watch_paths
            if isinstance(raw_watch_paths, list)
            else [raw_watch_paths]
        )
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

    @staticmethod
    def _same_path_or_file(left_path: str, right_path: str) -> bool:
        if canonical_path(left_path) == canonical_path(right_path):
            return True
        try:
            return os.path.samefile(left_path, right_path)
        except OSError:
            return False

    def _rejects_config_path(self, ctx: Context, *paths: str) -> bool:
        config_path = self._archive_config_path(ctx)
        if not config_path:
            return False
        return any(
            self._same_path_or_file(path_value, config_path)
            for path_value in paths
        )

    def _event_base_dir(self, ctx: Context) -> str:
        return os.path.dirname(canonical_path(ctx.path))

    def _resolve_source_path(
        self,
        ctx: Context,
        request: ArchiveRequest,
        *,
        event_dir: str,
        allowed_root: str,
    ) -> str | None:
        src_path = self._resolve_safe_selector(
            selector=request.src_selector,
            base_dir=event_dir,
            allowed_root=allowed_root,
        )
        if not src_path:
            return None
        if self._rejects_config_path(ctx, src_path):
            return None
        return src_path

    def _global_base_dir(self, src_path: str) -> str:
        repo_root = find_parent_git_repo(src_path)
        if repo_root:
            return canonical_path(repo_root)
        return os.path.dirname(canonical_path(src_path))

    def _resolve_text_dest_path(
        self,
        ctx: Context,
        request: ArchiveRequest,
        *,
        src_path: str,
        event_dir: str,
        allowed_root: str,
    ) -> str | None:
        if request.route == "pair":
            if not request.dest_selector:
                return None
            dest_selector = request.dest_selector
            base_dir = event_dir
        elif request.route == "local":
            src_dir = os.path.dirname(src_path)
            archive_dir = os.path.join(src_dir, ".archive")
            dest_selector = (
                os.path.join(".archive", "archive.md")
                if os.path.isdir(archive_dir)
                else "archive.md"
            )
            base_dir = src_dir
        elif request.route == "global":
            dest_selector = str(ctx.config["archive_global_dest_path"]).strip()
            if not dest_selector:
                dest_selector = "archive.md"
            base_dir = self._global_base_dir(src_path)
        else:
            return None

        dest_path = self._resolve_safe_selector(
            selector=dest_selector,
            base_dir=base_dir,
            allowed_root=allowed_root,
        )
        if not dest_path:
            return None
        if src_path == dest_path or self._rejects_config_path(ctx, dest_path):
            return None
        return dest_path

    def _resolve_dest_dir(
        self,
        ctx: Context,
        request: ArchiveRequest,
        *,
        src_path: str,
        event_dir: str,
        allowed_root: str,
    ) -> str | None:
        if request.route == "pair":
            if not request.dest_selector:
                return None
            dest_selector = request.dest_selector
            base_dir = event_dir
        elif request.route == "local":
            dest_selector = ".archive"
            base_dir = os.path.dirname(src_path)
        elif request.route == "global":
            dest_selector = str(ctx.config["archive_global_dest_path"]).strip()
            if not dest_selector:
                dest_selector = ".archive"
            base_dir = self._global_base_dir(src_path)
        else:
            return None

        dest_dir = self._resolve_safe_selector(
            selector=dest_selector,
            base_dir=base_dir,
            allowed_root=allowed_root,
        )
        if not dest_dir:
            return None
        if self._rejects_config_path(ctx, dest_dir):
            return None
        if os.path.exists(dest_dir):
            if os.path.islink(dest_dir) or not os.path.isdir(dest_dir):
                return None
            return canonical_path(dest_dir)

        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError:
            return None

        if os.path.islink(dest_dir) or not os.path.isdir(dest_dir):
            return None
        resolved_dir = canonical_path(dest_dir)
        if not self._is_within_root(resolved_dir, allowed_root):
            return None
        return resolved_dir

    def _resolve_paths(
        self,
        ctx: Context,
        *,
        src_selector: str | None = None,
        dest_selector: str | None = None,
    ) -> tuple[str, str] | None:
        if src_selector is None or dest_selector is None:
            pair_request = self._parse_auto_pair_request(ctx)
            if pair_request is None:
                return None
            src_selector = pair_request.src_selector
            dest_selector = pair_request.dest_selector

        event_dir = self._event_base_dir(ctx)
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
        if self._rejects_config_path(ctx, src_path, dest_path):
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

    def _git_last_commit_timestamp(self, src_path: str) -> Optional[float]:
        repo_root = find_parent_with(src_path, ".git")
        if not repo_root:
            return None

        rel_path = os.path.relpath(src_path, repo_root)
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

    def _archive_entry_timestamp(self, ctx: Context, src_path: str) -> Optional[float]:
        if ctx.config["archive_force_filesystem_mtime"]:
            return None
        return self._git_last_commit_timestamp(src_path)

    @staticmethod
    def _archive_text_date_label(timestamp_value: Optional[float]) -> str:
        if timestamp_value is None:
            return datetime.now().strftime("%d.%m.%Y")

        from datetime import datetime as real_datetime

        return real_datetime.fromtimestamp(timestamp_value).strftime("%d.%m.%Y")

    @staticmethod
    def _archive_file_date_label(timestamp_value: Optional[float]) -> str:
        if timestamp_value is None:
            return datetime.now().strftime("%Y-%m-%d")

        from datetime import datetime as real_datetime

        return real_datetime.fromtimestamp(timestamp_value).strftime("%Y-%m-%d")

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
        if Archive._has_multiple_hard_links(path_value):
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
        if Archive._has_multiple_hard_links(src_path):
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

    @staticmethod
    def _has_multiple_hard_links(path_value: str) -> bool:
        try:
            return os.stat(path_value, follow_symlinks=False).st_nlink > 1
        except OSError:
            return False

    @staticmethod
    def _safe_archive_stem(src_path: str) -> str:
        stem = os.path.splitext(os.path.basename(src_path))[0].strip()
        cleaned = "".join(
            char if char.isalnum() or char in ("-", "_", ".") else "-"
            for char in stem
        ).strip(".-")
        return cleaned or "archive"

    @classmethod
    def _unique_file_archive_path(
        cls,
        *,
        dest_dir: str,
        src_path: str,
        date_label: str,
    ) -> str | None:
        stem = cls._safe_archive_stem(src_path)
        for index in range(1, 1000):
            suffix = "" if index == 1 else f"-{index}"
            candidate = os.path.join(dest_dir, f"{date_label}--{stem}{suffix}.md")
            if not os.path.lexists(candidate):
                return candidate
        return None

    @staticmethod
    def _write_new_archive_file(dest_path: str, body: str) -> bool:
        if os.path.lexists(dest_path):
            return False

        file_descriptor: int | None = None
        open_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            open_flags |= os.O_NOFOLLOW

        try:
            file_descriptor = os.open(dest_path, open_flags, 0o666)
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as file_handle:
                file_descriptor = None
                file_handle.write(body.rstrip() + "\n")
        except OSError:
            if file_descriptor is not None:
                try:
                    os.close(file_descriptor)
                except OSError:
                    pass
            return False
        return True

    def _archive_request(
        self,
        ctx: Context,
        request: ArchiveRequest,
    ) -> Optional[IgnoreMap]:
        event_dir = self._event_base_dir(ctx)
        allowed_root = self._archive_allowed_root(ctx)
        if allowed_root is None:
            return None

        src_path = self._resolve_source_path(
            ctx,
            request,
            event_dir=event_dir,
            allowed_root=allowed_root,
        )
        if not src_path:
            return None

        if not request.force and not self._is_stale(ctx, src_path, request.idle_hours):
            return None

        src_text = self._read_text_no_follow(src_path)
        if src_text is None:
            return None

        body = self._normalize_archive_body(src_text, max_blank_lines=3)
        if not body:
            return None

        timestamp = self._archive_entry_timestamp(ctx, src_path)
        if request.output_mode == _TEXT_MODE:
            dest_path = self._resolve_text_dest_path(
                ctx,
                request,
                src_path=src_path,
                event_dir=event_dir,
                allowed_root=allowed_root,
            )
            if not dest_path:
                return None
            entry = (
                f"{ctx.config['archive_date_prefix']}"
                f"{self._archive_text_date_label(timestamp)}"
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

        if request.output_mode == _FILE_MODE:
            dest_dir = self._resolve_dest_dir(
                ctx,
                request,
                src_path=src_path,
                event_dir=event_dir,
                allowed_root=allowed_root,
            )
            if not dest_dir:
                return None
            date_label = self._archive_file_date_label(timestamp)
            dest_path = self._unique_file_archive_path(
                dest_dir=dest_dir,
                src_path=src_path,
                date_label=date_label,
            )
            if not dest_path or self._rejects_config_path(ctx, dest_path):
                return None
            if not self._write_new_archive_file(dest_path, body):
                return None
            if not self._truncate_source_file(src_path):
                return None
            return {src_path: 1, dest_path: 1}

        return None

    @staticmethod
    def _merge_ignore_maps(items: list[Optional[IgnoreMap]]) -> Optional[IgnoreMap]:
        merged: IgnoreMap = {}
        for item in items:
            if not item:
                continue
            for path_value, times in item.items():
                if not times:
                    continue
                merged[path_value] = merged.get(path_value, 0) + int(times)
        return merged or None

    def _archive_requests_if_needed(self, ctx: Context) -> Optional[IgnoreMap]:
        changes = [
            self._archive_request(ctx, request)
            for request in self._requests_for_context(ctx)
        ]
        return self._merge_ignore_maps(changes)

    def archive_src_to_dest(
        self, ctx: Context, force: bool = False
    ) -> Optional[IgnoreMap]:
        pair_request = self._parse_auto_pair_request(ctx)
        if pair_request is None:
            return None
        if force:
            pair_request = replace(pair_request, force=True)
        return self._archive_request(ctx, pair_request)

    def opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_requests_if_needed(ctx)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_requests_if_needed(ctx)

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_requests_if_needed(ctx)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_requests_if_needed(ctx)
