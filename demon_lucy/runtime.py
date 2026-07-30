from __future__ import annotations

import logging
from collections.abc import Iterable
from enum import StrEnum
from typing import List

from demon_lucy.lib.args.models import (
    KnownArg,
    ParsedArgs,
    Template,
)
from demon_lucy.lib.logfmt import log_record
from demon_lucy.lib.notifications import NotificationProvider
from demon_lucy.migrations import MIGRATIONS, Migration
from demon_lucy.modules.abstract_module import AbstractModule
from demon_lucy.modules.ai import Ai
from demon_lucy.modules.alias import Alias
from demon_lucy.modules.banner import Banner
from demon_lucy.modules.dropdir import DropDir
from demon_lucy.modules.formatter import Formatter
from demon_lucy.modules.graph import Graph
from demon_lucy.modules.include import Include
from demon_lucy.modules.git import Git
from demon_lucy.modules.kdeconnect_sync import KdeconnectSync
from demon_lucy.modules.linker import Linker
from demon_lucy.modules.plasma_widget import PlasmaWidget
from demon_lucy.modules.renamer import Renamer
from demon_lucy.modules.status import Status
from demon_lucy.modules.sys import Sys
from demon_lucy.modules.archive import Archive
from demon_lucy.modules.voice import Voice
from demon_lucy.modules.workspace import Workspace

logger = logging.getLogger(__name__)


class LogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


DEMON_LUCY_STARTUP_TEMPLATE: Template = [
    KnownArg(
        name="sys-config-path",
        value_type=str,
        default="config.txt",
        description="Path to the config file. Default: config.txt",
    ),
    KnownArg(
        name="sys-log-level",
        value_type=LogLevel,
        default=LogLevel.WARNING,
        description="Logging level: debug, info, warning, error, critical. Info shows Lucy event decisions; debug can include low-level library logs. Default: warning.",
    ),
    KnownArg(
        name="sys-log-format",
        value_type=str,
        default="%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d: %(message)s",
        description="Python logging format string. Default includes time, level, file, line, message.",
    ),
    KnownArg(
        name="sys-watch-paths",
        value_type=str,
        default=[],
        description="One or more directories to watch recursively. Example: --sys-watch-paths ~/notes ~/work/notes",
    ),
    KnownArg(
        name="sys-opened-event-cooldown-seconds",
        value_type=int,
        default=60,
        description="Cooldown for 'opened' events per file, in seconds. Prevents editor spam. Default: 60 seconds).",
    ),
    KnownArg(
        name="sys-disable-opened-events",
        value_type=bool,
        default=False,
        description="Ignore filesystem 'opened' events. Useful on Termux to reduce background wakeups.",
    ),
    KnownArg(
        name="sys-dynamic-block-hide-allowed-values",
        value_type=bool,
        default=False,
        description="Hide allowed parameter values in newly created dynamic blocks.",
    ),
    KnownArg(
        name="sys-notification-provider",
        value_type=NotificationProvider,
        default=NotificationProvider.AUTO,
        description="Notification provider. Supported: auto, termuxapi, desktop, disable. "
        "Default: auto (termuxapi when available, otherwise desktop).",
    ),
    KnownArg(
        name="sys-notification-min-interval-seconds",
        value_type=float,
        default=10.0,
        description="Minimum seconds between repeated notifications with the same key. Default: 10.0.",
    ),
    KnownArg(
        name="sys-notification-error-backoff-base-seconds",
        value_type=float,
        default=10.0,
        description="Base interval (seconds) for exponential error notification backoff.",
    ),
    KnownArg(
        name="sys-notification-error-backoff-max-seconds",
        value_type=float,
        default=1800.0,
        description="Maximum interval cap (seconds) for exponential error notification backoff.",
    ),
    KnownArg(
        name="sys-notification-error-burst-limit",
        value_type=int,
        default=3,
        description="Maximum number of error notifications allowed inside one burst window.",
    ),
    KnownArg(
        name="sys-notification-error-burst-window-seconds",
        value_type=float,
        default=600.0,
        description="Burst window length (seconds) used for global error notification limiting.",
    ),
    KnownArg(
        name="sys-ignore-paths",
        value_type=str,
        default=[],
        description="Skip module execution for files inside these paths. Example: --sys-ignore-paths ~/.cache ~/Notes/private",
    ),
    KnownArg(
        name="sys-ignore-move-paths",
        value_type=str,
        default=[".status"],
        description="Ignore internal move events under these paths. Relative paths are resolved under every watched root. Default: .status.",
    ),
    KnownArg(
        name="sys-git-repo-lock-wait-timeout-seconds",
        value_type=float,
        default=30.0,
        description="Maximum seconds to wait for Lucy's shared Git repo lock before skipping this cycle.",
    ),
    KnownArg(
        name="sys-git-repo-lock-retry-sleep-seconds",
        value_type=float,
        default=0.2,
        description="Seconds to sleep between attempts to acquire Lucy's shared Git repo lock.",
    ),
    KnownArg(
        name="sys-git-repo-lock-stale-seconds",
        value_type=float,
        default=1800.0,
        description="Age in seconds after which Lucy's shared Git repo lock is treated as stale.",
    ),
    KnownArg(
        name="sys-modules",
        value_type=str,
        default=[
            "alias",
            "workspace",
            "banner",
            "renamer",
            "linker",
            "formatter",
            "graph",
            "include",
            "archive",
            "status",
            "sys",
        ],
        description="Run only selected modules by name. Example: --sys-modules git status",
    ),
    KnownArg(
        name="sys-modules-exclude",
        value_type=str,
        default=[],
        description="Exclude modules from the selected/default module list. Example: --sys-modules-exclude status",
    ),
]


def run_config_migrations(config_path: str) -> list[Migration]:
    path_value = str(config_path).strip()
    if not path_value:
        return []

    migrated: list[Migration] = []
    for migration_factory in MIGRATIONS:
        migration = migration_factory(path_value)
        try:
            if migration.is_migration_needed():
                migration.migrate()
                migrated.append(migration)
        except Exception:
            logger.exception(
                log_record(
                    "config.migration_error",
                    migration=migration.get_migration_name(),
                    config_path=path_value,
                )
            )
    return migrated


def configure_logging(args: ParsedArgs) -> None:
    level: LogLevel = args.require("sys-log-level").value
    by_name = {
        LogLevel.DEBUG: logging.DEBUG,
        LogLevel.INFO: logging.INFO,
        LogLevel.WARNING: logging.WARNING,
        LogLevel.ERROR: logging.ERROR,
        LogLevel.CRITICAL: logging.CRITICAL,
    }
    logging.basicConfig(
        level=by_name[level],
        format=args.require("sys-log-format").value,
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


def log_startup_message(
    *,
    run_mode: str,
    modules: list[AbstractModule],
    args: ParsedArgs,
    extra_items: list[tuple[str, object]] | None = None,
) -> None:
    details: dict[str, object] = {
        "watch_paths": len(args.require("sys-watch-paths").value),
        "opened_events": (
            "off" if args.require("sys-disable-opened-events").value else "on"
        ),
        "log_level": args.require("sys-log-level").value,
    }
    if args.unknown:
        details["unknown_args"] = len(args.unknown)
    for key, value in extra_items or []:
        details[key] = value

    logging.warning(
        log_record(
            "runtime.start",
            mode=run_mode,
            modules="[" + (", ".join(module.name for module in modules) or "-") + "]",
            **details,
        )
    )


def normalize_name_list(values: Iterable[str]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for value in values:
        for part in str(value).replace(",", " ").split():
            normalized = part.strip()
            if not normalized or normalized in seen:
                continue
            names.append(normalized)
            seen.add(normalized)
    return names


def select_demon_lucy_modules(
    include_names: Iterable[str] | None = None,
    exclude_names: Iterable[str] | None = None,
) -> List[AbstractModule]:
    module_classes = [
        Alias,
        Workspace,
        Banner,
        Renamer,
        Status,
        Linker,
        DropDir,
        Formatter,
        Ai,
        Graph,
        Include,
        Archive,
        Sys,
        KdeconnectSync,
        Git,
        PlasmaWidget,
        Voice,
    ]
    requested_include = normalize_name_list(include_names or [])
    requested_exclude = normalize_name_list(exclude_names or [])
    include_set = set(requested_include)
    exclude_set = set(requested_exclude)
    available_names = {cls.name for cls in module_classes}

    unknown_include = include_set - available_names
    if unknown_include:
        logger.error(
            log_record(
                "runtime.module_unknown",
                reason="include",
                modules=sorted(unknown_include),
                available=sorted(available_names),
            )
        )
        include_set -= unknown_include

    unknown_exclude = exclude_set - available_names
    if unknown_exclude:
        logger.error(
            log_record(
                "runtime.module_unknown",
                reason="exclude",
                modules=sorted(unknown_exclude),
                available=sorted(available_names),
            )
        )
        exclude_set -= unknown_exclude

    selected_classes = module_classes
    if requested_include:
        selected_classes = [cls for cls in selected_classes if cls.name in include_set]
    if exclude_set:
        selected_classes = [
            cls for cls in selected_classes if cls.name not in exclude_set
        ]

    if not selected_classes:
        logger.error(log_record("runtime.modules_empty", reason="selection_empty"))

    return [cls() for cls in selected_classes]
