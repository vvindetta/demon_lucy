import argparse
import logging
import shlex
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Tuple, TypeVar

from demon_lucy.lib.logfmt import log_record

logger = logging.getLogger(__name__)
EnumType = TypeVar("EnumType", bound=Enum)


@dataclass(frozen=True, kw_only=True)
class ArgTemplate:
    name: str
    value_type: type = str
    default: Any = None
    description: str = ""
    required: bool = False
    params: tuple["ArgTemplate", ...] = ()


Template = List[ArgTemplate]

ArgLines = Dict[str, List[int]]


def enum_value_text(value: object) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def template_allowed_values(template: ArgTemplate) -> tuple[str, ...]:
    try:
        if not issubclass(template.value_type, Enum):
            return ()
    except TypeError:
        return ()
    return tuple(enum_value_text(member) for member in template.value_type)


def parse_enum_value(enum_type: type[EnumType], value: object) -> EnumType:
    if isinstance(value, enum_type):
        return value
    value_text = enum_value_text(value).strip().casefold()
    for member in enum_type:
        if enum_value_text(member).casefold() == value_text:
            return member
    allowed = "|".join(enum_value_text(member) for member in enum_type)
    raise ValueError(f"unsupported {enum_type.__name__} value: {value}; use {allowed}")


def _argparse_value_type(template: ArgTemplate):
    if not template_allowed_values(template):
        return template.value_type

    def parse_value(value: str) -> Enum:
        try:
            return parse_enum_value(template.value_type, value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc

    return parse_value


def normalize_template_params(
    values: Mapping[str, object],
    params: tuple[ArgTemplate, ...],
) -> dict[str, object]:
    params_by_name = {param.name: param for param in params}
    unknown = sorted(set(values) - set(params_by_name))
    if unknown:
        raise ValueError(f"unknown argument parameter: {unknown[0]}")

    normalized: dict[str, object] = {}
    for param in params:
        raw_value = values.get(param.name, "")
        value_text = enum_value_text(raw_value).strip()
        if not value_text:
            if param.default is not None:
                raw_value = param.default
                value_text = enum_value_text(raw_value).strip()
            elif param.required:
                raise ValueError(f"missing argument parameter: {param.name}")
            else:
                continue

        allowed_values = template_allowed_values(param)
        if allowed_values:
            try:
                normalized[param.name] = parse_enum_value(
                    param.value_type,
                    value_text,
                )
            except ValueError as exc:
                raise ValueError(
                    f"unsupported argument parameter {param.name}: {value_text}"
                ) from exc
            continue

        if param.value_type is str:
            normalized[param.name] = value_text
            continue
        try:
            normalized[param.name] = param.value_type(value_text)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid argument parameter {param.name}: {value_text}"
            ) from exc
    return normalized


def flag_to_dest(flag: str) -> str:
    return flag.lstrip("-").replace("-", "_")


def is_valid_flag_token(token: str) -> bool:
    if not token.startswith("--"):
        return False
    head = token.split("=", 1)[0]  # allow --flag=value
    if len(head) < 3 or not head[2].isalpha():  # must start with letter
        return False
    for ch in head[3:]:
        if not (ch.isalnum() or ch in ("_", "-")):
            return False
    return True


def parse_args(
    args: list[str],
    template: Template,
    *,
    include_defaults: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)

    for item in template:
        dest = flag_to_dest(item.name)
        arg_default = item.default if include_defaults else argparse.SUPPRESS

        if item.value_type is bool:
            parser.add_argument(
                item.name,
                dest=dest,
                action="store_true",  # --flag -> True
                default=arg_default,  # missing -> default (usually False)
            )
        elif isinstance(item.default, list):
            parser.add_argument(
                item.name,
                dest=dest,
                type=_argparse_value_type(item),
                nargs="*",
                default=(list(item.default) if include_defaults else argparse.SUPPRESS),
            )
        else:
            parser.add_argument(
                item.name,
                dest=dest,
                type=_argparse_value_type(item),
                default=arg_default,
            )

    try:
        namespace, unknown_args = parser.parse_known_args(args)
    except SystemExit:
        return {}, args

    return vars(namespace), unknown_args


def get_config_args(path: str, template: Template) -> Tuple[Dict[str, Any], List[str]]:
    """
    Read arguments from a config file and parse them with the same template.
    """
    config_args_raw: List[str] = []

    with open(path, "r", encoding="utf-8") as file:
        for lineno, line in enumerate(file, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                config_args_raw.extend(shlex.split(line))
            except ValueError as exc:
                logger.warning(
                    log_record(
                        "args.config_line_invalid",
                        path=path,
                        line=lineno,
                        error=exc,
                    )
                )
                continue

    return parse_args(template=template, args=config_args_raw)


def merge_known_args(
    args: Dict[str, Any], overwrite_args: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Merge two dicts of parsed arguments.

    - 'args' usually comes from config file (defaults).
    - 'overwrite_args' usually comes from CLI.

    Rules:
        - None or "" in overwrite_args = "not provided", do NOT overwrite.
        - Everything else in overwrite_args overrides args.
    """
    merged_args = dict(args)
    for key, value in overwrite_args.items():
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        merged_args[key] = value
    return merged_args


def setup_config_and_cli_args(
    template: Template,
) -> Tuple[Dict[str, Any], List[str]]:
    """
    High-level helper:

    1. Parse startup (CLI) arguments from sys.argv[1:].
    2. Try to read config file.
    3. Merge config and CLI args (CLI wins).
    4. Return (known_args_dict, unknown_args_list).
    """
    # 1. Parse CLI args
    known_startup_args, unknown_startup_args = parse_args(
        template=template,
        args=sys.argv[1:],
    )
    config_path = known_startup_args.get("sys_config_path")
    if not isinstance(config_path, str) or not config_path.strip():
        return known_startup_args, unknown_startup_args
    defaults_by_key: Dict[str, Any] = {}
    for item in template:
        defaults_by_key[flag_to_dest(item.name)] = item.default

    # 2. Parse config-file args
    try:
        known_config_args, unknown_config_args = get_config_args(
            path=config_path,
            template=template,
        )
    except FileNotFoundError:
        logger.warning(
            log_record(
                "args.config_missing",
                path=config_path,
                fallback="startup_args",
            )
        )
        return known_startup_args, unknown_startup_args

    # 3. Merge: CLI first, than config
    startup_overrides: Dict[str, Any] = {}
    for key, value in known_startup_args.items():
        if key not in defaults_by_key:
            startup_overrides[key] = value
            continue
        if value != defaults_by_key[key]:
            startup_overrides[key] = value

    merged_known = merge_known_args(
        args=known_config_args,
        overwrite_args=startup_overrides,
    )
    merged_unknown = unknown_config_args + unknown_startup_args

    return merged_known, merged_unknown


def get_args_from_file(
    path: str,
    template: Template,
) -> Tuple[Dict[str, Any], List[str], ArgLines]:
    """
    Reads args from file, accepting only lines that begin with a valid flag:
      --<name>   (where <name> starts with a letter)
    Rejects:
      -- text
      ---text
      ---
      text --flag
    """

    try:
        with open(path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        logger.info(log_record("args.file_missing", path=path))
        return {}, [], {}
    except (UnicodeDecodeError, OSError) as exc:
        logger.info(log_record("args.file_unreadable", path=path, error=exc))
        return {}, [], {}

    if not lines:
        return {}, [], {}

    merged_known: Dict[str, Any] = {}
    merged_unknown: List[str] = []
    arg_lines: ArgLines = {"__unknown__": []}

    for lineno, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # line must start with a valid flag token
        start = stripped.split()[0]
        if not is_valid_flag_token(start):
            continue

        try:
            tokens = shlex.split(stripped, comments=False, posix=True)
        except ValueError as e:
            logger.info(
                log_record("args.file_line_invalid", path=path, line=lineno, error=e)
            )
            continue

        # collect "--flag" + values until next flag
        cli_tokens: List[str] = []
        i = 0
        while i < len(tokens):
            tok = tokens[i]
            if not is_valid_flag_token(tok):
                i += 1
                continue

            cli_tokens.append(tok)
            i += 1
            while i < len(tokens):
                nxt = tokens[i]
                if is_valid_flag_token(nxt):
                    break
                cli_tokens.append(nxt)
                i += 1

        if not cli_tokens:
            continue

        line_known, line_unknown = parse_args(
            template=template,
            args=cli_tokens,
            include_defaults=False,
        )

        if line_unknown:
            merged_unknown.extend(line_unknown)
            arg_lines["__unknown__"].extend([lineno] * len(line_unknown))

        for key, value in line_known.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue

            count = len(value) if isinstance(value, list) else 1
            arg_lines.setdefault(key, []).extend([lineno] * count)

            if key not in merged_known or merged_known[key] in (None, ""):
                merged_known[key] = value
                continue

            if isinstance(merged_known[key], list) and isinstance(value, list):
                merged_known[key].extend(value)
            else:
                merged_known[key] = value

    return merged_known, merged_unknown, arg_lines
