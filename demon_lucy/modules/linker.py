from __future__ import annotations

import os
import re
from typing import Optional

from demon_lucy.lib.args.parser import Template, get_args_from_file
from demon_lucy.lib.path import canonical_path, find_parent_with
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)

INLINE_LINK_PATTERN = r"(!?\[[^\]\r\n]*\]\()([^\)\r\n]*)(\))"
REFERENCE_LINK_FILE_EXTENSIONS = {".md", ".markdown", ".mdx"}


class Linker(AbstractModule):
    name: str = "linker"
    priority: int = 22

    template: Template = [
        (
            "--linker-root",
            bool,
            False,
            "Create symlink in repository root with the same filename as current note.",
            False,
        ),
        (
            "--linker-auto-clean-root-links",
            bool,
            False,
            "If enabled and --linker-root is not set, delete all symlinks from repository root.",
            False,
        ),
        (
            "--linker-ignore",
            str,
            [],
            "Ignore files/links for linker actions. Supports basename or absolute/repo-relative path.",
            False,
        ),
        (
            "--linker-auto-update-md-links",
            bool,
            False,
            "If enabled, when a note is moved/renamed scan repo markdown files and update links that point to that note.",
            False,
        ),
    ]
    _INLINE_LINK_RE = re.compile(INLINE_LINK_PATTERN)

    @staticmethod
    def _matches_ignore_selector(
        *,
        path_value: str,
        repo_root: str,
        selector_value: str,
    ) -> bool:
        selector = str(selector_value).strip()
        if not selector:
            return False

        path_abs = canonical_path(path_value)
        expanded = os.path.expanduser(selector)
        if os.path.isabs(expanded):
            return path_abs == canonical_path(expanded)

        if os.sep in expanded:
            candidate = os.path.join(repo_root, expanded)
            return path_abs == canonical_path(candidate)

        return os.path.basename(path_abs) == expanded

    def _is_ignored_path(
        self,
        *,
        path_value: str,
        repo_root: str,
        ignore_selectors: list[str],
    ) -> bool:
        for selector in ignore_selectors:
            if self._matches_ignore_selector(
                path_value=path_value,
                repo_root=repo_root,
                selector_value=selector,
            ):
                return True
        return False

    def _create_top_link(
        self,
        *,
        source_path: str,
        repo_root: str,
        ignore_selectors: list[str],
    ) -> Optional[IgnoreMap]:
        link_path = os.path.abspath(
            os.path.join(repo_root, os.path.basename(source_path))
        )
        if link_path == source_path:
            return None

        if self._is_ignored_path(
            path_value=source_path,
            repo_root=repo_root,
            ignore_selectors=ignore_selectors,
        ):
            return None
        if self._is_ignored_path(
            path_value=link_path,
            repo_root=repo_root,
            ignore_selectors=ignore_selectors,
        ):
            return None

        if os.path.islink(link_path):
            if os.path.realpath(link_path) == os.path.realpath(source_path):
                return None
            return None

        if os.path.exists(link_path):
            return None

        try:
            link_target = os.path.relpath(source_path, repo_root)
        except ValueError:
            link_target = source_path

        try:
            os.symlink(link_target, link_path)
        except OSError:
            return None

        return {link_path: 1}

    def _cleanup_top_links(
        self,
        *,
        repo_root: str,
        ignore_selectors: list[str],
    ) -> Optional[IgnoreMap]:
        deleted: IgnoreMap = {}
        try:
            entries = os.listdir(repo_root)
        except OSError:
            return None

        for name in entries:
            if name == ".git":
                continue
            abs_path = os.path.abspath(os.path.join(repo_root, name))
            if not os.path.islink(abs_path):
                continue
            if self._is_ignored_path(
                path_value=abs_path,
                repo_root=repo_root,
                ignore_selectors=ignore_selectors,
            ):
                continue
            if self._linked_source_has_linker_root_flag(abs_path):
                continue

            try:
                os.unlink(abs_path)
            except OSError:
                continue

            deleted[abs_path] = 1

        return deleted or None

    def _linked_source_has_linker_root_flag(self, link_path: str) -> bool:
        try:
            source_path = os.path.realpath(link_path)
        except OSError:
            return False
        if not os.path.isfile(source_path):
            return False
        known_args, _unknown_args, _arg_lines = get_args_from_file(
            path=source_path,
            template=self.template,
        )
        return bool(known_args.get("linker_root"))

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
    def _is_within_root(path_value: str, root_value: str) -> bool:
        path_abs = canonical_path(path_value)
        root_abs = canonical_path(root_value)
        return path_abs == root_abs or path_abs.startswith(root_abs + os.sep)

    @staticmethod
    def _is_supported_reference_file(path_value: str) -> bool:
        _, ext = os.path.splitext(path_value.lower())
        return ext in REFERENCE_LINK_FILE_EXTENSIONS

    @staticmethod
    def _split_link_destination_and_suffix(destination: str) -> tuple[str, str]:
        split_at = len(destination)
        for marker in ("#", "?"):
            index = destination.find(marker)
            if index != -1:
                split_at = min(split_at, index)
        return destination[:split_at], destination[split_at:]

    @staticmethod
    def _is_local_link_destination(destination: str) -> bool:
        lowered = destination.strip().lower()
        if not lowered:
            return False
        if lowered.startswith("#"):
            return False
        if "://" in lowered:
            return False
        return not (
            lowered.startswith("mailto:")
            or lowered.startswith("javascript:")
            or lowered.startswith("data:")
        )

    @staticmethod
    def _parse_inline_destination(inside_text: str) -> tuple[str, str, str] | None:
        leading_len = len(inside_text) - len(inside_text.lstrip())
        leading = inside_text[:leading_len]
        body = inside_text[leading_len:]
        if not body:
            return None

        if body.startswith("<"):
            end_idx = body.find(">")
            if end_idx == -1:
                return None
            destination = body[: end_idx + 1]
            suffix = body[end_idx + 1 :]
            return leading, destination, suffix

        end_idx = 0
        while end_idx < len(body) and not body[end_idx].isspace():
            end_idx += 1
        if end_idx <= 0:
            return None
        destination = body[:end_idx]
        suffix = body[end_idx:]
        return leading, destination, suffix

    @staticmethod
    def _normalize_rel_markdown_path(path_value: str) -> str:
        normalized = os.path.normpath(path_value)
        return normalized.replace(os.sep, "/")

    @staticmethod
    def _rebuild_destination_with_style(
        original_path_part: str,
        new_path_part: str,
    ) -> str:
        if original_path_part.startswith("./") and not new_path_part.startswith("../"):
            if new_path_part != "." and not new_path_part.startswith("./"):
                return f"./{new_path_part}"
        return new_path_part

    def _rewrite_inline_links_for_moved_target(
        self,
        *,
        markdown_path: str,
        moved_from_path: str,
        moved_to_path: str,
    ) -> bool:
        try:
            with open(markdown_path, "r", encoding="utf-8") as file_handle:
                content = file_handle.read()
        except (OSError, UnicodeDecodeError):
            return False

        changed = False
        markdown_dir = os.path.dirname(markdown_path)
        moved_from_abs = canonical_path(moved_from_path)
        moved_to_abs = canonical_path(moved_to_path)

        def _replace(match: re.Match[str]) -> str:
            nonlocal changed
            prefix, inside, suffix = match.group(1), match.group(2), match.group(3)
            parsed = self._parse_inline_destination(inside)
            if not parsed:
                return match.group(0)
            leading, destination_token, tail = parsed

            wrapped = (
                len(destination_token) >= 2
                and destination_token.startswith("<")
                and destination_token.endswith(">")
            )
            destination_value = (
                destination_token[1:-1] if wrapped else destination_token
            ).strip()

            if not self._is_local_link_destination(destination_value):
                return match.group(0)

            path_part, trailing_suffix = self._split_link_destination_and_suffix(
                destination_value
            )
            if not path_part:
                return match.group(0)

            if os.path.isabs(path_part):
                resolved_old_target = canonical_path(path_part)
            else:
                resolved_old_target = canonical_path(
                    os.path.join(markdown_dir, path_part)
                )
            if resolved_old_target != moved_from_abs:
                return match.group(0)

            if os.path.isabs(path_part):
                new_path_part = moved_to_abs
            else:
                rel_new_path = os.path.relpath(moved_to_abs, markdown_dir)
                new_path_part = self._normalize_rel_markdown_path(rel_new_path)
                new_path_part = self._rebuild_destination_with_style(
                    original_path_part=path_part,
                    new_path_part=new_path_part,
                )

            new_destination_value = f"{new_path_part}{trailing_suffix}"
            if wrapped:
                new_destination_token = f"<{new_destination_value}>"
            else:
                new_destination_token = new_destination_value

            changed = True
            return f"{prefix}{leading}{new_destination_token}{tail}{suffix}"

        updated_content = self._INLINE_LINK_RE.sub(_replace, content)
        if not changed or updated_content == content:
            return False

        try:
            with open(markdown_path, "w", encoding="utf-8") as file_handle:
                file_handle.write(updated_content)
        except OSError:
            return False
        return True

    def _update_moved_links(
        self, *, ctx: Context, system: System
    ) -> Optional[IgnoreMap]:
        src_path_raw = str(getattr(system.event, "src_path", "") or "").strip()
        dest_path_raw = str(getattr(system.event, "dest_path", "") or "").strip()
        if not src_path_raw or not dest_path_raw:
            return None

        moved_from_abs = canonical_path(src_path_raw)
        moved_to_abs = canonical_path(dest_path_raw)
        if moved_from_abs == moved_to_abs:
            return None
        if not self._is_supported_reference_file(moved_from_abs):
            return None
        if not self._is_supported_reference_file(moved_to_abs):
            return None

        repo_root = find_parent_with(moved_to_abs, ".git") or find_parent_with(
            moved_from_abs, ".git"
        )
        if not repo_root:
            return None

        if not self._is_within_root(moved_from_abs, repo_root):
            return None
        if not self._is_within_root(moved_to_abs, repo_root):
            return None

        ignore_selectors = list(ctx.config["linker_ignore"])
        if self._is_ignored_path(
            path_value=moved_from_abs,
            repo_root=repo_root,
            ignore_selectors=ignore_selectors,
        ) or self._is_ignored_path(
            path_value=moved_to_abs,
            repo_root=repo_root,
            ignore_selectors=ignore_selectors,
        ):
            return None

        changed_paths: IgnoreMap = {}
        for root, dirs, files in os.walk(repo_root):
            dirs[:] = [name for name in dirs if name != ".git"]
            for file_name in files:
                markdown_path = os.path.abspath(os.path.join(root, file_name))
                if not self._is_supported_reference_file(markdown_path):
                    continue
                if self._is_ignored_path(
                    path_value=markdown_path,
                    repo_root=repo_root,
                    ignore_selectors=ignore_selectors,
                ):
                    continue
                updated = self._rewrite_inline_links_for_moved_target(
                    markdown_path=markdown_path,
                    moved_from_path=moved_from_abs,
                    moved_to_path=moved_to_abs,
                )
                if updated:
                    changed_paths[markdown_path] = 1

        return changed_paths or None

    def _apply(self, *, path: str, config: dict) -> Optional[IgnoreMap]:
        use_link_top = bool(config["linker_root"])
        auto_cleanup = bool(config["linker_auto_clean_root_links"])
        ignore_selectors = list(config["linker_ignore"])

        if not use_link_top and not auto_cleanup:
            return None

        source_path = os.path.abspath(path)
        if os.path.isdir(source_path):
            return None

        repo_root = find_parent_with(source_path, ".git")
        if not repo_root:
            return None

        if use_link_top:
            return self._create_top_link(
                source_path=source_path,
                repo_root=repo_root,
                ignore_selectors=ignore_selectors,
            )

        if auto_cleanup:
            return self._cleanup_top_links(
                repo_root=repo_root,
                ignore_selectors=ignore_selectors,
            )

        return None

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        link_changed = self._apply(path=ctx.path, config=ctx.config)
        moved_links_changed = None
        if bool(ctx.config["linker_auto_update_md_links"]):
            moved_links_changed = self._update_moved_links(ctx=ctx, system=system)
        return self._merge_ignore_maps(link_changed, moved_links_changed)
