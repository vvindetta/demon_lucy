from __future__ import annotations

import os
from pathlib import Path
import sys
import time

import pytest

import main_oneshot as oneshot


def _source(root: Path, name: str, text: str, age_hours: float) -> Path:
    path = root / name
    path.write_text(text, encoding="utf-8")
    timestamp = time.time() - age_hours * 3600
    os.utime(path, (timestamp, timestamp))
    return path


def _run_archive(
    root: Path,
    monkeypatch,
    rules: str,
    *,
    target: Path | None = None,
    cli: list[str] | None = None,
) -> int:
    monkeypatch.setattr(oneshot, "configure_logging", lambda _args: None)
    (root / ".git").mkdir(exist_ok=True)
    config = root / "lucy.cfg"
    config.write_text(
        "--sys-modules archive\n"
        "--sys-notification-provider disable\n"
        "--archive-force-filesystem-mtime\n"
        f'--sys-watch-paths "{root}"\n' + rules,
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "main_oneshot.py",
            "--sys-config-path",
            str(config),
            "--oneshot-paths",
            str(target or root),
            *(cli or []),
        ],
    )
    return oneshot.main()


@pytest.mark.parametrize("rule_source", ["config", "note"])
@pytest.mark.parametrize("second_state", ["stale", "recent", "empty"])
def test_oneshot_processes_each_pair_with_its_own_interval(
    tmp_path: Path, monkeypatch, rule_source: str, second_state: str
) -> None:
    first = _source(tmp_path, "→ now.md", "first entry\n", 3)
    second_text = "" if second_state == "empty" else "second entry\n"
    second = _source(
        tmp_path, "today.md", second_text, 3 if second_state == "recent" else 11
    )
    past = tmp_path / "past.md"
    year = tmp_path / "year.md"
    rules = (
        f'--archive-auto-pair "{first}" "{past}" 2 text\n'
        f'--archive-auto-pair "{second}" "{year}" 10 text\n'
    )
    target = None
    if rule_source == "note":
        target = _source(tmp_path, "trigger.md", rules, 0)
        rules = ""

    assert _run_archive(tmp_path, monkeypatch, rules, target=target) == 0

    assert first.read_text(encoding="utf-8") == ""
    assert past.read_text(encoding="utf-8").endswith("first entry\n")
    if second_state == "stale":
        assert second.read_text(encoding="utf-8") == ""
        assert year.read_text(encoding="utf-8").endswith("second entry\n")
    else:
        assert second.read_text(encoding="utf-8") == second_text
        assert not year.exists()


def test_repeated_pairs_preserve_optional_values_and_mode_like_filenames(
    tmp_path: Path, monkeypatch
) -> None:
    first = _source(tmp_path, "now.md", "first entry\n", 13)
    second = _source(tmp_path, "text", "second entry\n", 13)
    third = _source(tmp_path, "2", "third entry\n", 13)
    trigger = _source(tmp_path, "trigger.md", "event\n", 0)
    rules = (
        "--archive-auto-pair now.md past.md\n"
        "--archive-auto-pair text second.md text\n"
        "--archive-auto-pair 2 history 1 file\n"
    )

    assert _run_archive(tmp_path, monkeypatch, rules, target=trigger) == 0

    assert all(
        path.read_text(encoding="utf-8") == "" for path in (first, second, third)
    )
    assert (tmp_path / "past.md").read_text(encoding="utf-8").endswith("first entry\n")
    assert (
        (tmp_path / "second.md").read_text(encoding="utf-8").endswith("second entry\n")
    )
    archived = list((tmp_path / "history").iterdir())
    assert len(archived) == 1
    assert archived[0].read_text(encoding="utf-8") == "third entry\n"


@pytest.mark.parametrize("command", ["--archive", "--archive-pair text"])
def test_manual_archive_selects_current_note_from_multiple_pairs(
    tmp_path: Path, monkeypatch, command: str
) -> None:
    first = _source(tmp_path, "first.md", "leave this\n", 0)
    second = _source(tmp_path, "second.md", command + "\narchive this\n", 0)
    rules = (
        "--archive-auto-pair first.md first-past.md 10 text\n"
        "--archive-auto-pair second.md second-past.md 10 text\n"
    )

    assert _run_archive(tmp_path, monkeypatch, rules, target=second) == 0

    assert first.read_text(encoding="utf-8") == "leave this\n"
    assert not (tmp_path / "first-past.md").exists()
    assert second.read_text(encoding="utf-8") == ""
    assert (
        (tmp_path / "second-past.md")
        .read_text(encoding="utf-8")
        .endswith("archive this\n")
    )


@pytest.mark.parametrize("matching_rules", [0, 2])
def test_manual_archive_requires_unambiguous_pair(
    tmp_path: Path, monkeypatch, caplog, matching_rules: int
) -> None:
    original = "--archive\nkeep this\n"
    target = _source(tmp_path, "note.md", original, 0)
    other = _source(tmp_path, "other.md", "keep other\n", 13)
    source_name = target.name if matching_rules else other.name
    rules = (
        f"--archive-auto-pair {source_name} first-past.md\n"
        f"--archive-auto-pair {source_name} second-past.md\n"
    )

    assert _run_archive(tmp_path, monkeypatch, rules, target=target) == 0

    assert target.read_text(encoding="utf-8") == original
    assert other.read_text(encoding="utf-8") == "keep other\n"
    assert not (tmp_path / "first-past.md").exists()
    assert not (tmp_path / "second-past.md").exists()
    reason = "multiple_matching_pairs" if matching_rules else "no_matching_pair"
    assert f"reason={reason}" in caplog.text


def test_invalid_pair_does_not_discard_other_configured_pairs(
    tmp_path: Path, monkeypatch, caplog
) -> None:
    first = _source(tmp_path, "first.md", "leave invalid\n", 13)
    second = _source(tmp_path, "second.md", "archive valid\n", 13)
    trigger = _source(tmp_path, "trigger.md", "event\n", 0)
    rules = (
        "--archive-auto-pair first.md first-past.md zip\n"
        "--archive-auto-pair second.md second-past.md 10 text\n"
    )

    assert _run_archive(tmp_path, monkeypatch, rules, target=trigger) == 0

    assert first.read_text(encoding="utf-8") == "leave invalid\n"
    assert not (tmp_path / "first-past.md").exists()
    assert second.read_text(encoding="utf-8") == ""
    assert (
        (tmp_path / "second-past.md")
        .read_text(encoding="utf-8")
        .endswith("archive valid\n")
    )
    assert "reason=invalid_trailing_token" in caplog.text


@pytest.mark.parametrize("override_source", ["cli", "note"])
def test_explicit_pair_overrides_configured_pairs(
    tmp_path: Path, monkeypatch, override_source: str
) -> None:
    first = _source(tmp_path, "first.md", "keep first\n", 13)
    second = _source(tmp_path, "second.md", "keep second\n", 13)
    third = _source(tmp_path, "third.md", "archive third\n", 13)
    rule = ["--archive-auto-pair", "third.md", "third-past.md", "2", "text"]
    trigger = _source(
        tmp_path,
        "trigger.md",
        " ".join(rule) + "\n" if override_source == "note" else "event\n",
        0,
    )
    rules = (
        "--archive-auto-pair first.md first-past.md\n"
        "--archive-auto-pair second.md second-past.md\n"
    )

    assert (
        _run_archive(
            tmp_path,
            monkeypatch,
            rules,
            target=trigger,
            cli=rule if override_source == "cli" else None,
        )
        == 0
    )

    assert first.read_text(encoding="utf-8") == "keep first\n"
    assert second.read_text(encoding="utf-8") == "keep second\n"
    assert not (tmp_path / "first-past.md").exists()
    assert not (tmp_path / "second-past.md").exists()
    assert third.read_text(encoding="utf-8") == ""
    assert (
        (tmp_path / "third-past.md")
        .read_text(encoding="utf-8")
        .endswith("archive third\n")
    )
