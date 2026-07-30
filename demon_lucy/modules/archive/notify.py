from __future__ import annotations

import logging

from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import safe_notify
from demon_lucy.lib.path import canonical_path
from demon_lucy.modules.abstract_module import Context

logger = logging.getLogger(__name__)


def _detail_text(**items: object) -> str:
    lines: list[str] = []
    for key, value in items.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple, set)):
            rendered = ", ".join(str(item) for item in value)
        else:
            rendered = str(value)
        if rendered:
            lines.append(f"{key}: {rendered}")
    return "\n".join(lines)


def _config_path(ctx: Context) -> str | None:
    config_path: str = ctx.args.require("sys-config-path").value
    config_path = config_path.strip()
    if not config_path:
        return None
    return canonical_path(config_path)


def archive_issue(
    ctx: Context,
    *,
    action: str,
    key_prefix: str,
    reason: str,
    summary: str,
    scope: str | None = None,
    **details: object,
) -> None:
    path = canonical_path(ctx.path)
    log_details = {"reason": reason, "path": path, **details}
    logger.error(log_record(action, **log_details))

    scope_value = scope or str(details.get("target") or details.get("selector") or path)
    message_details = _detail_text(path=path, reason=reason, **details)
    message = summary
    if message_details:
        message += f"\n\n{message_details}"
    safe_notify(
        f"{key_prefix}:{reason}:{scope_value}",
        message,
        args=ctx.args,
        use_rare_mode=True,
    )


def invalid_rule(
    ctx: Context,
    *,
    flag: str,
    reason: str,
    **details: object,
) -> None:
    archive_issue(
        ctx,
        action="archive.rule_invalid",
        key_prefix=f"archive-rule:{flag}",
        reason=reason,
        summary="Archive rule is invalid.",
        scope=_config_path(ctx) or canonical_path(ctx.path),
        flag=flag,
        **details,
    )


def security_block(
    ctx: Context,
    *,
    reason: str,
    role: str,
    flag: str | None = None,
    selector: str | None = None,
    target: str | None = None,
    allowed_root: str | None = None,
    **details: object,
) -> None:
    archive_issue(
        ctx,
        action="archive.security_blocked",
        key_prefix="archive-security",
        reason=reason,
        summary="Archive blocked an unsafe path.",
        scope=target or selector or canonical_path(ctx.path),
        role=role,
        flag=flag,
        selector=selector,
        target=target,
        allowed_root=allowed_root,
        **details,
    )


def operation_failed(
    ctx: Context,
    *,
    reason: str,
    target: str,
    **details: object,
) -> None:
    archive_issue(
        ctx,
        action="archive.operation_failed",
        key_prefix="archive-operation",
        reason=reason,
        summary="Archive operation failed.",
        scope=target,
        target=target,
        **details,
    )
