from __future__ import annotations

import logging
import os

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.symlink import replace_symlink_atomic
from demon_lucy.modules.linker.root import is_ignored_path

logger = logging.getLogger(__name__)


def update_symlinks_for_moved_paths(
    *,
    moved_from_abs: str,
    moved_to_abs: str,
    repo_root: str,
    args: ParsedArgs,
    event_id: str,
) -> dict[str, int] | None:
    ignore_selectors = args.require("linker-ignore").value
    changed: dict[str, int] = {}
    for root, dirs, files in os.walk(repo_root, followlinks=False):
        dirs[:] = [name for name in dirs if name != ".git"]
        for name in [*dirs, *files]:
            path = os.path.join(root, name)
            if name == ".git" or not os.path.islink(path):
                continue
            if is_ignored_path(
                path_value=path,
                repo_root=repo_root,
                ignore_selectors=ignore_selectors,
            ):
                continue
            try:
                target = os.readlink(path)
                # Match the immediate target, preserving symlink chains.
                target_abs = os.path.abspath(os.path.join(root, target))
                if os.path.normcase(target_abs) != os.path.normcase(moved_from_abs):
                    continue
                new_target = (
                    moved_to_abs
                    if os.path.isabs(target)
                    else os.path.relpath(moved_to_abs, root)
                )
                if not replace_symlink_atomic(path, new_target, expected_target=target):
                    logger.info(
                        log_record(
                            "linker.symlink_skipped",
                            id=event_id,
                            path=path,
                            reason="link_changed",
                        )
                    )
                    continue
            except OSError as exc:
                logger.error(
                    log_record(
                        "linker.symlink_failed",
                        id=event_id,
                        path=path,
                        src=moved_from_abs,
                        dest=moved_to_abs,
                        error=exc,
                    )
                )
                safe_notify(
                    f"linker-symlink:{path}",
                    f"Could not update symlink {path}: {exc}",
                    args=args,
                    use_rare_mode=True,
                )
                continue
            changed[path] = 1
            logger.info(
                log_record(
                    "linker.symlink_updated",
                    id=event_id,
                    path=path,
                    src=moved_from_abs,
                    dest=moved_to_abs,
                )
            )
    return changed or None
