from __future__ import annotations

import time
from dataclasses import replace

from demon_lucy.lib.args.models import KnownArg, Template
from demon_lucy.lib.path import canonical_path
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)
from demon_lucy.modules.dropdir.actions import (
    action_context,
    action_delay_seconds,
    action_rules,
    matches_selector,
    merge_changes,
    move_back_to_source,
    run_action_modules,
)

DROPDIR_TEMPLATE: Template = [
    KnownArg(
        name="dropdir-action",
        value_type=str,
        default=[],
        description="Run temporary Lucy flags when a file is moved into a matching drop directory. "
        "Format: selector=flags. Example: --dropdir-action 'cleanup=--archive-pair'",
    ),
    KnownArg(
        name="dropdir-action-delay-milliseconds",
        value_type=int,
        default=0,
        description="Delay before running dropdir action after instant move-back (milliseconds). "
        "Example: --dropdir-action-delay-milliseconds 1200",
    ),
]


class DropDir(AbstractModule):
    name: str = "dropdir"
    priority: int = 24
    template = DROPDIR_TEMPLATE

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        actions = action_rules(ctx)
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
            ctx=ctx,
            destination_path=file_path,
        )

        delay_seconds = action_delay_seconds(ctx)
        if delay_seconds > 0:
            time.sleep(delay_seconds)

        current_path = action_path
        changed = move_back_changed
        for action in matched_actions:
            next_ctx = action_context(
                path=current_path,
                base_ctx=ctx,
                action=action,
                system=system,
            )
            if next_ctx is None:
                continue
            action_result = run_action_modules(
                source_module=self,
                ctx=next_ctx,
                system=system,
            )
            if action_result is None:
                continue
            changed = merge_changes(changed, action_result.changed)
            current_path = action_result.context.path

        if not changed and current_path == ctx.path:
            return None
        return ModuleResult(
            context=replace(ctx, path=current_path),
            changed=changed,
        )
