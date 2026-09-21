from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, Self


class ArgSource(StrEnum):
    DEFAULT = "default"
    CONFIG = "config"
    FILE = "file"
    CLI = "cli"


@dataclass(frozen=True, kw_only=True)
class ArgParam:
    name: str
    value_type: type[Any] = str
    default: Any = None
    required: bool = False


@dataclass(frozen=True, kw_only=True)
class KnownArg:
    name: str
    value_type: type[Any] = str
    default: Any = None
    description: str = ""
    required: bool = False
    # Repeatable str[] groups whose values may themselves look like flags.
    literal_value_count: int = 0
    params: tuple[ArgParam, ...] = ()
    value: Any = None
    source: ArgSource | None = None
    lines: tuple[int, ...] = ()


Template = list[KnownArg]


@dataclass(frozen=True, kw_only=True)
class UnknownArg:
    token: str
    source: ArgSource
    line: int | None = None


@dataclass(frozen=True)
class ParsedArgs:
    known: tuple[KnownArg, ...] = ()
    unknown: tuple[UnknownArg, ...] = ()

    def find(self, name: str) -> KnownArg | None:
        return next(
            (argument for argument in self.known if argument.name == name),
            None,
        )

    def require(self, name: str) -> KnownArg:
        argument = self.find(name)
        if argument is None:
            raise KeyError(name)
        return argument

    def known_from(self, source: ArgSource) -> tuple[KnownArg, ...]:
        return tuple(argument for argument in self.known if argument.source is source)

    def unknown_from(self, source: ArgSource) -> tuple[UnknownArg, ...]:
        return tuple(argument for argument in self.unknown if argument.source is source)

    def merged_with(self, overwrite: Self, *, accumulate: bool = False) -> Self:
        known = {argument.name: argument for argument in self.known}
        for argument in overwrite.known:
            existing = known.get(argument.name)
            if (
                accumulate
                and argument.literal_value_count
                and existing is not None
                and existing.source is argument.source
            ):
                argument = replace(
                    argument,
                    value=[*existing.value, *argument.value],
                    lines=(*existing.lines, *argument.lines),
                )
            known[argument.name] = argument
        return type(self)(
            known=tuple(known.values()),
            unknown=(*self.unknown, *overwrite.unknown),
        )
