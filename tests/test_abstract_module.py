from __future__ import annotations

import pytest
from watchdog.events import FileModifiedEvent

from demon_lucy.lib.args.parser import ArgTemplate, Template
from demon_lucy.modules.abstract_module import AbstractModule, Context, System


class DemoModule(AbstractModule):
    name: str = "demo"


def test_default_module_priority_is_15():
    assert DemoModule().priority == 15


@pytest.mark.parametrize(
    "hook_name",
    ["created", "modified", "moved", "deleted", "opened"],
)
def test_default_module_hooks_are_noops(hook_name: str):
    module = DemoModule()
    ctx = Context(path="/tmp/x", config={}, arg_lines={})
    system = System(
        event=FileModifiedEvent("/tmp/x"), global_template=[], modules=[module]
    )

    hook = getattr(module, hook_name)
    assert hook(ctx, system) is None


def test_context_and_system_dataclasses_keep_values():
    module = DemoModule()
    event = FileModifiedEvent("/tmp/file")
    template: Template = [ArgTemplate(name="--x")]
    ctx = Context(path="/tmp/file", config={"x": ["1"]}, arg_lines={"x": [1]})
    system = System(event=event, global_template=template, modules=[module])

    assert ctx.path == "/tmp/file"
    assert ctx.config["x"] == ["1"]
    assert system.event is event
    assert system.global_template == template
    assert system.modules == [module]
    assert system.run_mode == "daemon"
