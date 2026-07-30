import logging
import sys
from dataclasses import replace

from demon_lucy.lib.args.models import (
    ArgSource,
    KnownArg,
    ParsedArgs,
    Template,
    UnknownArg,
)
from demon_lucy.lib.args.parser import (
    is_valid_flag_token,
    parse_args,
    split_arg_line,
)
from demon_lucy.lib.logfmt import log_record

logger = logging.getLogger(__name__)


def _parse_config_args(path: str, template: Template) -> ParsedArgs:
    parsed = parse_args(
        template=template,
        args=[],
    )

    with open(path, "r", encoding="utf-8") as file:
        for lineno, line in enumerate(file, start=1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                line_args = split_arg_line(line)
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
            parsed = parsed.merged_with(
                parse_args(
                    template=template,
                    args=line_args,
                    source=ArgSource.CONFIG,
                    include_defaults=False,
                    line=lineno,
                ),
            )
    return parsed


def parse_note_args(
    path: str,
    template: Template,
) -> ParsedArgs:
    try:
        with open(path, "r", encoding="utf-8") as file:
            lines = file.readlines()
    except FileNotFoundError:
        logger.info(log_record("args.file_missing", path=path))
        return ParsedArgs()
    except (UnicodeDecodeError, OSError) as exc:
        logger.info(log_record("args.file_unreadable", path=path, error=exc))
        return ParsedArgs()

    merged_known: dict[str, KnownArg] = {}
    merged_unknown: list[UnknownArg] = []

    for lineno, raw_line in enumerate(lines, start=1):
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        try:
            tokens = split_arg_line(stripped)
        except ValueError as exc:
            logger.info(
                log_record(
                    "args.file_line_invalid",
                    path=path,
                    line=lineno,
                    error=exc,
                )
            )
            continue

        if not tokens or not is_valid_flag_token(tokens[0]):
            continue

        line_parsed = parse_args(
            template=template,
            args=tokens,
            source=ArgSource.FILE,
            include_defaults=False,
            line=lineno,
        )

        merged_unknown.extend(line_parsed.unknown)

        for argument in line_parsed.known:
            existing = merged_known.get(argument.name)
            if (
                existing is not None
                and isinstance(argument.value, list)
                and argument.value
            ):
                merged_known[argument.name] = replace(
                    argument,
                    value=[*existing.value, *argument.value],
                    lines=(*existing.lines, *argument.lines),
                )
            else:
                merged_known[argument.name] = argument

    return ParsedArgs(
        known=tuple(merged_known.values()),
        unknown=tuple(merged_unknown),
    )


def load_args(template: Template) -> ParsedArgs:
    startup_args = parse_args(
        template=template,
        args=sys.argv[1:],
    )
    config_path_arg = startup_args.find("sys-config-path")
    if config_path_arg is None:
        return startup_args

    try:
        config_args = _parse_config_args(
            path=config_path_arg.value,
            template=template,
        )
    except FileNotFoundError:
        logger.warning(
            log_record(
                "args.config_missing",
                path=config_path_arg.value,
                fallback="startup_args",
            )
        )
        return startup_args

    return config_args.merged_with(
        ParsedArgs(
            known=startup_args.known_from(ArgSource.CLI),
            unknown=startup_args.unknown,
        ),
    )
