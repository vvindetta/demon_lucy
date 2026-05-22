from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import List

from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.modules.abstract_module import AbstractModule
from lucy_notes_manager.modules.banner import Banner
from lucy_notes_manager.modules.dropdir import DropDir
from lucy_notes_manager.modules.formatter import Formatter
from lucy_notes_manager.modules.git import Git
from lucy_notes_manager.modules.kdeconnect_sync import KdeconnectSync
from lucy_notes_manager.modules.linker import Linker
from lucy_notes_manager.modules.plasma_sync import PlasmaSync
from lucy_notes_manager.modules.renamer import Renamer
from lucy_notes_manager.modules.status import Status
from lucy_notes_manager.modules.sys import Sys
from lucy_notes_manager.modules.today import Today

LUCY_STARTUP_TEMPLATE: Template = [
    (
        "--sys-config-path",
        str,
        "config.txt",
        "Path to the config file. Default: config.txt",
        False,
    ),
    (
        "--sys-log-level",
        str,
        "warning",
        "Logging level: debug, info, warning, error, critical. Default: warning.",
        False,
    ),
    (
        "--sys-log-format",
        str,
        "%(asctime)s [%(levelname)s] %(filename)s:%(lineno)d: %(message)s",
        "Python logging format string. Default includes time, level, file, line, message.",
        False,
    ),
    (
        "--sys-watch-paths",
        str,
        [],
        "One or more directories to watch recursively. Example: --sys-watch-paths ~/notes ~/work/notes",
        False,
    ),
    (
        "--sys-opened-event-cooldown-seconds",
        int,
        30,
        "Cooldown for 'opened' events per file, in seconds. Prevents editor spam. Default: 30 seconds).",
        False,
    ),
    (
        "--sys-disable-opened-events",
        bool,
        False,
        "Ignore filesystem 'opened' events. Useful on Termux to reduce background wakeups.",
        False,
    ),
    (
        "--sys-notification-provider",
        str,
        "auto",
        "Notification provider. Supported: auto, termuxapi, desktop, disable. "
        "Default: auto (termuxapi when available, otherwise desktop).",
        False,
    ),
    (
        "--sys-notification-min-interval-seconds",
        float,
        10.0,
        "Minimum seconds between repeated notifications with the same key. Default: 10.0.",
        False,
    ),
    (
        "--sys-notification-error-backoff-base-seconds",
        float,
        10.0,
        "Base interval (seconds) for exponential error notification backoff.",
        False,
    ),
    (
        "--sys-notification-error-backoff-max-seconds",
        float,
        1800.0,
        "Maximum interval cap (seconds) for exponential error notification backoff.",
        False,
    ),
    (
        "--sys-notification-error-burst-limit",
        int,
        3,
        "Maximum number of error notifications allowed inside one burst window.",
        False,
    ),
    (
        "--sys-notification-error-burst-window-seconds",
        float,
        600.0,
        "Burst window length (seconds) used for global error notification limiting.",
        False,
    ),
    (
        "--sys-ignore-paths",
        str,
        [],
        "Skip module execution for files inside these paths. Example: --sys-ignore-paths ~/.cache ~/Notes/private",
        False,
    ),
    (
        "--sys-modules",
        str,
        ["banner", "renamer", "linker", "formatter", "today", "sys"],
        "Run only selected modules by name. Example: --sys-modules git status",
        False,
    ),
    (
        "--sys-modules-exclude",
        str,
        [],
        "Exclude modules from the selected/default module list. Example: --sys-modules-exclude status",
        False,
    ),
]


def configure_logging(config: dict) -> None:
    raw_level = config["sys_log_level"]
    normalized = str(raw_level).strip().lower()
    by_name = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "warn": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    if normalized not in by_name:
        allowed = ", ".join(["debug", "info", "warning", "error", "critical"])
        raise ValueError(f"Unsupported --sys-log-level '{raw_level}'. Use: {allowed}.")
    logging.basicConfig(
        level=by_name[normalized],
        format=config["sys_log_format"],
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )


def log_startup_message(
    *,
    run_mode: str,
    modules: list[AbstractModule],
    config: dict,
    unknown_args: list[str] | None = None,
    extra_items: list[tuple[str, object]] | None = None,
) -> None:
    details: list[str] = [
        f"watch_paths={len(config.get('sys_watch_paths') or [])}",
        f"opened_events={'off' if config.get('sys_disable_opened_events') else 'on'}",
        f"log_level={config.get('sys_log_level', '')}",
        *([f"unknown_args={len(unknown_args)}"] if unknown_args else []),
        *(f"{key}={value}" for key, value in (extra_items or [])),
    ]

    logging.warning(
        "LUCY START | mode=%s | modules=[%s] | %s",
        run_mode,
        ", ".join(module.name for module in modules) or "-",
        " | ".join(details),
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


def select_lucy_modules(
    include_names: Iterable[str] | None = None,
    exclude_names: Iterable[str] | None = None,
) -> List[AbstractModule]:
    module_classes = [
        Banner,
        Renamer,
        Status,
        Linker,
        DropDir,
        Formatter,
        Today,
        Sys,
        KdeconnectSync,
        Git,
        PlasmaSync,
    ]
    include_set = set(normalize_name_list(include_names or []))
    exclude_set = set(normalize_name_list(exclude_names or []))
    available_names = {cls.name for cls in module_classes}

    unknown_include = include_set - available_names
    if unknown_include:
        raise ValueError(
            "Unknown modules in include list: "
            f"{', '.join(sorted(unknown_include))}. "
            f"Available: {', '.join(sorted(available_names))}"
        )

    unknown_exclude = exclude_set - available_names
    if unknown_exclude:
        raise ValueError(
            "Unknown modules in exclude list: "
            f"{', '.join(sorted(unknown_exclude))}. "
            f"Available: {', '.join(sorted(available_names))}"
        )

    selected_classes = module_classes
    if include_set:
        selected_classes = [cls for cls in selected_classes if cls.name in include_set]
    if exclude_set:
        selected_classes = [
            cls for cls in selected_classes if cls.name not in exclude_set
        ]

    if not selected_classes:
        raise ValueError("No modules selected after include filter.")

    return [cls() for cls in selected_classes]
