from __future__ import annotations

import pytest

from demon_lucy.runtime import (
    DEMON_LUCY_STARTUP_TEMPLATE,
    select_demon_lucy_modules,
)
from demon_lucy.lib.args.parser import parse_args


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


def test_select_demon_lucy_modules_rejects_unknown_include():
    with pytest.raises(ValueError, match="Unknown modules in include list"):
        select_demon_lucy_modules(include_names=["nope"])


def test_select_demon_lucy_modules_rejects_unknown_exclude():
    with pytest.raises(ValueError, match="Unknown modules in exclude list"):
        select_demon_lucy_modules(exclude_names=["nope"])


def test_sys_modules_default_is_defined_in_startup_template():
    known_args, _unknown = parse_args(args=[], template=DEMON_LUCY_STARTUP_TEMPLATE)
    assert known_args["sys_modules"]


def test_sys_ignore_move_paths_default_is_defined_in_startup_template():
    known_args, _unknown = parse_args(args=[], template=DEMON_LUCY_STARTUP_TEMPLATE)
    assert known_args["sys_ignore_move_paths"] == [".status"]
