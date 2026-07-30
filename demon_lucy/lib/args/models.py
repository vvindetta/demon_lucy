from dataclasses import dataclass
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
    value_type: type = str
    default: Any = None
    required: bool = False


@dataclass(frozen=True, kw_only=True)
class KnownArg:
    name: str
    value_type: type = str
    default: Any = None
    description: str = ""
    required: bool = False
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
        return tuple(
            argument
            for argument in self.known
            if argument.source is source
        )

    def unknown_from(self, source: ArgSource) -> tuple[UnknownArg, ...]:
        return tuple(
            argument
            for argument in self.unknown
            if argument.source is source
        )

    def merged_with(self, overwrite: Self) -> Self:
        known = {argument.name: argument for argument in self.known}
        known.update({argument.name: argument for argument in overwrite.known})
        return type(self)(
            known=tuple(known.values()),
            unknown=(*self.unknown, *overwrite.unknown),
        )
