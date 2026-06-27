import argparse
import logging
import shlex
import sys
from typing import Any, Dict, List, Tuple

from demon_lucy.lib.logfmt import log_record

logger = logging.getLogger(__name__)


"""
Template example:
    [
        ("--rename", str, None, "Will rename file", False),
        ("--banner", str, "date", "Draws ASCII banner", False),
        ("--tags", str, [], "Multi-value argument", False),
        ("--required", str, None, "Required value", True),
    ]
"""
TemplateItem = Tuple[str, type, Any, str, bool]
Template = List[TemplateItem]

ArgLines = Dict[str, List[int]]


def parse_template_item(item: TemplateItem) -> tuple[str, type, Any, str, bool]:
    flag, typ, default, desc, required = item
    return flag, typ, default, desc, bool(required)


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
        flag, typ, default, _desc, _required = parse_template_item(item)
        dest = flag_to_dest(flag)
        arg_default = default if include_defaults else argparse.SUPPRESS

        if typ is bool:
            parser.add_argument(
                flag,
                dest=dest,
                action="store_true",  # --flag -> True
                default=arg_default,  # missing -> default (usually False)
            )
        elif isinstance(default, list):
            parser.add_argument(
                flag,
                dest=dest,
                type=typ,
                nargs="*",
                default=list(default) if include_defaults else argparse.SUPPRESS,
            )
        else:
            parser.add_argument(
                flag,
                dest=dest,
                type=typ,
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
        flag, _typ, default, _desc, _required = parse_template_item(item)
        defaults_by_key[flag_to_dest(flag)] = default

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
