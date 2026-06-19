import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional

from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.path import canonical_path
from demon_lucy.modules.abstract_module import AbstractModule, Context, System
from demon_lucy.modules.plasma_widget.config import PLASMA_WIDGET_TEMPLATE
from demon_lucy.modules.plasma_widget.engine import (
    SyncPlan,
    SyncState,
    bootstrap_state,
    plan_from_bold_mirror,
    plan_from_main_plasma,
    plan_from_markdown,
)
from demon_lucy.modules.plasma_widget.markdown_codec import (
    _doc_hash,
    _doc_to_md,
    _md_to_doc,
)
from demon_lucy.modules.plasma_widget.mirror_mapper import (
    _apply_mirror_items_to_doc,
    _apply_mirror_lines_to_doc,
    _bold_lines_to_plasma_html,
    _extract_bold_items_from_doc,
    _items_hash,
    _mirror_html_to_lines,
    _mirror_html_to_items,
)
from demon_lucy.modules.plasma_widget.model import (
    DocLine,
    _hash_text,
    _normalize_md,
)
from demon_lucy.modules.plasma_widget.plasma_html_codec import (
    _doc_to_plasma_html,
    _html_to_doc,
)

__all__ = [
    "DocLine",
    "PlasmaWidget",
    "SyncPlan",
    "SyncState",
    "bootstrap_state",
    "plan_from_bold_mirror",
    "plan_from_main_plasma",
    "plan_from_markdown",
    "_apply_mirror_items_to_doc",
    "_apply_mirror_lines_to_doc",
    "_bold_lines_to_plasma_html",
    "_doc_hash",
    "_doc_to_md",
    "_doc_to_plasma_html",
    "_extract_bold_items_from_doc",
    "_hash_text",
    "_html_to_doc",
    "_items_hash",
    "_md_to_doc",
    "_mirror_html_to_lines",
    "_mirror_html_to_items",
    "_normalize_md",
]

logger = logging.getLogger(__name__)

IgnoreMap = Dict[str, int]
SyncKey = tuple[str, str, Optional[str]]

_IGNORE_BURST = 1


# ---------------- State ---------------- #

_INIT_DONE: bool = False

_STATE: SyncState = SyncState(
    doc_hash=None,
    bold_items_hash=None,
    css_style=None,
)

_STATE_GUARD = threading.Lock()
_STATE_BY_KEY: dict[SyncKey, SyncState] = {}
_INIT_DONE_BY_KEY: dict[SyncKey, bool] = {}


def _sync_key(
    widget_path: str,
    markdown_path: str,
    bold_widget_path: Optional[str],
) -> SyncKey:
    return (canonical_path(widget_path), canonical_path(markdown_path), bold_widget_path)


def _state_for_key(sync_key: SyncKey) -> SyncState:
    with _STATE_GUARD:
        state = _STATE_BY_KEY.get(sync_key)
        if state is not None:
            return state
    return SyncState(
        doc_hash=None,
        bold_items_hash=None,
        css_style=None,
    )


def _set_state_for_key(sync_key: SyncKey, state: SyncState) -> None:
    global _STATE, _INIT_DONE
    with _STATE_GUARD:
        _STATE_BY_KEY[sync_key] = state
        _INIT_DONE_BY_KEY[sync_key] = True
    # Keep legacy globals for compatibility/introspection.
    _STATE = state
    _INIT_DONE = True


# ---------------- IO ---------------- #


@dataclass(frozen=True)
class ReadResult:
    path: str
    content: str
    exists: bool
    ok: bool


@dataclass(frozen=True)
class PendingWrite:
    path: str
    previous_content: str
    next_content: str


def _read_file_checked(
    path: str,
) -> ReadResult:
    absolute_path = canonical_path(path)
    try:
        with open(absolute_path, "r", encoding="utf-8") as file_handle:
            return ReadResult(
                path=absolute_path,
                content=file_handle.read(),
                exists=True,
                ok=True,
            )
    except FileNotFoundError:
        return ReadResult(
            path=absolute_path,
            content="",
            exists=False,
            ok=True,
        )
    except (PermissionError, OSError):
        return ReadResult(
            path=absolute_path,
            content="",
            exists=False,
            ok=False,
        )


def _write_text_atomic(
    path: str,
    content: str,
    *,
    notify_errors: bool = True,
) -> bool:
    absolute_path = canonical_path(path)
    directory = os.path.dirname(absolute_path)
    temp_path: Optional[str] = None
    try:
        os.makedirs(directory, exist_ok=True)
        file_descriptor, temp_path = tempfile.mkstemp(
            prefix=".plasma_widget.",
            suffix=".tmp",
            dir=directory,
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as file_handle:
            file_handle.write(content)
        os.replace(temp_path, absolute_path)
        temp_path = None
        return True
    except (PermissionError, OSError):
        return False
    finally:
        if temp_path:
            try:
                os.remove(temp_path)
            except OSError:
                logger.info(
                    log_record("plasma.temp_cleanup_failed", path=temp_path)
                )


def _collect_pending_writes(
    *,
    plan: SyncPlan,
    widget_path: str,
    markdown_path: str,
    bold_widget_path: Optional[str],
) -> Optional[list[PendingWrite]]:
    pending: list[PendingWrite] = []
    targets: list[tuple[Optional[str], Optional[str]]] = [
        (widget_path, plan.widget_html),
        (markdown_path, plan.markdown_text),
        (bold_widget_path, plan.mirror_html),
    ]
    for path, next_content in targets:
        if path is None or next_content is None:
            continue
        read_result = _read_file_checked(path)
        if not read_result.ok:
            return None
        if read_result.content == next_content:
            continue
        pending.append(
            PendingWrite(
                path=read_result.path,
                previous_content=read_result.content,
                next_content=next_content,
            )
        )
    return pending


def _restore_previous_writes(
    applied: list[PendingWrite],
    *,
    config: Mapping[str, Any],
) -> list[str]:
    rollback_failed_paths: list[str] = []
    for write in reversed(applied):
        restored = _write_text_atomic(
            write.path,
            write.previous_content,
            notify_errors=False,
        )
        if not restored:
            logger.error(log_record("plasma.rollback_failed", path=write.path))
            rollback_failed_paths.append(write.path)
    return rollback_failed_paths


def _notify_write_failure(
    *,
    failed_path: str,
    rollback_failed_paths: list[str],
    config: Mapping[str, Any],
) -> None:
    logger.error(
        log_record(
            "plasma.write_failed",
            path=failed_path,
            rollback_failed_paths=len(rollback_failed_paths),
        )
    )
    details = f"Failed to write Plasma sync file:\n{failed_path}"
    if rollback_failed_paths:
        details += "\n\nRollback also failed for:\n" + "\n".join(rollback_failed_paths)
    safe_notify(
        "plasma-write:" + canonical_path(failed_path),
        details,
        config=config,
        use_rare_mode=True,
    )


def _apply_pending_writes(
    *,
    pending: list[PendingWrite],
    ignore: IgnoreMap,
    config: Mapping[str, Any],
) -> bool:
    applied: list[PendingWrite] = []
    for write in pending:
        if not _write_text_atomic(
            write.path,
            write.next_content,
            notify_errors=True,
        ):
            rollback_failed_paths = _restore_previous_writes(applied, config=config)
            _notify_write_failure(
                failed_path=write.path,
                rollback_failed_paths=rollback_failed_paths,
                config=config,
            )
            return False
        _inc_ignore(ignore, write.path, _IGNORE_BURST)
        applied.append(write)
    return True


def _inc_ignore(ignore: IgnoreMap, path: str, times: int = 1) -> None:
    absolute_path = canonical_path(path)
    ignore[absolute_path] = ignore.get(absolute_path, 0) + int(times)


def _notify_empty_source_guard(
    *,
    source_label: str,
    source_path: str,
    markdown_path: str,
    config: Mapping[str, Any],
) -> None:
    source_abs = canonical_path(source_path)
    markdown_abs = canonical_path(markdown_path)
    logger.error(
        log_record(
            "plasma.guard_blocked",
            reason="empty_source",
            source=source_abs,
            markdown=markdown_abs,
        )
    )
    safe_notify(
        "plasma-empty-source:" + markdown_abs,
        (
            f"Blocked empty {source_label} from clearing markdown:\n"
            f"{markdown_abs}\n\nSource:\n{source_abs}"
        ),
        config=config,
        use_rare_mode=True,
    )


def _notify_shrinking_source_guard(
    *,
    source_label: str,
    source_path: str,
    markdown_path: str,
    config: Mapping[str, Any],
) -> None:
    source_abs = canonical_path(source_path)
    markdown_abs = canonical_path(markdown_path)
    logger.error(
        log_record(
            "plasma.guard_blocked",
            reason="shrinking_source",
            source=source_abs,
            markdown=markdown_abs,
        )
    )
    safe_notify(
        "plasma-shrinking-source:" + markdown_abs,
        (
            f"Blocked shrinking {source_label} from truncating markdown:\n"
            f"{markdown_abs}\n\nSource:\n{source_abs}"
        ),
        config=config,
        use_rare_mode=True,
    )


# ---------------- Startup init ---------------- #


def _init_from_disk_once(
    sync_key: SyncKey,
    widget_path: str,
    markdown_path: str,
    bold_widget_path: Optional[str],
) -> None:
    global _INIT_DONE, _STATE
    with _STATE_GUARD:
        if _INIT_DONE_BY_KEY.get(sync_key, False):
            return

    _ = canonical_path(bold_widget_path) if bold_widget_path else None
    markdown_read = _read_file_checked(markdown_path)
    widget_read = _read_file_checked(widget_path)
    if not markdown_read.ok or not widget_read.ok:
        return

    state = bootstrap_state(markdown_read.content, widget_read.content)
    with _STATE_GUARD:
        if _INIT_DONE_BY_KEY.get(sync_key, False):
            return
        _STATE_BY_KEY[sync_key] = state
        _INIT_DONE_BY_KEY[sync_key] = True
    # Keep legacy globals for compatibility/introspection.
    _STATE = state
    _INIT_DONE = True


def _apply_sync_plan(
    *,
    sync_key: SyncKey,
    plan: SyncPlan,
    widget_path: str,
    markdown_path: str,
    bold_widget_path: Optional[str],
    config: Mapping[str, Any],
) -> Optional[IgnoreMap]:
    ignore: IgnoreMap = {}
    pending = _collect_pending_writes(
        plan=plan,
        widget_path=widget_path,
        markdown_path=markdown_path,
        bold_widget_path=bold_widget_path,
    )
    if pending is None:
        return None
    if not _apply_pending_writes(
        pending=pending,
        ignore=ignore,
        config=config,
    ):
        return None

    _set_state_for_key(sync_key, plan.next_state)

    return ignore or None


# ---------------- Module ---------------- #


class PlasmaWidget(AbstractModule):
    name: str = "plasma_widget"
    priority: int = 30

    template = PLASMA_WIDGET_TEMPLATE

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._handle(ctx)

    def deleted(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return None

    def _cfg(self, ctx: Context) -> tuple[str, str, Optional[str], bool]:
        return (
            canonical_path(ctx.config["plasma_widget_path"]),
            canonical_path(ctx.config["plasma_markdown_note_path"]),
            (
                canonical_path(ctx.config["plasma_bold_widget_path"])
                if ctx.config["plasma_bold_widget_path"]
                else None
            ),
            ctx.config["plasma_css_style"],
        )

    def _handle(self, ctx: Context) -> Optional[IgnoreMap]:
        widget_path, markdown_path, bold_widget_path, css_style = self._cfg(ctx)
        sync_key = _sync_key(widget_path, markdown_path, bold_widget_path)

        _init_from_disk_once(
            sync_key,
            widget_path,
            markdown_path,
            bold_widget_path,
        )

        path = canonical_path(ctx.path)
        widget_abs = canonical_path(widget_path)
        md_abs = canonical_path(markdown_path)
        bold_abs = canonical_path(bold_widget_path) if bold_widget_path else None

        if path == md_abs:
            return self._from_markdown(
                sync_key=sync_key,
                markdown_path=markdown_path,
                widget_path=widget_path,
                bold_widget_path=bold_widget_path,
                css_style=css_style,
                config=ctx.config,
            )

        if bold_abs and path == bold_abs:
            return self._from_bold_mirror(
                sync_key=sync_key,
                widget_path=widget_path,
                markdown_path=markdown_path,
                bold_widget_path=bold_widget_path,
                css_style=css_style,
                config=ctx.config,
            )

        if path == widget_abs:
            return self._from_main_plasma(
                sync_key=sync_key,
                widget_path=widget_path,
                markdown_path=markdown_path,
                bold_widget_path=bold_widget_path,
                css_style=css_style,
                html_path=path,
                config=ctx.config,
            )

        return None

    def _from_markdown(
        self,
        markdown_path: str,
        widget_path: str,
        bold_widget_path: Optional[str],
        css_style: bool,
        config: Mapping[str, Any],
        sync_key: Optional[SyncKey] = None,
    ) -> Optional[IgnoreMap]:
        if sync_key is None:
            sync_key = _sync_key(widget_path, markdown_path, bold_widget_path)
        markdown_read = _read_file_checked(markdown_path)
        widget_read = _read_file_checked(widget_path)
        mirror_read = _read_file_checked(bold_widget_path) if bold_widget_path else None

        if not markdown_read.ok or not widget_read.ok:
            return None
        if mirror_read is not None and not mirror_read.ok:
            return None

        plan = plan_from_markdown(
            state=_state_for_key(sync_key),
            markdown_text=markdown_read.content,
            markdown_exists=markdown_read.exists,
            widget_html_current=widget_read.content,
            mirror_html_current=(
                mirror_read.content if mirror_read is not None else None
            ),
            css_style=css_style,
        )

        if plan.missing_markdown:
            safe_notify(
                "md_missing:" + markdown_path,
                f"Markdown note file not found:\n{markdown_path}",
                config=config,
                use_rare_mode=True,
            )
            return None

        ignore = _apply_sync_plan(
            sync_key=sync_key,
            plan=plan,
            widget_path=widget_path,
            markdown_path=markdown_path,
            bold_widget_path=bold_widget_path,
            config=config,
        )

        if ignore:
            logger.info(
                log_record(
                    "plasma.sync_applied",
                    direction="markdown_to_widget",
                    source=canonical_path(markdown_path),
                    widget=canonical_path(widget_path),
                    mirror=canonical_path(bold_widget_path) if bold_widget_path else None,
                )
            )
        return ignore

    def _from_main_plasma(
        self,
        widget_path: str,
        markdown_path: str,
        bold_widget_path: Optional[str],
        css_style: bool,
        html_path: str,
        config: Mapping[str, Any],
        sync_key: Optional[SyncKey] = None,
    ) -> Optional[IgnoreMap]:
        if sync_key is None:
            sync_key = _sync_key(widget_path, markdown_path, bold_widget_path)
        if not os.path.exists(html_path):
            return None

        widget_read = _read_file_checked(html_path)
        markdown_read = _read_file_checked(markdown_path)
        mirror_read = _read_file_checked(bold_widget_path) if bold_widget_path else None

        if not widget_read.ok or not markdown_read.ok:
            return None
        if mirror_read is not None and not mirror_read.ok:
            return None

        plan = plan_from_main_plasma(
            state=_state_for_key(sync_key),
            widget_html_current=widget_read.content,
            widget_exists=True,
            markdown_text_current=markdown_read.content,
            mirror_html_current=(
                mirror_read.content if mirror_read is not None else None
            ),
            css_style=css_style,
        )

        if plan.blocked_empty_source:
            _notify_empty_source_guard(
                source_label="MAIN Plasma widget",
                source_path=html_path,
                markdown_path=markdown_path,
                config=config,
            )
        if plan.blocked_shrinking_source:
            _notify_shrinking_source_guard(
                source_label="MAIN Plasma widget",
                source_path=html_path,
                markdown_path=markdown_path,
                config=config,
            )

        ignore = _apply_sync_plan(
            sync_key=sync_key,
            plan=plan,
            widget_path=widget_path,
            markdown_path=markdown_path,
            bold_widget_path=bold_widget_path,
            config=config,
        )

        if ignore:
            logger.info(
                log_record(
                    "plasma.sync_applied",
                    direction="widget_to_markdown",
                    source=canonical_path(html_path),
                    markdown=canonical_path(markdown_path),
                    mirror=canonical_path(bold_widget_path) if bold_widget_path else None,
                )
            )
        return ignore

    def _from_bold_mirror(
        self,
        widget_path: str,
        markdown_path: str,
        bold_widget_path: Optional[str],
        css_style: bool,
        config: Mapping[str, Any],
        sync_key: Optional[SyncKey] = None,
    ) -> Optional[IgnoreMap]:
        """
        Optional: editing mirror updates MAIN bold lines.
        Mirror contains one line per bold-line in MAIN (line-safe mapping).
        """
        if sync_key is None:
            sync_key = _sync_key(widget_path, markdown_path, bold_widget_path)
        if not bold_widget_path or not os.path.exists(bold_widget_path):
            return None

        mirror_read = _read_file_checked(bold_widget_path)
        widget_read = _read_file_checked(widget_path)
        markdown_read = _read_file_checked(markdown_path)
        if not mirror_read.ok or not widget_read.ok or not markdown_read.ok:
            return None

        plan = plan_from_bold_mirror(
            state=_state_for_key(sync_key),
            mirror_html_current=mirror_read.content,
            mirror_exists=True,
            widget_html_current=widget_read.content,
            markdown_text_current=markdown_read.content,
            css_style=css_style,
        )

        if plan.blocked_empty_source:
            _notify_empty_source_guard(
                source_label="BOLD Plasma mirror",
                source_path=bold_widget_path,
                markdown_path=markdown_path,
                config=config,
            )
        if plan.blocked_shrinking_source:
            _notify_shrinking_source_guard(
                source_label="BOLD Plasma mirror",
                source_path=bold_widget_path,
                markdown_path=markdown_path,
                config=config,
            )

        ignore = _apply_sync_plan(
            sync_key=sync_key,
            plan=plan,
            widget_path=widget_path,
            markdown_path=markdown_path,
            bold_widget_path=bold_widget_path,
            config=config,
        )

        if ignore:
            logger.info(
                log_record(
                    "plasma.sync_applied",
                    direction="mirror_to_widget_to_markdown",
                    source=canonical_path(bold_widget_path),
                    widget=canonical_path(widget_path),
                    markdown=canonical_path(markdown_path),
                )
            )
        return ignore
