from __future__ import annotations

import logging
import os
import platform
import threading
import time
from typing import Optional

from demon_lucy.lib import safe_notify
from demon_lucy.lib.args import Template
from demon_lucy.lib.path import (
    abs_expand_path,
    find_parent_with,
    path_has_component,
)
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from demon_lucy.modules.git.worker import build_patch_packet, commit_dirty_tree
from demon_lucy.modules.kdeconnect_sync.config import KDECONNECT_SYNC_TEMPLATE
from demon_lucy.modules.kdeconnect_sync.queue import (
    ensure_queue_excluded_in_repo,
    is_queue_internal_path,
    outgoing_pc_to_phone_dir,
)
from demon_lucy.modules.kdeconnect_sync.transport import (
    transfer_packet_to_phone,
)

logger = logging.getLogger(__name__)

_COALESCE_GUARD = threading.Lock()
_COALESCE_TOKENS: dict[str, int] = {}


def _to_str(value: object) -> str:
    return str(value or "")


def _notify_config(config: dict) -> dict:
    return {
        "sys_notification_provider": config["sys_notification_provider"],
        "sys_notification_min_interval_seconds": config[
            "sys_notification_min_interval_seconds"
        ],
        "sys_notification_error_backoff_base_seconds": config[
            "sys_notification_error_backoff_base_seconds"
        ],
        "sys_notification_error_backoff_max_seconds": config[
            "sys_notification_error_backoff_max_seconds"
        ],
        "sys_notification_error_burst_limit": config[
            "sys_notification_error_burst_limit"
        ],
        "sys_notification_error_burst_window_seconds": config[
            "sys_notification_error_burst_window_seconds"
        ],
    }


def _repo_sync_notify(
    *,
    repo_root: str,
    config_snapshot: dict,
    summary_text: str,
    details_text: str = "",
) -> None:
    message_text = f"Repository:\n{repo_root}\n\n{summary_text}"
    if details_text:
        message_text += f"\n\nDetails:\n{details_text[:1200]}"
    safe_notify(
        name=f"kdeconnect-sync:{repo_root}",
        message=message_text,
        config=_notify_config(config_snapshot),
        use_rare_mode=True,
    )


def _author_device_name() -> str:
    host_name = platform.node().strip()
    if host_name:
        return host_name
    return "unknown-device"


class KdeconnectSync(AbstractModule):
    name: str = "kdeconnect_sync"
    priority: int = 49
    template: Template = KDECONNECT_SYNC_TEMPLATE

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "created")

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "modified")

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "moved")

    def deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx, system, "deleted")

    def _handle(
        self, ctx: Context, system: System, event_type: str
    ) -> Optional[IgnoreMap]:
        if not ctx.config["kdeconnect_sync"]:
            return None

        event = system.event
        src_path = abs_expand_path(_to_str(getattr(event, "src_path", "") or ""))
        dest_path = abs_expand_path(_to_str(getattr(event, "dest_path", "") or ""))

        if (src_path and path_has_component(src_path, ".git")) or (
            dest_path and path_has_component(dest_path, ".git")
        ):
            return None

        repo_root = find_parent_with(_to_str(ctx.path), ".git") or find_parent_with(
            dest_path or src_path, ".git"
        )
        if not repo_root:
            return None

        queue_dir_name = _to_str(ctx.config["kdeconnect_patch_queue_dir"]).strip()
        if not queue_dir_name:
            _repo_sync_notify(
                repo_root=repo_root,
                config_snapshot=ctx.config,
                summary_text="kdeconnect patch queue path is empty.",
            )
            return None

        for path_text in [src_path, dest_path]:
            if path_text and is_queue_internal_path(
                path_text, repo_root, queue_dir_name
            ):
                return None

        if system.run_mode == "oneshot":
            self._run_repo_sync(
                repo_root=repo_root,
                event_type=event_type,
                trigger_paths=[path for path in [src_path, dest_path] if path],
                config_snapshot=dict(ctx.config),
            )
            return None

        self._schedule_repo_sync(
            repo_root=repo_root,
            event_type=event_type,
            trigger_paths=[path for path in [src_path, dest_path] if path],
            config_snapshot=dict(ctx.config),
        )
        return None

    def _schedule_repo_sync(
        self,
        *,
        repo_root: str,
        event_type: str,
        trigger_paths: list[str],
        config_snapshot: dict,
    ) -> None:
        token = time.monotonic_ns()
        with _COALESCE_GUARD:
            _COALESCE_TOKENS[repo_root] = token

        def _run_after_delay() -> None:
            delay_seconds = max(
                0.0,
                float(config_snapshot["kdeconnect_patch_coalesce_milliseconds"])
                / 1000.0,
            )
            if delay_seconds > 0.0:
                time.sleep(delay_seconds)
            with _COALESCE_GUARD:
                if _COALESCE_TOKENS.get(repo_root) != token:
                    return
            self._run_repo_sync(
                repo_root=repo_root,
                event_type=event_type,
                trigger_paths=trigger_paths,
                config_snapshot=config_snapshot,
            )

        threading.Thread(target=_run_after_delay, daemon=True).start()

    def _run_repo_sync(
        self,
        *,
        repo_root: str,
        event_type: str,
        trigger_paths: list[str],
        config_snapshot: dict,
    ) -> None:
        try:
            ensure_queue_excluded_in_repo(
                repo_root=repo_root,
                queue_dir_name=_to_str(config_snapshot["kdeconnect_patch_queue_dir"]),
            )
        except OSError as exc:
            _repo_sync_notify(
                repo_root=repo_root,
                config_snapshot=config_snapshot,
                summary_text="failed to configure local git exclude for patch queue.",
                details_text=str(exc),
            )
            return

        commit_result = commit_dirty_tree(
            self,
            repo_root=repo_root,
            event_type=event_type,
            paths=trigger_paths,
            config_snapshot=config_snapshot,
        )
        if commit_result.status == "noop":
            return
        if commit_result.status == "busy":
            logger.info(
                "kdeconnect sync skipped because git repo is busy | repo=%s",
                repo_root,
            )
            return
        if commit_result.status != "committed":
            _repo_sync_notify(
                repo_root=repo_root,
                config_snapshot=config_snapshot,
                summary_text="failed to create commit for patch sync.",
                details_text=commit_result.error_text,
            )
            return

        queue_dir_name = _to_str(config_snapshot["kdeconnect_patch_queue_dir"])
        outgoing_dir = outgoing_pc_to_phone_dir(
            repo_root=repo_root, queue_dir_name=queue_dir_name
        )
        packet_result = build_patch_packet(
            self,
            repo_root=repo_root,
            commit_sha=commit_result.commit_sha,
            changed_paths=list(commit_result.changed_paths),
            queue_dir=outgoing_dir,
            author_device=_author_device_name(),
            config_snapshot=config_snapshot,
        )
        if packet_result.status == "busy":
            logger.info(
                "kdeconnect patch build skipped because git repo is busy | repo=%s",
                repo_root,
            )
            return
        if packet_result.status != "built":
            _repo_sync_notify(
                repo_root=repo_root,
                config_snapshot=config_snapshot,
                summary_text="failed to build patch packet.",
                details_text=packet_result.error_text,
            )
            return

        if config_snapshot["kdeconnect_dry_run"]:
            return

        transfer_result = transfer_packet_to_phone(
            device_id=_to_str(config_snapshot["kdeconnect_device_id"]).strip(),
            remote_root=_to_str(config_snapshot["kdeconnect_remote_root"]).strip(),
            queue_dir_name=queue_dir_name,
            packet_paths=[packet_result.patch_path, packet_result.metadata_path],
            timeout_seconds=float(
                config_snapshot["kdeconnect_command_timeout_seconds"]
            ),
            mount_retry_seconds=float(
                config_snapshot["kdeconnect_mount_retry_seconds"]
            ),
            max_retries=int(config_snapshot["kdeconnect_patch_max_retries"]),
        )
        if transfer_result.status == "sent":
            logger.info(
                "kdeconnect patch packet sent | repo=%s | patch_id=%s | remote_dir=%s",
                repo_root,
                packet_result.patch_id,
                transfer_result.remote_incoming_dir,
            )
            return

        _repo_sync_notify(
            repo_root=repo_root,
            config_snapshot=config_snapshot,
            summary_text="failed to transfer patch packet to phone.",
            details_text=transfer_result.error_text,
        )
