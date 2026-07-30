from __future__ import annotations

import pytest
from watchdog.events import FileModifiedEvent

from demon_lucy.lib.args.models import (
    ArgSource,
    KnownArg,
    ParsedArgs,
    Template,
)
from demon_lucy.modules.abstract_module import AbstractModule, Context, System


class DemoModule(AbstractModule):
    name: str = "demo"


def test_default_module_priority_is_15():
    assert DemoModule().priority == 15


@pytest.mark.parametrize(
    "hook_name",
    ["created", "modified", "moved", "deleted", "opened", "cli"],
)
def test_default_module_hooks_are_noops(hook_name: str):
    module = DemoModule()
    event = FileModifiedEvent("/tmp/x")
    ctx = Context(
        path="/tmp/x",
        args=ParsedArgs(),
        run_mode="daemon",
        event_id="evt-test",
        event=event,
    )
    system = System(
        global_template=[],
        modules=[module],
    )

    hook = getattr(module, hook_name)
    assert hook(ctx, system) is None


def test_context_and_system_dataclasses_keep_values():
    module = DemoModule()
    event = FileModifiedEvent("/tmp/file")
    template: Template = [KnownArg(name="x")]
    parsed_args = ParsedArgs(
        known=(
            KnownArg(
                name="x",
                value=["1"],
                source=ArgSource.FILE,
                lines=(1,),
            ),
        )
    )
    ctx = Context(
        path="/tmp/file",
        args=parsed_args,
        run_mode="daemon",
        event_id="evt-test",
        event=event,
    )
    system = System(global_template=template, modules=[module])

    assert ctx.path == "/tmp/file"
    assert ctx.args is parsed_args
    assert ctx.args.require("x").value == ["1"]
    assert ctx.args.require("x").source is ArgSource.FILE
    assert ctx.args.require("x").lines == (1,)
    assert ctx.event is event
    assert ctx.event_id == "evt-test"
    assert ctx.run_mode == "daemon"
    assert system.global_template == template
    assert system.modules == [module]
