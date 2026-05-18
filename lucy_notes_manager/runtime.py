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
        "info",
        "Logging level: debug, info, warning, error, critical. Default: info.",
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
        "termuxapi",
        "Notification provider. Supported: termuxapi, desktop, disable. Default: termuxapi.",
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
        "--sys-ignore-paths",
        str,
        [],
        "Skip module execution for files inside these paths. Example: --sys-ignore-paths ~/.cache ~/Notes/private",
        False,
    ),
]


def resolve_logging_level(raw_level: str) -> int:
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
        raise ValueError(
            f"Unsupported --sys-log-level '{raw_level}'. Use: {allowed}."
        )
    return by_name[normalized]


def configure_logging(config: dict) -> None:
    log_level = resolve_logging_level(config["sys_log_level"])
    logging.basicConfig(
        level=log_level,
        format=config["sys_log_format"],
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
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
) -> List[AbstractModule]:
    modules: List[AbstractModule] = [
        Banner(),
        Renamer(),
        Status(),
        Linker(),
        DropDir(),
        Formatter(),
        Today(),
        Sys(),
        Git(),
        PlasmaSync(),
    ]
    include_set = set(normalize_name_list(include_names or []))
    available_names = {module.name for module in modules}

    unknown_include = include_set - available_names
    if unknown_include:
        available_sorted = ", ".join(sorted(available_names))
        requested_sorted = ", ".join(sorted(unknown_include))
        raise ValueError(
            "Unknown modules in include list: "
            f"{requested_sorted}. Available: {available_sorted}"
        )

    if include_set:
        modules = [module for module in modules if module.name in include_set]

    if not modules:
        raise ValueError("No modules selected after include filter.")

    return modules
