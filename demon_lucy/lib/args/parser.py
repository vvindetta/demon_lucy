import argparse
import re
import shlex
from collections.abc import Callable, Mapping
from dataclasses import replace
from enum import Enum
from itertools import groupby
from typing import Any, TypeVar

from demon_lucy.lib.args.models import (
    ArgParam,
    ArgSource,
    KnownArg,
    ParsedArgs,
    Template,
    UnknownArg,
)


ARG_NAME_PATTERN = r"[a-z][a-z0-9-]*"
_EnumType = TypeVar("_EnumType", bound=Enum)


def split_arg_line(line: str) -> list[str]:
    """Split a Lucy arg line while preserving backslashes as literal text."""
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    lexer.escape = ""
    return list(lexer)


def is_valid_flag_token(token: str) -> bool:
    head = token.split("=", 1)[0]
    return (
        head.startswith("--") and re.fullmatch(ARG_NAME_PATTERN, head[2:]) is not None
    )


def _enum_value_text(value: object) -> str:
    return str(value.value if isinstance(value, Enum) else value)


def _parse_enum_value(enum_type: type[_EnumType], value: object) -> _EnumType:
    if isinstance(value, enum_type):
        return value
    value_text = _enum_value_text(value).strip().casefold()
    for member in enum_type:
        if _enum_value_text(member).casefold() == value_text:
            return member
    allowed = "|".join(_enum_value_text(member) for member in enum_type)
    raise ValueError(f"unsupported {enum_type.__name__} value: {value}; use {allowed}")


def normalize_arg_params(
    values: Mapping[str, object],
    params: tuple[ArgParam, ...],
) -> dict[str, object]:
    param_names = {param.name for param in params}
    unknown = sorted(set(values) - param_names)
    if unknown:
        raise ValueError(f"unknown argument parameter: {unknown[0]}")

    normalized: dict[str, object] = {}
    for param in params:
        raw_value = values.get(param.name, "")
        value_text = _enum_value_text(raw_value).strip()
        if not value_text:
            if param.default is not None:
                raw_value = param.default
                value_text = _enum_value_text(raw_value).strip()
            elif param.required:
                raise ValueError(f"missing argument parameter: {param.name}")
            else:
                continue

        if issubclass(param.value_type, Enum):
            try:
                normalized[param.name] = _parse_enum_value(
                    param.value_type,
                    value_text,
                )
            except ValueError as exc:
                raise ValueError(
                    f"unsupported argument parameter {param.name}: {value_text}"
                ) from exc
            continue

        try:
            normalized[param.name] = param.value_type(value_text)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid argument parameter {param.name}: {value_text}"
            ) from exc
    return normalized


def _arg_name_to_dest(name: str) -> str:
    return name.replace("-", "_")


def _argparse_type(
    value_type: type[Any],
) -> Callable[[str], Any]:
    if not issubclass(value_type, Enum):
        return value_type

    def parse_value(value: str) -> Enum:
        try:
            return _parse_enum_value(value_type, value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    return parse_value


def parse_args(
    args: list[str],
    template: Template,
    *,
    source: ArgSource = ArgSource.CLI,
    include_defaults: bool = True,
    line: int | None = None,
) -> ParsedArgs:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    for item in template:
        options: dict[str, Any] = {
            "dest": _arg_name_to_dest(item.name),
            "default": argparse.SUPPRESS,
        }
        if item.value_type is bool:
            options["action"] = "store_true"
        else:
            options["type"] = _argparse_type(item.value_type)
            if isinstance(item.default, list):
                options["nargs"] = "*"
        parser.add_argument(f"--{item.name}", **options)

    try:
        namespace, unknown = parser.parse_known_args(args)
    except SystemExit:
        return ParsedArgs(
            unknown=tuple(
                UnknownArg(token=token, source=source, line=line) for token in args
            ),
        )

    values = vars(namespace)
    known: list[KnownArg] = []
    for item in template:
        dest = _arg_name_to_dest(item.name)
        if dest in values:
            value = values[dest]
            value_source = source
        elif include_defaults:
            value = (
                list(item.default) if isinstance(item.default, list) else item.default
            )
            value_source = ArgSource.DEFAULT
        else:
            continue
        lines = (
            ()
            if line is None
            else (line,) * (len(value) if isinstance(value, list) else 1)
        )
        known.append(
            replace(
                item,
                value=value,
                source=value_source,
                lines=lines,
            )
        )

    return ParsedArgs(
        known=tuple(known),
        unknown=tuple(
            UnknownArg(token=token, source=source, line=line) for token in unknown
        ),
    )


def resolve_unknown_args(
    args: tuple[UnknownArg, ...],
    template: Template,
) -> ParsedArgs:
    parsed = ParsedArgs()
    groups = groupby(args, key=lambda argument: (argument.source, argument.line))
    for (source, line), arguments in groups:
        parsed = parsed.merged_with(
            parse_args(
                args=[argument.token for argument in arguments],
                template=template,
                source=source,
                include_defaults=False,
                line=line,
            )
        )
    return parsed
