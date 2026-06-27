from __future__ import annotations

import time
from typing import Optional

from demon_lucy.lib.args.parser import Template
from demon_lucy.lib.path import canonical_path
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)
from demon_lucy.modules.dropdir.actions import (
    action_context,
    action_rules,
    delay_seconds_from_config,
    matches_selector,
    merge_ignore_maps,
    move_back_to_source,
    run_action_modules,
)

DROPDIR_TEMPLATE: Template = [
    (
        "--dropdir-action",
        str,
        [],
        "Run temporary Lucy flags when a file is moved into a matching drop directory. "
        "Format: selector=flags. Example: --dropdir-action 'cleanup=--archive-pair'",
        False,
    ),
    (
        "--dropdir-action-delay-milliseconds",
        int,
        0,
        "Delay before running dropdir action after instant move-back (milliseconds). "
        "Example: --dropdir-action-delay-milliseconds 1200",
        False,
    ),
]


class DropDir(AbstractModule):
    name: str = "dropdir"
    priority: int = 24
    template = DROPDIR_TEMPLATE

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        actions = action_rules(ctx, system)
        if not actions:
            return None

        file_path = canonical_path(ctx.path)
        matched_actions = [
            action
            for action in actions
            if matches_selector(file_path=file_path, raw_selector=action.selector)
        ]
        if not matched_actions:
            return None

        action_path, move_back_changed = move_back_to_source(
            system=system,
            destination_path=file_path,
        )

        delay_seconds = delay_seconds_from_config(ctx)
        if delay_seconds > 0:
            time.sleep(delay_seconds)

        action_changed: Optional[IgnoreMap] = None
        for action in matched_actions:
            next_ctx = action_context(
                path=action_path,
                base_ctx=ctx,
                action=action,
                system=system,
            )
            if next_ctx is None:
                continue
            event_ignore = run_action_modules(
                source_module=self,
                ctx=next_ctx,
                system=system,
            )
            action_changed = merge_ignore_maps(action_changed, event_ignore)

        return merge_ignore_maps(move_back_changed, action_changed)
