from dataclasses import replace

from watchdog.events import FileModifiedEvent, FileSystemEvent

from demon_lucy.lib.args.models import (
    ArgSource,
    ParsedArgs,
    Template,
)
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.abstract_module import Context, RunMode


def make_args(
    template: Template,
    values: dict[str, object] | None = None,
    *,
    lines: dict[str, tuple[int, ...] | list[int]] | None = None,
    source: ArgSource = ArgSource.FILE,
) -> ParsedArgs:
    parsed = parse_args(args=[], template=template)
    line_values = lines or {}
    resolved = []
    for name, value in (values or {}).items():
        item = next(argument for argument in template if argument.name == name)
        resolved.append(
            replace(
                item,
                value=value,
                source=source,
                lines=tuple(line_values.get(name, ())),
            )
        )
    return parsed.merged_with(ParsedArgs(known=tuple(resolved)))


def make_context(
    path: str,
    template: Template,
    values: dict[str, object] | None = None,
    *,
    lines: dict[str, tuple[int, ...] | list[int]] | None = None,
    source: ArgSource = ArgSource.FILE,
    run_mode: RunMode = "daemon",
    event_id: str = "evt-test",
    event: FileSystemEvent | None = None,
) -> Context:
    return Context(
        path=path,
        args=make_args(
            template,
            values,
            lines=lines,
            source=source,
        ),
        run_mode=run_mode,
        event_id=event_id,
        event=event or FileModifiedEvent(path),
    )
