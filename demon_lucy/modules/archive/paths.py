from __future__ import annotations

import os

from demon_lucy.lib.path import canonical_path, find_parent_git_repo, path_is_inside
from demon_lucy.modules.abstract_module import Context

from demon_lucy.modules.archive import notify
from demon_lucy.modules.archive.types import ArchiveRequest


def selector_has_parent_reference(selector: str) -> bool:
    separators = [os.sep]
    if os.altsep:
        separators.append(os.altsep)

    normalized = selector
    for separator in separators:
        normalized = normalized.replace(separator, os.sep)

    return any(part == ".." for part in normalized.split(os.sep))


def event_path_is_inside_configured_watch_roots(ctx: Context) -> bool:
    watch_roots = configured_watch_roots(ctx)
    if not watch_roots:
        return True

    event_path = canonical_path(ctx.path)
    return any(path_is_inside(event_path, root) for root in watch_roots)


def configured_watch_roots(ctx: Context) -> list[str]:
    raw_watch_paths = ctx.config.get("sys_watch_paths")
    if not raw_watch_paths:
        return []

    values = raw_watch_paths if isinstance(raw_watch_paths, list) else [raw_watch_paths]
    roots: list[str] = []
    for value in values:
        watch_path = str(value).strip()
        if watch_path:
            roots.append(canonical_path(watch_path))
    return roots


def archive_allowed_root(ctx: Context) -> str | None:
    if not event_path_is_inside_configured_watch_roots(ctx):
        notify.security_block(
            ctx,
            reason="event_outside_watch_roots",
            role="event",
            target=canonical_path(ctx.path),
        )
        return None
    repo_root = find_parent_git_repo(ctx.path)
    if repo_root:
        return canonical_path(repo_root)
    return canonical_path(os.path.dirname(ctx.path))


def source_allowed_root(
    ctx: Context,
    *,
    selector: str,
    current_allowed_root: str,
) -> str:
    raw_selector = str(selector).strip()
    if not raw_selector or raw_selector.startswith("~"):
        return current_allowed_root

    expanded_selector = os.path.expanduser(raw_selector)
    if not os.path.isabs(expanded_selector):
        return current_allowed_root

    source_path = canonical_path(expanded_selector)
    if path_is_inside(source_path, current_allowed_root):
        return current_allowed_root

    matching_roots = [
        root
        for root in configured_watch_roots(ctx)
        if path_is_inside(source_path, root)
    ]
    if not matching_roots:
        return current_allowed_root

    repo_root = find_parent_git_repo(source_path)
    if repo_root:
        return canonical_path(repo_root)
    return max(matching_roots, key=len)


def archive_config_path(ctx: Context) -> str | None:
    raw_config_path = ctx.config.get("sys_config_path")
    if raw_config_path is None:
        return None
    config_path = str(raw_config_path).strip()
    if not config_path:
        return None
    return canonical_path(config_path)


def same_path_or_file(left_path: str, right_path: str) -> bool:
    if canonical_path(left_path) == canonical_path(right_path):
        return True
    try:
        return os.path.samefile(left_path, right_path)
    except OSError:
        return False


def rejects_config_path(ctx: Context, *path_values: str) -> bool:
    config_path = archive_config_path(ctx)
    if not config_path:
        return False
    return any(same_path_or_file(path_value, config_path) for path_value in path_values)


def event_base_dir(ctx: Context) -> str:
    return os.path.dirname(canonical_path(ctx.path))


def source_flag_for_request(request: ArchiveRequest) -> str:
    if request.route == "pair":
        return "--archive-auto-pair"
    if request.route == "local":
        return "--archive-local" if request.force else "--archive-auto-local"
    if request.route == "global":
        return "--archive-global" if request.force else "--archive-auto-global"
    return "--archive"


def dest_flag_for_request(request: ArchiveRequest) -> str:
    if request.route == "pair":
        return "--archive-auto-pair"
    if request.route == "local":
        return "--archive-local" if request.force else "--archive-auto-local"
    if request.route == "global":
        return "--archive-global-dest-path"
    return "--archive"


def resolve_safe_selector(
    ctx: Context,
    *,
    selector: str,
    base_dir: str,
    allowed_root: str,
    role: str,
    flag: str | None = None,
) -> str | None:
    raw_selector = str(selector).strip()
    if not raw_selector:
        notify.invalid_rule(
            ctx,
            flag=flag or role,
            reason="empty_selector",
            role=role,
        )
        return None
    if raw_selector.startswith("~"):
        notify.security_block(
            ctx,
            reason="home_selector_rejected",
            role=role,
            flag=flag,
            selector=raw_selector,
            allowed_root=allowed_root,
        )
        return None

    expanded_selector = os.path.expanduser(raw_selector)
    if not os.path.isabs(expanded_selector) and selector_has_parent_reference(
        expanded_selector
    ):
        notify.security_block(
            ctx,
            reason="parent_selector_rejected",
            role=role,
            flag=flag,
            selector=raw_selector,
            allowed_root=allowed_root,
        )
        return None

    candidate_path = (
        os.path.abspath(expanded_selector)
        if os.path.isabs(expanded_selector)
        else os.path.abspath(os.path.join(base_dir, expanded_selector))
    )
    if os.path.islink(candidate_path):
        notify.security_block(
            ctx,
            reason="symlink_path_rejected",
            role=role,
            flag=flag,
            selector=raw_selector,
            target=candidate_path,
            allowed_root=allowed_root,
        )
        return None

    resolved_path = canonical_path(candidate_path)
    if not path_is_inside(resolved_path, allowed_root):
        notify.security_block(
            ctx,
            reason="outside_allowed_root",
            role=role,
            flag=flag,
            selector=raw_selector,
            target=resolved_path,
            allowed_root=allowed_root,
        )
        return None
    return resolved_path


def resolve_source_path(
    ctx: Context,
    request: ArchiveRequest,
    *,
    base_dir: str,
    allowed_root: str,
) -> str | None:
    src_path = resolve_safe_selector(
        ctx,
        selector=request.src_selector,
        base_dir=base_dir,
        allowed_root=allowed_root,
        role="src",
        flag=source_flag_for_request(request),
    )
    if not src_path:
        return None
    if rejects_config_path(ctx, src_path):
        notify.security_block(
            ctx,
            reason="config_path_rejected",
            role="src",
            flag=source_flag_for_request(request),
            target=src_path,
        )
        return None
    return src_path


def global_base_dir(src_path: str) -> str:
    repo_root = find_parent_git_repo(src_path)
    if repo_root:
        return canonical_path(repo_root)
    return os.path.dirname(canonical_path(src_path))


def resolve_text_dest_path(
    ctx: Context,
    request: ArchiveRequest,
    *,
    src_path: str,
    base_dir: str,
    allowed_root: str,
) -> str | None:
    if request.route == "pair":
        if not request.dest_selector:
            return None
        dest_selector = request.dest_selector
        dest_base_dir = base_dir
    elif request.route == "local":
        src_dir = os.path.dirname(src_path)
        archive_dir = os.path.join(src_dir, ".archive")
        dest_selector = (
            os.path.join(".archive", "archive.md")
            if os.path.isdir(archive_dir)
            else "archive.md"
        )
        dest_base_dir = src_dir
    elif request.route == "global":
        dest_selector = str(ctx.config["archive_global_dest_path"]).strip()
        if not dest_selector:
            dest_selector = "archive.md"
        dest_base_dir = global_base_dir(src_path)
    else:
        return None

    dest_path = resolve_safe_selector(
        ctx,
        selector=dest_selector,
        base_dir=dest_base_dir,
        allowed_root=allowed_root,
        role="dest",
        flag=dest_flag_for_request(request),
    )
    if not dest_path:
        return None
    if src_path == dest_path:
        notify.invalid_rule(
            ctx,
            flag=dest_flag_for_request(request),
            reason="src_dest_same_path",
            src=src_path,
            dest=dest_path,
        )
        return None
    if rejects_config_path(ctx, dest_path):
        notify.security_block(
            ctx,
            reason="config_path_rejected",
            role="dest",
            flag=dest_flag_for_request(request),
            target=dest_path,
        )
        return None
    return dest_path


def resolve_dest_dir(
    ctx: Context,
    request: ArchiveRequest,
    *,
    src_path: str,
    base_dir: str,
    allowed_root: str,
) -> str | None:
    if request.route == "pair":
        if not request.dest_selector:
            return None
        dest_selector = request.dest_selector
        dest_base_dir = base_dir
    elif request.route == "local":
        dest_selector = ".archive"
        dest_base_dir = os.path.dirname(src_path)
    elif request.route == "global":
        dest_selector = str(ctx.config["archive_global_dest_path"]).strip()
        if not dest_selector:
            dest_selector = ".archive"
        dest_base_dir = global_base_dir(src_path)
    else:
        return None

    dest_dir = resolve_safe_selector(
        ctx,
        selector=dest_selector,
        base_dir=dest_base_dir,
        allowed_root=allowed_root,
        role="dest_dir",
        flag=dest_flag_for_request(request),
    )
    if not dest_dir:
        return None
    if rejects_config_path(ctx, dest_dir):
        notify.security_block(
            ctx,
            reason="config_path_rejected",
            role="dest_dir",
            flag=dest_flag_for_request(request),
            target=dest_dir,
        )
        return None
    if os.path.exists(dest_dir):
        if os.path.islink(dest_dir) or not os.path.isdir(dest_dir):
            notify.security_block(
                ctx,
                reason="dest_dir_not_directory",
                role="dest_dir",
                flag=dest_flag_for_request(request),
                target=dest_dir,
            )
            return None
        return canonical_path(dest_dir)

    try:
        os.makedirs(dest_dir, exist_ok=True)
    except OSError:
        notify.operation_failed(
            ctx,
            reason="create_dest_dir_failed",
            target=dest_dir,
        )
        return None

    if os.path.islink(dest_dir) or not os.path.isdir(dest_dir):
        notify.security_block(
            ctx,
            reason="dest_dir_not_directory",
            role="dest_dir",
            flag=dest_flag_for_request(request),
            target=dest_dir,
        )
        return None
    resolved_dir = canonical_path(dest_dir)
    if not path_is_inside(resolved_dir, allowed_root):
        notify.security_block(
            ctx,
            reason="outside_allowed_root",
            role="dest_dir",
            flag=dest_flag_for_request(request),
            target=resolved_dir,
            allowed_root=allowed_root,
        )
        return None
    return resolved_dir
