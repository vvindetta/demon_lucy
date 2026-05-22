from __future__ import annotations

import pytest

from lucy_notes_manager.runtime import LUCY_STARTUP_TEMPLATE, select_lucy_modules
from lucy_notes_manager.lib.args import parse_args


def _module_names(modules) -> list[str]:
    return [module.name for module in modules]


def test_select_lucy_modules_include_only_requested_names():
    modules = select_lucy_modules(include_names=["git", "status"])
    names = _module_names(modules)
    assert set(names) == {"git", "status"}


def test_select_lucy_modules_exclude_removes_from_default_list():
    modules = select_lucy_modules(exclude_names=["status", "git"])
    names = _module_names(modules)
    assert "status" not in names
    assert "git" not in names


def test_select_lucy_modules_include_then_exclude_applies_exclude():
    modules = select_lucy_modules(
        include_names=["git", "status", "today"],
        exclude_names=["status"],
    )
    names = _module_names(modules)
    assert set(names) == {"git", "today"}


def test_select_lucy_modules_rejects_unknown_include():
    with pytest.raises(ValueError, match="Unknown modules in include list"):
        select_lucy_modules(include_names=["nope"])


def test_select_lucy_modules_rejects_unknown_exclude():
    with pytest.raises(ValueError, match="Unknown modules in exclude list"):
        select_lucy_modules(exclude_names=["nope"])


def test_sys_modules_default_is_defined_in_startup_template():
    known_args, _unknown = parse_args(args=[], template=LUCY_STARTUP_TEMPLATE)
    assert known_args["sys_modules"]
