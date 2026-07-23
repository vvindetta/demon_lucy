from __future__ import annotations

import os
from dataclasses import replace
from typing import Optional

from demon_lucy.lib.args.parser import Template
from demon_lucy.lib.date_sections import format_date_section_header
from demon_lucy.lib.dynamic_blocks.parser import partition_dynamic_blocks
from demon_lucy.lib.path import canonical_path
from demon_lucy.lib.runtime_system import RuntimeSystem
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)

from demon_lucy.modules.archive import clock, notify, paths, requests, storage
from demon_lucy.modules.archive.constants import ARCHIVE_TEMPLATE
from demon_lucy.modules.archive.types import ArchiveRequest
from demon_lucy.modules.archive.types import ArchiveOutputMode


class Archive(AbstractModule):
    name: str = "archive"
    priority: int = 25
    template: Template = ARCHIVE_TEMPLATE

    def _read_source_content(
        self,
        ctx: Context,
        request: ArchiveRequest,
        src_path: str,
        runtime_system: RuntimeSystem,
    ) -> tuple[str, str] | None:
        src_text = storage.read_text_no_follow(
            src_path,
            runtime_system=runtime_system,
        )
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

        try:
            archive_text, retained_source = partition_dynamic_blocks(src_text)
        except ValueError as exc:
            notify.operation_failed(
                ctx,
                reason="invalid_dynamic_blocks",
                target=src_path,
                error=exc,
            )
            return None

        body = storage.normalize_archive_body(archive_text, max_blank_lines=3)
        if not body:
            return None
        return body, retained_source

    def _archive_text(
        self,
        ctx: Context,
        request: ArchiveRequest,
        *,
        src_path: str,
        body: str,
        retained_source: str,
        timestamp: float | None,
        base_dir: str,
        allowed_root: str,
        runtime_system: RuntimeSystem,
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
        entry_date = clock.archive_entry_date(timestamp)
        header_line = format_date_section_header(
            entry_date,
            prefix=date_prefix,
            suffix=date_suffix,
        )
        append_ok, appended = storage.write_text_archive_entry(
            dest_path=dest_path,
            header_line=header_line,
            body=body,
            prefix=date_prefix,
            suffix=date_suffix,
            runtime_system=runtime_system,
        )
        if not append_ok:
            notify.operation_failed(
                ctx,
                reason="write_text_archive_failed",
                target=dest_path,
            )
            return None

        if not storage.write_text_no_follow(
            src_path,
            retained_source,
            runtime_system=runtime_system,
        ):
            notify.operation_failed(
                ctx,
                reason="rewrite_source_failed",
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
        retained_source: str,
        timestamp: float | None,
        base_dir: str,
        allowed_root: str,
        runtime_system: RuntimeSystem,
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
            date_label=clock.archive_entry_date(timestamp).isoformat(),
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
        if not storage.write_new_archive_file(
            dest_path,
            body,
            runtime_system=runtime_system,
        ):
            notify.operation_failed(
                ctx,
                reason="write_file_archive_failed",
                target=dest_path,
            )
            return None
        if not storage.write_text_no_follow(
            src_path,
            retained_source,
            runtime_system=runtime_system,
        ):
            notify.operation_failed(
                ctx,
                reason="rewrite_source_failed",
                target=src_path,
            )
            return None
        return {src_path: 1, dest_path: 1}

    def _archive_request(
        self,
        ctx: Context,
        request: ArchiveRequest,
        runtime_system: RuntimeSystem,
    ) -> Optional[IgnoreMap]:
        base_dir = paths.event_base_dir(ctx)
        allowed_root = paths.archive_allowed_root(ctx)
        if allowed_root is None:
            return None
        allowed_root = paths.source_allowed_root(
            ctx,
            selector=request.src_selector,
            current_allowed_root=allowed_root,
        )

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

        source_content = self._read_source_content(
            ctx,
            request,
            src_path,
            runtime_system,
        )
        if source_content is None:
            return None
        body, retained_source = source_content

        timestamp = clock.archive_entry_timestamp(ctx, src_path)
        if request.output_mode is ArchiveOutputMode.TEXT:
            return self._archive_text(
                ctx,
                request,
                src_path=src_path,
                body=body,
                retained_source=retained_source,
                timestamp=timestamp,
                base_dir=base_dir,
                allowed_root=allowed_root,
                runtime_system=runtime_system,
            )

        if request.output_mode is ArchiveOutputMode.FILE:
            return self._archive_file(
                ctx,
                request,
                src_path=src_path,
                body=body,
                retained_source=retained_source,
                timestamp=timestamp,
                base_dir=base_dir,
                allowed_root=allowed_root,
                runtime_system=runtime_system,
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

    def _archive_requests_if_needed(
        self,
        ctx: Context,
        runtime_system: RuntimeSystem,
    ) -> Optional[IgnoreMap]:
        return self._merge_ignore_maps(
            [
                self._archive_request(ctx, request, runtime_system)
                for request in requests.requests_for_context(ctx)
            ]
        )

    def archive_src_to_dest(
        self,
        ctx: Context,
        system: System,
        force: bool = False,
    ) -> Optional[IgnoreMap]:
        pair_request = requests.auto_pair_request(ctx)
        if pair_request is None:
            return None
        allowed_root = paths.archive_allowed_root(ctx)
        if allowed_root is None:
            return None
        allowed_root = paths.source_allowed_root(
            ctx,
            selector=pair_request.src_selector,
            current_allowed_root=allowed_root,
        )
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
        return self._archive_request(ctx, pair_request, system.runtime_system)

    def opened(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_requests_if_needed(ctx, system.runtime_system)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_requests_if_needed(ctx, system.runtime_system)

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_requests_if_needed(ctx, system.runtime_system)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._archive_requests_if_needed(ctx, system.runtime_system)
