from __future__ import annotations

import os
from dataclasses import replace
from typing import Optional

from demon_lucy.lib.args.parser import Template
from demon_lucy.lib.path import canonical_path
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)

from demon_lucy.modules.archive import clock, notify, paths, requests, storage
from demon_lucy.modules.archive.constants import ARCHIVE_TEMPLATE, FILE_MODE, TEXT_MODE
from demon_lucy.modules.archive.types import ArchiveRequest


class Archive(AbstractModule):
    name: str = "archive"
    priority: int = 25
    template: Template = ARCHIVE_TEMPLATE

    def _read_source_body(
        self,
        ctx: Context,
        request: ArchiveRequest,
        src_path: str,
    ) -> str | None:
        src_text = storage.read_text_no_follow(src_path)
        if src_text is None:
            if os.path.islink(src_path):
                notify.security_block(
                    ctx,
                    reason="source_symlink_rejected",
                    role="src",
                    flag=paths.source_flag_for_request(request),
                    target=src_path,
                )
            elif storage.has_multiple_hard_links(src_path):
                notify.security_block(
                    ctx,
                    reason="source_hardlink_rejected",
                    role="src",
                    flag=paths.source_flag_for_request(request),
                    target=src_path,
                )
            else:
                notify.operation_failed(
                    ctx,
                    reason="read_source_failed",
                    target=src_path,
                )
            return None

        body = storage.normalize_archive_body(src_text, max_blank_lines=3)
        return body or None

    def _archive_text(
        self,
        ctx: Context,
        request: ArchiveRequest,
        *,
        src_path: str,
        body: str,
        timestamp: float | None,
        base_dir: str,
        allowed_root: str,
    ) -> Optional[IgnoreMap]:
        dest_path = paths.resolve_text_dest_path(
            ctx,
            request,
            src_path=src_path,
            base_dir=base_dir,
            allowed_root=allowed_root,
        )
        if not dest_path:
            return None

        date_prefix = str(ctx.config["archive_date_prefix"])
        date_suffix = str(ctx.config["archive_date_suffix"])
        header_line = storage.archive_text_header_line(
            date_label=clock.archive_text_date_label(timestamp),
            prefix=date_prefix,
            suffix=date_suffix,
        )
        append_ok, appended = storage.write_text_archive_entry(
            dest_path=dest_path,
            header_line=header_line,
            body=body,
            prefix=date_prefix,
            suffix=date_suffix,
        )
        if not append_ok:
            notify.operation_failed(
                ctx,
                reason="write_text_archive_failed",
                target=dest_path,
            )
            return None

        if not storage.truncate_source_file(src_path):
            notify.operation_failed(
                ctx,
                reason="truncate_source_failed",
                target=src_path,
            )
            return None

        changed: IgnoreMap = {src_path: 1}
        if appended:
            changed[dest_path] = 1
        return changed

    def _archive_file(
        self,
        ctx: Context,
        request: ArchiveRequest,
        *,
        src_path: str,
        body: str,
        timestamp: float | None,
        base_dir: str,
        allowed_root: str,
    ) -> Optional[IgnoreMap]:
        dest_dir = paths.resolve_dest_dir(
            ctx,
            request,
            src_path=src_path,
            base_dir=base_dir,
            allowed_root=allowed_root,
        )
        if not dest_dir:
            return None

        dest_path = storage.unique_file_archive_path(
            dest_dir=dest_dir,
            src_path=src_path,
            date_label=clock.archive_file_date_label(timestamp),
        )
        if not dest_path:
            notify.operation_failed(
                ctx,
                reason="unique_archive_name_failed",
                target=dest_dir,
            )
            return None
        if paths.rejects_config_path(ctx, dest_path):
            notify.security_block(
                ctx,
                reason="config_path_rejected",
                role="dest",
                flag=paths.dest_flag_for_request(request),
                target=dest_path,
            )
            return None
        if not storage.write_new_archive_file(dest_path, body):
            notify.operation_failed(
                ctx,
                reason="write_file_archive_failed",
                target=dest_path,
            )
            return None
        if not storage.truncate_source_file(src_path):
            notify.operation_failed(
                ctx,
                reason="truncate_source_failed",
                target=src_path,
            )
            return None
        return {src_path: 1, dest_path: 1}

    def _archive_request(
        self,
        ctx: Context,
        request: ArchiveRequest,
    ) -> Optional[IgnoreMap]:
        base_dir = paths.event_base_dir(ctx)
        allowed_root = paths.archive_allowed_root(ctx)
        if allowed_root is None:
            return None

        src_path = paths.resolve_source_path(
            ctx,
            request,
            base_dir=base_dir,
            allowed_root=allowed_root,
        )
        if not src_path:
            return None

        if not request.force and not clock.is_stale(ctx, src_path, request.idle_hours):
            return None

        body = self._read_source_body(ctx, request, src_path)
        if not body:
            return None

        timestamp = clock.archive_entry_timestamp(ctx, src_path)
        if request.output_mode == TEXT_MODE:
            return self._archive_text(
                ctx,
                request,
                src_path=src_path,
                body=body,
                timestamp=timestamp,
                base_dir=base_dir,
                allowed_root=allowed_root,
            )

        if request.output_mode == FILE_MODE:
            return self._archive_file(
                ctx,
                request,
                src_path=src_path,
                body=body,
                timestamp=timestamp,
                base_dir=base_dir,
                allowed_root=allowed_root,
            )

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
        return self._merge_ignore_maps(
            [
                self._archive_request(ctx, request)
                for request in requests.requests_for_context(ctx)
            ]
        )

    def archive_src_to_dest(
        self,
        ctx: Context,
        force: bool = False,
    ) -> Optional[IgnoreMap]:
        pair_request = requests.auto_pair_request(ctx)
        if pair_request is None:
            return None
        allowed_root = paths.archive_allowed_root(ctx)
        if allowed_root is None:
            return None
        src_path = paths.resolve_source_path(
            ctx,
            pair_request,
            base_dir=paths.event_base_dir(ctx),
            allowed_root=allowed_root,
        )
        if not src_path or canonical_path(src_path) != canonical_path(ctx.path):
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
