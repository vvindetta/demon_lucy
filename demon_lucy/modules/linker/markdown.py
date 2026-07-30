from __future__ import annotations

import os
import posixpath
import re
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from watchdog.events import FileSystemEvent

from demon_lucy.lib.path import (
    canonical_path,
    find_parent_git_repo,
    find_parent_with,
    path_is_inside,
)
from demon_lucy.modules.abstract_module import IgnoreMap
from demon_lucy.modules.linker.root import is_ignored_path

INLINE_LINK_PATTERN = r"(!?\[[^\]\r\n]*\]\()([^\)\r\n]*)(\))"
REFERENCE_LINK_FILE_EXTENSIONS = {".md", ".markdown", ".mdx"}
_INLINE_LINK_RE = re.compile(INLINE_LINK_PATTERN)


def is_supported_reference_file(path_value: str) -> bool:
    return Path(path_value).suffix.lower() in REFERENCE_LINK_FILE_EXTENSIONS


def _split_link_destination_and_suffix(destination: str) -> tuple[str, str]:
    parsed = urlsplit(destination)
    suffix = ""
    if parsed.query:
        suffix += f"?{parsed.query}"
    if parsed.fragment:
        suffix += f"#{parsed.fragment}"
    return parsed.path, suffix


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


def _normalize_rel_markdown_path(path_value: str) -> str:
    value = path_value.replace(os.sep, "/")
    if os.altsep:
        value = value.replace(os.altsep, "/")
    return posixpath.normpath(value)


def _rebuild_destination_with_style(
    *,
    original_path_part: str,
    new_path_part: str,
) -> str:
    if original_path_part.startswith("./") and not new_path_part.startswith("../"):
        if new_path_part != "." and not new_path_part.startswith("./"):
            return f"./{new_path_part}"
    return new_path_part


def rewrite_inline_links_for_moved_target(
    *,
    markdown_path: str,
    moved_from_path: str,
    moved_to_path: str,
) -> bool:
    try:
        with open(
            markdown_path,
            "r",
            encoding="utf-8",
            newline="",
        ) as file_handle:
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
        parsed = _parse_inline_destination(inside)
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

        if not _is_local_link_destination(destination_value):
            return match.group(0)

        path_part, trailing_suffix = _split_link_destination_and_suffix(
            destination_value
        )
        if not path_part:
            return match.group(0)

        if os.path.isabs(path_part):
            resolved_old_target = canonical_path(path_part)
        else:
            resolved_old_target = canonical_path(os.path.join(markdown_dir, path_part))
        if resolved_old_target != moved_from_abs:
            return match.group(0)

        if os.path.isabs(path_part):
            new_path_part = moved_to_abs
        else:
            rel_new_path = os.path.relpath(moved_to_abs, markdown_dir)
            new_path_part = _normalize_rel_markdown_path(rel_new_path)
            new_path_part = _rebuild_destination_with_style(
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

    updated_content = _INLINE_LINK_RE.sub(_replace, content)
    if not changed or updated_content == content:
        return False

    try:
        with open(
            markdown_path,
            "w",
            encoding="utf-8",
            newline="",
        ) as file_handle:
            file_handle.write(updated_content)
    except OSError:
        return False
    return True


def _inline_local_link_path_parts(line: str) -> list[tuple[str, str]]:
    links: list[tuple[str, str]] = []
    for match in _INLINE_LINK_RE.finditer(line):
        link_key = match.group(1)
        parsed = _parse_inline_destination(match.group(2))
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
        if not _is_local_link_destination(destination_value):
            continue
        path_part, _trailing_suffix = _split_link_destination_and_suffix(
            destination_value
        )
        if not path_part:
            continue
        if not is_supported_reference_file(path_part):
            continue
        links.append((link_key, path_part))
    return links


def _edited_link_moves_from_lines(
    *,
    old_line: str,
    new_line: str,
    markdown_path: str,
    repo_root: str,
) -> list[tuple[str, str]]:
    old_links = _inline_local_link_path_parts(old_line)
    new_links = _inline_local_link_path_parts(new_line)
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
            old_abs = _resolve_link_path_part(
                path_part=old_path_part,
                markdown_dir=markdown_dir,
            )
            new_abs = _resolve_link_path_part(
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


def _resolve_link_path_part(*, path_part: str, markdown_dir: str) -> str | None:
    if not path_part:
        return None
    path = Path(path_part)
    if path.is_absolute():
        return canonical_path(str(path))
    return canonical_path(str(Path(markdown_dir) / path))


def _edited_link_moves_from_diff(
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
                    _edited_link_moves_from_lines(
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


def move_targets_for_edited_links(
    *,
    markdown_path: str,
    ignore_selectors: list[str],
) -> IgnoreMap | None:
    markdown_path = canonical_path(markdown_path)
    if not is_supported_reference_file(markdown_path):
        return None
    repo_root = find_parent_git_repo(markdown_path)
    if not repo_root:
        return None
    if not path_is_inside(markdown_path, repo_root):
        return None

    if is_ignored_path(
        path_value=markdown_path,
        repo_root=repo_root,
        ignore_selectors=ignore_selectors,
    ):
        return None

    changed_paths: IgnoreMap = {}
    seen_moves: set[tuple[str, str]] = set()
    for old_abs, new_abs in _edited_link_moves_from_diff(
        markdown_path=markdown_path,
        repo_root=repo_root,
    ):
        move = (old_abs, new_abs)
        if move in seen_moves:
            continue
        seen_moves.add(move)
        if old_abs == new_abs:
            continue
        if is_ignored_path(
            path_value=old_abs,
            repo_root=repo_root,
            ignore_selectors=ignore_selectors,
        ) or is_ignored_path(
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
        _merge_ignore_maps_into(
            changed_paths,
            update_links_for_moved_paths(
                moved_from_abs=old_abs,
                moved_to_abs=new_abs,
                repo_root=repo_root,
                ignore_selectors=ignore_selectors,
            ),
        )
    return changed_paths or None


def _merge_ignore_maps_into(
    target: IgnoreMap,
    source: IgnoreMap | None,
) -> None:
    if not source:
        return
    for path_value, times in source.items():
        target[path_value] = target.get(path_value, 0) + times


def update_links_for_moved_paths(
    *,
    moved_from_abs: str,
    moved_to_abs: str,
    repo_root: str,
    ignore_selectors: list[str],
) -> IgnoreMap | None:
    if is_ignored_path(
        path_value=moved_from_abs,
        repo_root=repo_root,
        ignore_selectors=ignore_selectors,
    ) or is_ignored_path(
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
            if not is_supported_reference_file(markdown_path):
                continue
            if is_ignored_path(
                path_value=markdown_path,
                repo_root=repo_root,
                ignore_selectors=ignore_selectors,
            ):
                continue
            updated = rewrite_inline_links_for_moved_target(
                markdown_path=markdown_path,
                moved_from_path=moved_from_abs,
                moved_to_path=moved_to_abs,
            )
            if updated:
                changed_paths[markdown_path] = 1

    return changed_paths or None


def update_moved_links(
    *,
    event: FileSystemEvent,
    ignore_selectors: list[str],
) -> IgnoreMap | None:
    src_path_raw = str(getattr(event, "src_path", "") or "").strip()
    dest_path_raw = str(getattr(event, "dest_path", "") or "").strip()
    if not src_path_raw or not dest_path_raw:
        return None

    moved_from_abs = canonical_path(src_path_raw)
    moved_to_abs = canonical_path(dest_path_raw)
    if moved_from_abs == moved_to_abs:
        return None
    if not is_supported_reference_file(moved_from_abs):
        return None
    if not is_supported_reference_file(moved_to_abs):
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

    return update_links_for_moved_paths(
        moved_from_abs=moved_from_abs,
        moved_to_abs=moved_to_abs,
        repo_root=repo_root,
        ignore_selectors=ignore_selectors,
    )
