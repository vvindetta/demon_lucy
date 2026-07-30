from __future__ import annotations

import logging

from demon_lucy.runtime import (
    DEMON_LUCY_STARTUP_TEMPLATE,
    LogLevel,
    select_demon_lucy_modules,
)
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.lib.notifications import NotificationProvider


def _module_names(modules) -> list[str]:
    return [module.name for module in modules]


def test_select_demon_lucy_modules_include_only_requested_names():
    modules = select_demon_lucy_modules(include_names=["git", "status"])
    names = _module_names(modules)
    assert set(names) == {"git", "status"}


def test_select_demon_lucy_modules_exclude_removes_from_default_list():
    modules = select_demon_lucy_modules(exclude_names=["status", "git"])
    names = _module_names(modules)
    assert "status" not in names
    assert "git" not in names


def test_select_demon_lucy_modules_include_then_exclude_applies_exclude():
    modules = select_demon_lucy_modules(
        include_names=["git", "status", "archive"],
        exclude_names=["status"],
    )
    names = _module_names(modules)
    assert set(names) == {"git", "archive"}


def test_select_demon_lucy_modules_skips_unknown_include(caplog):
    with caplog.at_level(logging.ERROR, logger="demon_lucy.runtime"):
        modules = select_demon_lucy_modules(include_names=["git", "nope"])

    assert _module_names(modules) == ["git"]
    assert "runtime.module_unknown" in caplog.text
    assert "reason=include" in caplog.text
    assert "modules=nope" in caplog.text


def test_select_demon_lucy_modules_skips_unknown_exclude(caplog):
    with caplog.at_level(logging.ERROR, logger="demon_lucy.runtime"):
        modules = select_demon_lucy_modules(exclude_names=["git", "nope"])

    names = _module_names(modules)
    assert "git" not in names
    assert "status" in names
    assert "runtime.module_unknown" in caplog.text
    assert "reason=exclude" in caplog.text
    assert "modules=nope" in caplog.text


def test_select_demon_lucy_modules_can_return_empty_after_unknown_include(caplog):
    with caplog.at_level(logging.ERROR, logger="demon_lucy.runtime"):
        modules = select_demon_lucy_modules(include_names=["nope"])

    assert modules == []
    assert "runtime.module_unknown" in caplog.text
    assert "runtime.modules_empty" in caplog.text


def test_sys_modules_default_is_defined_in_startup_template():
    parsed = parse_args(args=[], template=DEMON_LUCY_STARTUP_TEMPLATE)
    modules = parsed.require("sys-modules").value
    assert modules
    assert "alias" in modules
    assert "workspace" in modules
    assert "graph" in modules
    assert "include" in modules
    assert "ai" not in modules
    assert "status" in modules


def test_fixed_system_string_domains_use_enums():
    parsed = parse_args(args=[], template=DEMON_LUCY_STARTUP_TEMPLATE)

    assert parsed.require("sys-log-level").value is LogLevel.WARNING
    assert (
        parsed.require("sys-notification-provider").value
        is NotificationProvider.AUTO
    )


def test_startup_template_system_flags_use_sys_prefix():
    flags = [item.name for item in DEMON_LUCY_STARTUP_TEMPLATE]

    assert flags
    assert all(flag.startswith("sys-") for flag in flags)


def test_voice_module_is_available_only_when_requested():
    requested = select_demon_lucy_modules(include_names=["voice"])
    parsed = parse_args(args=[], template=DEMON_LUCY_STARTUP_TEMPLATE)
    default_modules = select_demon_lucy_modules(
        include_names=parsed.require("sys-modules").value
    )

    assert _module_names(requested) == ["voice"]
    assert "voice" not in _module_names(default_modules)


def test_sys_ignore_move_paths_default_is_defined_in_startup_template():
    parsed = parse_args(args=[], template=DEMON_LUCY_STARTUP_TEMPLATE)
    assert parsed.require("sys-ignore-move-paths").value == [".status"]


def test_dynamic_block_allowed_values_are_visible_by_default():
    parsed = parse_args(args=[], template=DEMON_LUCY_STARTUP_TEMPLATE)

    assert (
        parsed.require("sys-dynamic-block-hide-allowed-values").value
        is False
    )


def test_sys_git_repo_lock_defaults_are_defined_in_startup_template():
    parsed = parse_args(args=[], template=DEMON_LUCY_STARTUP_TEMPLATE)
    assert (
        parsed.require("sys-git-repo-lock-wait-timeout-seconds").value
        == 30.0
    )
    assert (
        parsed.require("sys-git-repo-lock-retry-sleep-seconds").value
        == 0.2
    )
    assert (
        parsed.require("sys-git-repo-lock-stale-seconds").value
        == 1800.0
    )
