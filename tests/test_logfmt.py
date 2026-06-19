from __future__ import annotations

from demon_lucy.lib.logfmt import ignore_summary, log_record


def test_log_record_uses_stable_pipe_separated_key_values() -> None:
    assert (
        log_record(
            "event.done",
            id="evt-000001",
            status="ok",
            changed_paths=2,
            changed_events=3,
            enabled=True,
            empty="",
            omitted=None,
        )
        == "event.done | id=evt-000001 | status=ok | changed_paths=2 | changed_events=3 | enabled=true | empty=-"
    )


def test_ignore_summary_counts_changed_paths_and_events() -> None:
    assert ignore_summary({"/a": 1, "/b": 3, "/c": 0}) == (2, 4)
    assert ignore_summary(None) == (0, 0)
