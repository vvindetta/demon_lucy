from __future__ import annotations

import os
import posixpath
import re
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from demon_lucy.lib.args.parser import Template, get_args_from_file
from demon_lucy.lib.path import (
    canonical_path,
    find_parent_git_repo,
    find_parent_with,
    path_is_inside,
)
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
            "If enabled, keep markdown links and target files in sync both ways: moved files update links, edited links move target files.",
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
        expanded_path = Path(selector).expanduser()
        if expanded_path.is_absolute():
            return path_abs == canonical_path(str(expanded_path))

        has_separator = any(
            separator and separator in selector
            for separator in (os.sep, os.altsep, "/")
        )
        if has_separator or len(expanded_path.parts) > 1:
            return path_abs == canonical_path(str(Path(repo_root) / expanded_path))

        return Path(path_abs).name == str(expanded_path)

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
        link_path = str((Path(repo_root) / Path(source_path).name).absolute())
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
            entries = list(Path(repo_root).iterdir())
        except OSError:
            return None

        for entry in entries:
            if entry.name == ".git":
                continue
            abs_path = str(entry.absolute())
            if not entry.is_symlink():
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
            source_path = Path(link_path).resolve(strict=False)
        except OSError:
            return False
        if not source_path.is_file():
            return False
        known_args, _unknown_args, _arg_lines = get_args_from_file(
            path=str(source_path),
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
    def _is_supported_reference_file(path_value: str) -> bool:
        return Path(path_value).suffix.lower() in REFERENCE_LINK_FILE_EXTENSIONS

    @staticmethod
    def _split_link_destination_and_suffix(destination: str) -> tuple[str, str]:
        parsed = urlsplit(destination)
        suffix = ""
        if parsed.query:
            suffix += f"?{parsed.query}"
        if parsed.fragment:
            suffix += f"#{parsed.fragment}"
        return parsed.path, suffix

    @staticmethod
    def _is_local_link_destination(destination: str) -> bool:
        lowered = destination.strip().lower()
        if not lowered:
            return False
        if lowered.startswith("#"):
            return False
        if "://" in lowered:
            return False
        scheme = urlsplit(lowered).scheme
        return scheme not in {"mailto", "javascript", "data"}

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
        value = path_value.replace(os.sep, "/")
        if os.altsep:
            value = value.replace(os.altsep, "/")
        return posixpath.normpath(value)

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

    def _inline_local_link_path_parts(self, line: str) -> list[tuple[str, str]]:
        links: list[tuple[str, str]] = []
        for match in self._INLINE_LINK_RE.finditer(line):
            link_key = match.group(1)
            parsed = self._parse_inline_destination(match.group(2))
            if not parsed:
                continue
            _leading, destination_token, _tail = parsed
            wrapped = (
                len(destination_token) >= 2
                and destination_token.startswith("<")
                and destination_token.endswith(">")
            )
            destination_value = (
                destination_token[1:-1] if wrapped else destination_token
            ).strip()
            if not self._is_local_link_destination(destination_value):
                continue
            path_part, _trailing_suffix = self._split_link_destination_and_suffix(
                destination_value
            )
            if not path_part:
                continue
            if not self._is_supported_reference_file(path_part):
                continue
            links.append((link_key, path_part))
        return links

    def _edited_link_moves_from_lines(
        self,
        *,
        old_line: str,
        new_line: str,
        markdown_path: str,
        repo_root: str,
    ) -> list[tuple[str, str]]:
        old_links = self._inline_local_link_path_parts(old_line)
        new_links = self._inline_local_link_path_parts(new_line)
        if not old_links or not new_links:
            return []

        markdown_dir = os.path.dirname(markdown_path)
        moves: list[tuple[str, str]] = []
        used_new_indexes: set[int] = set()
        for old_key, old_path_part in old_links:
            for index, (new_key, new_path_part) in enumerate(new_links):
                if index in used_new_indexes:
                    continue
                if old_key != new_key:
                    continue
                used_new_indexes.add(index)
                if old_path_part == new_path_part:
                    break
                old_abs = self._resolve_link_path_part(
                    path_part=old_path_part,
                    markdown_dir=markdown_dir,
                )
                new_abs = self._resolve_link_path_part(
                    path_part=new_path_part,
                    markdown_dir=markdown_dir,
                )
                if not old_abs or not new_abs:
                    break
                if not path_is_inside(old_abs, repo_root):
                    break
                if not path_is_inside(new_abs, repo_root):
                    break
                moves.append((old_abs, new_abs))
                break
        return moves

    @staticmethod
    def _resolve_link_path_part(*, path_part: str, markdown_dir: str) -> str | None:
        if not path_part:
            return None
        path = Path(path_part)
        if path.is_absolute():
            return canonical_path(str(path))
        return canonical_path(str(Path(markdown_dir) / path))

    def _edited_link_moves_from_diff(
        self,
        *,
        markdown_path: str,
        repo_root: str,
    ) -> list[tuple[str, str]]:
        try:
            relative_path = Path(markdown_path).relative_to(repo_root)
        except ValueError:
            return []
        try:
            result = subprocess.run(
                [
                    "git",
                    "diff",
                    "--no-ext-diff",
                    "--unified=0",
                    "--",
                    str(relative_path),
                ],
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=3,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0 or not result.stdout:
            return []

        moves: list[tuple[str, str]] = []
        removed_lines: list[str] = []
        added_lines: list[str] = []

        def flush_hunk() -> None:
            nonlocal removed_lines, added_lines
            for old_line in removed_lines:
                for new_line in added_lines:
                    moves.extend(
                        self._edited_link_moves_from_lines(
                            old_line=old_line,
                            new_line=new_line,
                            markdown_path=markdown_path,
                            repo_root=repo_root,
                        )
                    )
            removed_lines = []
            added_lines = []

        for raw_line in result.stdout.splitlines():
            if raw_line.startswith("@@"):
                flush_hunk()
                continue
            if raw_line.startswith("---") or raw_line.startswith("+++"):
                continue
            if raw_line.startswith("-"):
                removed_lines.append(raw_line[1:])
                continue
            if raw_line.startswith("+"):
                added_lines.append(raw_line[1:])
                continue
        flush_hunk()
        return moves

    def _move_targets_for_edited_links(
        self,
        *,
        ctx: Context,
    ) -> Optional[IgnoreMap]:
        markdown_path = canonical_path(ctx.path)
        if not self._is_supported_reference_file(markdown_path):
            return None
        repo_root = find_parent_git_repo(markdown_path)
        if not repo_root:
            return None
        if not path_is_inside(markdown_path, repo_root):
            return None

        ignore_selectors = list(ctx.config["linker_ignore"])
        if self._is_ignored_path(
            path_value=markdown_path,
            repo_root=repo_root,
            ignore_selectors=ignore_selectors,
        ):
            return None

        changed_paths: IgnoreMap = {}
        seen_moves: set[tuple[str, str]] = set()
        for old_abs, new_abs in self._edited_link_moves_from_diff(
            markdown_path=markdown_path,
            repo_root=repo_root,
        ):
            move = (old_abs, new_abs)
            if move in seen_moves:
                continue
            seen_moves.add(move)
            if old_abs == new_abs:
                continue
            if self._is_ignored_path(
                path_value=old_abs,
                repo_root=repo_root,
                ignore_selectors=ignore_selectors,
            ) or self._is_ignored_path(
                path_value=new_abs,
                repo_root=repo_root,
                ignore_selectors=ignore_selectors,
            ):
                continue
            old_path = Path(old_abs)
            new_path = Path(new_abs)
            if not old_path.is_file():
                continue
            if old_path.is_symlink():
                continue
            if os.path.lexists(new_abs):
                continue
            try:
                new_path.parent.mkdir(parents=True, exist_ok=True)
                old_path.rename(new_path)
            except OSError:
                continue
            changed_paths[old_abs] = changed_paths.get(old_abs, 0) + 1
            changed_paths[new_abs] = changed_paths.get(new_abs, 0) + 1
            self._merge_ignore_maps_into(
                changed_paths,
                self._update_links_for_moved_paths(
                    moved_from_abs=old_abs,
                    moved_to_abs=new_abs,
                    repo_root=repo_root,
                    config=ctx.config,
                ),
            )
        return changed_paths or None

    @staticmethod
    def _merge_ignore_maps_into(
        target: IgnoreMap,
        source: Optional[IgnoreMap],
    ) -> None:
        if not source:
            return
        for path_value, times in source.items():
            if not times:
                continue
            target[path_value] = target.get(path_value, 0) + int(times)

    def _update_links_for_moved_paths(
        self,
        *,
        moved_from_abs: str,
        moved_to_abs: str,
        repo_root: str,
        config: dict,
    ) -> Optional[IgnoreMap]:
        ignore_selectors = list(config["linker_ignore"])
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

        if not path_is_inside(moved_from_abs, repo_root):
            return None
        if not path_is_inside(moved_to_abs, repo_root):
            return None

        return self._update_links_for_moved_paths(
            moved_from_abs=moved_from_abs,
            moved_to_abs=moved_to_abs,
            repo_root=repo_root,
            config=ctx.config,
        )

    def _apply(self, *, path: str, config: dict) -> Optional[IgnoreMap]:
        use_link_top = bool(config["linker_root"])
        auto_cleanup = bool(config["linker_auto_clean_root_links"])
        ignore_selectors = list(config["linker_ignore"])

        if not use_link_top and not auto_cleanup:
            return None

        source_path = str(Path(path).absolute())
        if Path(source_path).is_dir():
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
        link_changed = self._apply(path=ctx.path, config=ctx.config)
        edited_links_changed = None
        if bool(ctx.config["linker_auto_update_md_links"]):
            edited_links_changed = self._move_targets_for_edited_links(ctx=ctx)
        return self._merge_ignore_maps(link_changed, edited_links_changed)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        link_changed = self._apply(path=ctx.path, config=ctx.config)
        moved_links_changed = None
        if bool(ctx.config["linker_auto_update_md_links"]):
            moved_links_changed = self._update_moved_links(ctx=ctx, system=system)
        return self._merge_ignore_maps(link_changed, moved_links_changed)
