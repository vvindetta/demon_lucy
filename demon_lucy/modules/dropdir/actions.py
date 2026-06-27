from __future__ import annotations

import logging
import os
import shlex
from dataclasses import dataclass
from typing import Optional

from demon_lucy.lib.args.parser import (
    is_valid_flag_token,
    merge_known_args,
    parse_args,
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
    if not left and not right:
        return None
    merged: IgnoreMap = {}
    for source in (left or {}, right or {}):
        for path_value, times in source.items():
            if not times:
                continue
            merged[path_value] = merged.get(path_value, 0) + int(times)
    return merged or None


def delay_seconds_from_config(ctx: Context) -> float:
    raw_value = ctx.config.get("dropdir_action_delay_milliseconds", 0)
    try:
        delay_ms = int(raw_value)
    except (TypeError, ValueError):
        return 0.0
    if delay_ms <= 0:
        return 0.0
    return delay_ms / 1000.0


def parse_action(raw_action: str) -> DropDirAction | None:
    raw = str(raw_action).strip()
    if not raw or "=" not in raw:
        return None

    selector, action_text = raw.split("=", 1)
    selector = selector.strip()
    action_text = action_text.strip()
    if not selector or not action_text:
        return None

    try:
        tokens = shlex.split(action_text, comments=False, posix=True)
    except ValueError:
        return None

    if not tokens:
        return None

    return DropDirAction(selector=selector, tokens=tokens, raw=raw)


def action_rules(ctx: Context, system: System) -> list[DropDirAction]:
    rules: list[DropDirAction] = []
    for raw_action in ctx.config.get("dropdir_action", []) or []:
        rule = parse_action(str(raw_action))
        if rule is None:
            logger.error(
                log_record(
                    "dropdir.action_invalid",
                    id=system.event_id,
                    path=ctx.path,
                    reason="invalid_rule",
                    rule=raw_action,
                )
            )
            continue
        rules.append(rule)
    return rules


def matches_selector(file_path: str, raw_selector: str) -> bool:
    selector = str(raw_selector).strip()
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
    system: System,
    destination_path: str,
) -> tuple[str, Optional[IgnoreMap]]:
    src_raw = str(getattr(system.event, "src_path", "") or "").strip()
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


def system_flags_in_tokens(tokens: list[str]) -> list[str]:
    flags: list[str] = []
    for token in tokens:
        if not is_valid_flag_token(token):
            continue
        flag = token.split("=", 1)[0]
        if flag.startswith("--sys-"):
            flags.append(flag)
    return flags


def arg_lines_for_action_config(action_config: dict) -> dict[str, list[int]]:
    arg_lines: dict[str, list[int]] = {}
    for key, value in action_config.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        count = len(value) if isinstance(value, list) and value else 1
        arg_lines[key] = [1] * count
    return arg_lines


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
                id=system.event_id,
                path=path,
                reason="system_flags_forbidden",
                flags=system_flags,
                rule=action.raw,
            )
        )
        return None

    action_config, unknown_args = parse_args(
        args=action.tokens,
        template=system.global_template,
        include_defaults=False,
    )
    if unknown_args:
        logger.error(
            log_record(
                "dropdir.action_invalid",
                id=system.event_id,
                path=path,
                reason="unknown_action_args",
                unknown_args=unknown_args,
                rule=action.raw,
            )
        )
        return None

    merged_config = merge_known_args(base_ctx.config, action_config)
    merged_arg_lines = dict(base_ctx.arg_lines)
    for key, lines in arg_lines_for_action_config(action_config).items():
        merged_arg_lines[key] = lines

    return Context(
        path=path,
        config=merged_config,
        arg_lines=merged_arg_lines,
    )


def next_action_path(
    current_path: str,
    event_ignore: Optional[IgnoreMap],
) -> str:
    if not event_ignore or os.path.exists(current_path):
        return current_path

    current_abs = canonical_path(current_path)
    candidates: list[str] = []
    for path_value in event_ignore:
        candidate_path = canonical_path(path_value)
        if candidate_path == current_abs:
            continue
        if not os.path.exists(candidate_path) or os.path.isdir(candidate_path):
            continue
        candidates.append(candidate_path)
    if len(candidates) != 1:
        return current_path
    return candidates[0]


def run_action_modules(
    *,
    source_module: AbstractModule,
    ctx: Context,
    system: System,
) -> Optional[IgnoreMap]:
    event_type = str(system.event.event_type)
    current_path = ctx.path
    action_config = ctx.config
    action_arg_lines = ctx.arg_lines
    changed: Optional[IgnoreMap] = None

    for module in system.modules:
        if module is source_module:
            continue
        if event_type not in module.__class__.__dict__:
            continue

        action = getattr(module, event_type)
        event_ignore = action(
            Context(
                path=current_path,
                config=action_config,
                arg_lines=action_arg_lines,
            ),
            system,
        )
        changed = merge_ignore_maps(changed, event_ignore)
        current_path = next_action_path(current_path, event_ignore)

    return changed
