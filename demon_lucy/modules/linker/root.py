from __future__ import annotations

import logging
import os
from pathlib import Path
from demon_lucy.lib.args.models import Template
from demon_lucy.lib.args.sources import parse_note_args
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.path import canonical_path
from demon_lucy.lib.operating_system import OperatingSystem

logger = logging.getLogger(__name__)

_WINDOWS_PRIVILEGE_NOT_HELD = 1314


def matches_ignore_selector(
    *,
    path_value: str,
    repo_root: str,
    selector_value: str,
) -> bool:
    selector = selector_value.strip()
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
    operating_system: OperatingSystem,
    event_id: str,
) -> dict[str, int] | None:
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

    if os.path.lexists(link_path):
        return None

    try:
        link_target = os.path.relpath(source_path, repo_root)
    except ValueError:
        link_target = source_path

    try:
        os.symlink(link_target, link_path)
    except OSError as symlink_error:
        if not (
            operating_system is OperatingSystem.WINDOWS
            and getattr(symlink_error, "winerror", None) == _WINDOWS_PRIVILEGE_NOT_HELD
        ):
            logger.error(
                log_record(
                    "linker.root_link_failed",
                    id=event_id,
                    source=source_path,
                    dest=link_path,
                    reason="symlink_failed",
                    error=symlink_error,
                )
            )
            return None

        logger.warning(
            log_record(
                "linker.root_symlink_unavailable",
                id=event_id,
                source=source_path,
                dest=link_path,
                reason="developer_mode_or_symlink_privilege_required",
                fallback="hardlink",
                message=(
                    "Windows Developer Mode is disabled or symlink privilege "
                    "is unavailable. Lucy will try a hard-link fallback."
                ),
            )
        )
        try:
            os.link(source_path, link_path)
        except OSError as hardlink_error:
            logger.error(
                log_record(
                    "linker.root_link_failed",
                    id=event_id,
                    source=source_path,
                    dest=link_path,
                    reason="hardlink_fallback_failed",
                    error=hardlink_error,
                )
            )
            return None

    return {link_path: 1}


def _is_hard_link_to_source(entry_path: str, source_path: str) -> bool:
    try:
        return os.stat(entry_path).st_nlink > 1 and os.path.samefile(
            entry_path,
            source_path,
        )
    except OSError:
        return False


def cleanup_top_links(
    *,
    repo_root: str,
    source_path: str,
    ignore_selectors: list[str],
    template: Template,
    operating_system: OperatingSystem,
) -> dict[str, int] | None:
    deleted: dict[str, int] = {}
    try:
        entries = list(Path(repo_root).iterdir())
    except OSError:
        return None

    for entry in entries:
        if entry.name == ".git":
            continue
        abs_path = str(entry.absolute())
        is_symlink = entry.is_symlink()
        is_current_windows_hard_link = (
            operating_system is OperatingSystem.WINDOWS
            and _is_hard_link_to_source(abs_path, source_path)
        )
        if not is_symlink and not is_current_windows_hard_link:
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
    parsed_args = parse_note_args(
        path=str(source_path),
        template=template,
    )
    linker_root = parsed_args.find("linker-root")
    return linker_root is not None and linker_root.value
