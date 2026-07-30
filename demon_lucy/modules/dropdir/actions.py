from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from demon_lucy.lib.args.parser import (
    is_valid_flag_token,
    parse_args,
    split_arg_line,
)
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.path import canonical_path, path_is_inside
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DropDirAction:
    selector: str
    tokens: list[str]
    raw: str


def merge_ignore_maps(
    left: Optional[IgnoreMap],
    right: Optional[IgnoreMap],
) -> Optional[IgnoreMap]:
    merged: IgnoreMap = {}
    for source in (left, right):
        if not source:
            continue
        for path_value, times in source.items():
            if not times:
                continue
            merged[path_value] = merged.get(path_value, 0) + int(times)
    return merged or None


def action_delay_seconds(ctx: Context) -> float:
    delay_ms: int = ctx.args.require("dropdir-action-delay-milliseconds").value
    return max(0, delay_ms) / 1000.0


def parse_action(raw_action: str) -> DropDirAction | None:
    raw = raw_action.strip()
    if not raw or "=" not in raw:
        return None

    selector, action_text = raw.split("=", 1)
    selector = selector.strip()
    action_text = action_text.strip()
    if not selector or not action_text:
        return None

    try:
        tokens = split_arg_line(action_text)
    except ValueError:
        return None

    if not tokens:
        return None

    return DropDirAction(selector=selector, tokens=tokens, raw=raw)


def action_rules(ctx: Context) -> list[DropDirAction]:
    rules: list[DropDirAction] = []
    for raw_action in ctx.args.require("dropdir-action").value:
        rule = parse_action(raw_action)
        if rule is None:
            logger.error(
                log_record(
                    "dropdir.action_invalid",
                    id=ctx.event_id,
                    path=ctx.path,
                    reason="invalid_rule",
                    rule=raw_action,
                )
            )
            continue
        rules.append(rule)
    return rules


def matches_selector(file_path: str, raw_selector: str) -> bool:
    selector = raw_selector.strip()
    if not selector:
        return False

    file_dir = canonical_path(os.path.dirname(file_path))
    expanded = os.path.expanduser(selector)

    if os.path.isabs(expanded):
        return path_is_inside(file_dir, expanded)

    if os.sep in selector:
        candidate = os.path.join(os.path.dirname(file_path), expanded)
        return path_is_inside(file_dir, candidate)

    dir_components = [part for part in file_dir.split(os.sep) if part]
    return selector in dir_components


def move_back_to_source(
    ctx: Context,
    destination_path: str,
) -> tuple[str, Optional[IgnoreMap]]:
    src_raw = str(getattr(ctx.event, "src_path", "") or "").strip()
    if not src_raw:
        return destination_path, None

    src_path = canonical_path(src_raw)
    dest_path = canonical_path(destination_path)
    if src_path == dest_path:
        return dest_path, None

    if not os.path.exists(dest_path):
        return dest_path, None

    if os.path.exists(src_path):
        return dest_path, None

    try:
        os.rename(dest_path, src_path)
    except OSError:
        return dest_path, None

    return src_path, {dest_path: 1, src_path: 1}


def next_action_path(
    current_path: str,
    changed: Optional[IgnoreMap],
) -> str:
    if not changed or os.path.exists(current_path):
        return current_path

    current_abs = canonical_path(current_path)
    candidates: list[str] = []
    for path_value in changed:
        candidate_path = canonical_path(path_value)
        if candidate_path == current_abs:
            continue
        if not os.path.exists(candidate_path) or os.path.isdir(candidate_path):
            continue
        candidates.append(candidate_path)
    if len(candidates) != 1:
        return current_path
    return candidates[0]


def system_flags_in_tokens(tokens: list[str]) -> list[str]:
    flags: list[str] = []
    for token in tokens:
        if not is_valid_flag_token(token):
            continue
        flag = token.split("=", 1)[0]
        if flag.startswith("--sys-"):
            flags.append(flag)
    return flags


def action_context(
    *,
    path: str,
    base_ctx: Context,
    action: DropDirAction,
    system: System,
) -> Context | None:
    system_flags = system_flags_in_tokens(action.tokens)
    if system_flags:
        logger.error(
            log_record(
                "dropdir.action_invalid",
                id=base_ctx.event_id,
                path=path,
                reason="system_flags_forbidden",
                flags=system_flags,
                rule=action.raw,
            )
        )
        return None

    action_args = parse_args(
        args=action.tokens,
        template=system.global_template,
        include_defaults=False,
    )
    if action_args.unknown:
        logger.error(
            log_record(
                "dropdir.action_invalid",
                id=base_ctx.event_id,
                path=path,
                reason="unknown_action_args",
                unknown_args=[item.token for item in action_args.unknown],
                rule=action.raw,
            )
        )
        return None

    return Context(
        path=path,
        args=base_ctx.args.merged_with(action_args),
        run_mode=base_ctx.run_mode,
        event_id=base_ctx.event_id,
        event=base_ctx.event,
    )


def run_action_modules(
    *,
    source_module: AbstractModule,
    ctx: Context,
    system: System,
) -> Optional[IgnoreMap]:
    if ctx.event is None:
        return None
    event_type = str(ctx.event.event_type)
    current_path = ctx.path
    changed: Optional[IgnoreMap] = None

    for module in system.modules:
        if module is source_module:
            continue
        handler = getattr(type(module), event_type)
        if handler is getattr(AbstractModule, event_type):
            continue

        module_changed = handler(
            module,
            Context(
                path=current_path,
                args=ctx.args,
                run_mode=ctx.run_mode,
                event_id=ctx.event_id,
                event=ctx.event,
            ),
            system,
        )
        changed = merge_ignore_maps(changed, module_changed)
        current_path = next_action_path(current_path, module_changed)

    return changed
