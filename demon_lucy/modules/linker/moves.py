from __future__ import annotations

import os
from pathlib import Path

from watchdog.events import FileSystemEvent

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.path import (
    canonical_path,
    find_parent_git_repo,
    find_parent_with,
    path_is_inside,
)
from demon_lucy.modules.linker.markdown import (
    edited_link_moves_from_diff,
    is_supported_reference_file,
    update_markdown_links_for_moved_paths,
)
from demon_lucy.modules.linker.root import is_ignored_path
from demon_lucy.modules.linker.symlinks import update_symlinks_for_moved_paths


def move_targets_for_edited_links(
    *,
    markdown_path: str,
    args: ParsedArgs,
    event_id: str,
) -> dict[str, int] | None:
    ignore_selectors = args.require("linker-ignore").value
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

    changed_paths: dict[str, int] = {}
    seen_moves: set[tuple[str, str]] = set()
    for old_abs, new_abs in edited_link_moves_from_diff(
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
        _merge_changes_into(
            changed_paths,
            update_links_for_moved_paths(
                moved_from_abs=old_abs,
                moved_to_abs=new_abs,
                repo_root=repo_root,
                args=args,
                event_id=event_id,
            ),
        )
    return changed_paths or None


def _merge_changes_into(
    target: dict[str, int],
    source: dict[str, int] | None,
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
    args: ParsedArgs,
    event_id: str,
) -> dict[str, int] | None:
    ignore_selectors = args.require("linker-ignore").value
    if any(
        is_ignored_path(
            path_value=path,
            repo_root=repo_root,
            ignore_selectors=ignore_selectors,
        )
        for path in (moved_from_abs, moved_to_abs)
    ):
        return None

    changed = (
        update_symlinks_for_moved_paths(
            moved_from_abs=moved_from_abs,
            moved_to_abs=moved_to_abs,
            repo_root=repo_root,
            args=args,
            event_id=event_id,
        )
        or {}
    )
    if is_supported_reference_file(moved_from_abs) and is_supported_reference_file(
        moved_to_abs
    ):
        _merge_changes_into(
            changed,
            update_markdown_links_for_moved_paths(
                moved_from_abs=moved_from_abs,
                moved_to_abs=moved_to_abs,
                repo_root=repo_root,
                ignore_selectors=ignore_selectors,
            ),
        )
    return changed or None


def update_moved_links(
    *,
    event: FileSystemEvent,
    args: ParsedArgs,
    event_id: str,
) -> dict[str, int] | None:
    src_path_raw = str(getattr(event, "src_path", "") or "").strip()
    dest_path_raw = str(getattr(event, "dest_path", "") or "").strip()
    if not src_path_raw or not dest_path_raw:
        return None
    if event.is_directory or os.path.islink(dest_path_raw):
        return None

    moved_from_abs = canonical_path(src_path_raw)
    moved_to_abs = canonical_path(dest_path_raw)
    if moved_from_abs == moved_to_abs:
        return None
    if not os.path.isfile(moved_to_abs):
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
        args=args,
        event_id=event_id,
    )
