from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from demon_lucy.lib.args.parser import Template, get_args_from_file
from demon_lucy.lib.path import canonical_path
from demon_lucy.modules.abstract_module import IgnoreMap


def matches_ignore_selector(
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
        separator and separator in selector for separator in (os.sep, os.altsep, "/")
    )
    if has_separator or len(expanded_path.parts) > 1:
        return path_abs == canonical_path(str(Path(repo_root) / expanded_path))

    return Path(path_abs).name == str(expanded_path)


def is_ignored_path(
    *,
    path_value: str,
    repo_root: str,
    ignore_selectors: list[str],
) -> bool:
    for selector in ignore_selectors:
        if matches_ignore_selector(
            path_value=path_value,
            repo_root=repo_root,
            selector_value=selector,
        ):
            return True
    return False


def create_top_link(
    *,
    source_path: str,
    repo_root: str,
    ignore_selectors: list[str],
) -> Optional[IgnoreMap]:
    link_path = str((Path(repo_root) / Path(source_path).name).absolute())
    if link_path == source_path:
        return None

    if is_ignored_path(
        path_value=source_path,
        repo_root=repo_root,
        ignore_selectors=ignore_selectors,
    ):
        return None
    if is_ignored_path(
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


def cleanup_top_links(
    *,
    repo_root: str,
    ignore_selectors: list[str],
    template: Template,
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
        if is_ignored_path(
            path_value=abs_path,
            repo_root=repo_root,
            ignore_selectors=ignore_selectors,
        ):
            continue
        if linked_source_has_linker_root_flag(abs_path, template=template):
            continue

        try:
            os.unlink(abs_path)
        except OSError:
            continue

        deleted[abs_path] = 1

    return deleted or None


def linked_source_has_linker_root_flag(
    link_path: str,
    *,
    template: Template,
) -> bool:
    try:
        source_path = Path(link_path).resolve(strict=False)
    except OSError:
        return False
    if not source_path.is_file():
        return False
    known_args, _unknown_args, _arg_lines = get_args_from_file(
        path=str(source_path),
        template=template,
    )
    return bool(known_args.get("linker_root"))
