from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from unittest.mock import Mock

import pytest
from watchdog.events import FileModifiedEvent

import demon_lucy.modules.voice as voice_mod
from demon_lucy.lib.args.models import ArgSource, ParsedArgs
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.voice import Voice
from demon_lucy.modules.voice.errors import VoiceError
from demon_lucy.modules.voice.providers import TranscriptResult
from demon_lucy.runtime import DEMON_LUCY_STARTUP_TEMPLATE
from tests.args_support import result_changes

_TEMPLATE = [*DEMON_LUCY_STARTUP_TEMPLATE, *Voice.template]
_NOTIFICATION_TOKENS = [
    "--sys-notification-provider",
    "disable",
    "--sys-notification-min-interval-seconds",
    "0",
    "--sys-notification-error-backoff-base-seconds",
    "0",
    "--sys-notification-error-backoff-max-seconds",
    "0",
    "--sys-notification-error-burst-limit",
    "0",
    "--sys-notification-error-burst-window-seconds",
    "0",
]


def _args(
    tokens: list[str] | None = None,
    *,
    line: int | None = None,
) -> ParsedArgs:
    parsed = parse_args(args=_NOTIFICATION_TOKENS, template=_TEMPLATE)
    if not tokens:
        return parsed
    return parsed.merged_with(
        parse_args(
            args=tokens,
            template=_TEMPLATE,
            source=ArgSource.FILE if line is not None else ArgSource.CLI,
            include_defaults=False,
            line=line,
        )
    )


def _context(path: Path, args: ParsedArgs) -> Context:
    return Context(
        path=str(path),
        args=args,
        run_mode="daemon",
        event_id="evt-test",
        event=FileModifiedEvent(str(path)),
    )


def _system(module: Voice) -> System:
    return System(
        global_template=_TEMPLATE,
        modules=[module],
    )


def test_voice_replaces_flag_inline(tmp_path: Path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("before\n--voice\n", encoding="utf-8")

    monkeypatch.setattr(
        voice_mod,
        "listen_once",
        lambda _args: TranscriptResult(
            text="privet mir",
            provider="offline-vosk",
            model="/models/ru",
        ),
    )

    module = Voice()
    ctx = _context(note, _args(["--voice"], line=2))

    changed = module.modified(ctx, _system(module))

    assert result_changes(changed) == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == "before\nprivet mir\n"


def test_voice_inline_works_through_module_manager(tmp_path: Path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("--voice\n", encoding="utf-8")

    monkeypatch.setattr(
        voice_mod,
        "listen_once",
        lambda _args: TranscriptResult(
            text="hello",
            provider="offline-vosk",
            model="/models/ru",
        ),
    )

    manager = ModuleManager(
        modules=[Voice()],
        startup_args=parse_args(
            args=[
                *_NOTIFICATION_TOKENS,
                "--voice-offline-vosk-model-path",
                "/models/ru",
            ],
            template=DEMON_LUCY_STARTUP_TEMPLATE,
        ),
    )

    changed = manager.run(str(note), FileModifiedEvent(str(note)), event_id="evt-test")

    assert changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == "hello\n"


def test_voice_ignores_config_flags_without_inline_voice(tmp_path: Path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("--voice-offline-vosk-model-path /models/ru\n", encoding="utf-8")

    def fail_listen(*_args, **_kwargs):
        raise AssertionError("voice should not record")

    monkeypatch.setattr(voice_mod, "listen_once", fail_listen)

    module = Voice()
    ctx = _context(note, _args())

    assert module.modified(ctx, _system(module)) is None
    assert (
        note.read_text(encoding="utf-8")
        == "--voice-offline-vosk-model-path /models/ru\n"
    )


def test_voice_empty_transcript_keeps_flag(tmp_path: Path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("--voice\n", encoding="utf-8")

    monkeypatch.setattr(
        voice_mod,
        "listen_once",
        lambda _args: TranscriptResult(
            text="",
            provider="offline-vosk",
            model="/models/ru",
        ),
    )

    module = Voice()
    ctx = _context(note, _args(["--voice"], line=1))

    assert module.modified(ctx, _system(module)) is None
    assert note.read_text(encoding="utf-8") == "--voice\n"


@pytest.mark.parametrize("ending", ["\n", "\r\n", "\r", ""])
def test_voice_preserves_line_endings_and_mode(tmp_path, monkeypatch, ending):
    note = tmp_path / "note.md"
    prefix = "before\u2028still the first line\r\n"
    note.write_bytes((prefix + "--voice" + ending).encode())
    note.chmod(0o640)
    monkeypatch.setattr(
        voice_mod,
        "listen_once",
        lambda _: TranscriptResult(" hello \n world ", "offline-vosk", "/model"),
    )
    module = Voice()

    result = module.created(_context(note, _args(["--voice"], line=2)), _system(module))

    assert result_changes(result) == {str(note): 1}
    assert note.read_bytes() == (prefix + "hello world" + ending).encode()
    assert note.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("source", [ArgSource.CONFIG, ArgSource.CLI])
def test_voice_requires_a_note_command(tmp_path, monkeypatch, source):
    note = tmp_path / "note.md"
    note.write_text("--voice\n")
    listen = Mock()
    monkeypatch.setattr(voice_mod, "listen_once", listen)
    args = _args().merged_with(
        parse_args(
            args=["--voice"],
            template=_TEMPLATE,
            source=source,
            include_defaults=False,
            line=1,
        )
    )
    module = Voice()

    assert module.modified(_context(note, args), _system(module)) is None
    listen.assert_not_called()


@pytest.mark.parametrize(
    "text", ["my changed note\n", "# --voice\n", "a quoted --voice\n"]
)
def test_voice_does_not_record_for_stale_command_lines(tmp_path, monkeypatch, text):
    note = tmp_path / "note.md"
    note.write_text(text)
    listen = Mock()
    monkeypatch.setattr(voice_mod, "listen_once", listen)
    module = Voice()

    assert (
        module.modified(_context(note, _args(["--voice"], line=1)), _system(module))
        is None
    )
    listen.assert_not_called()
    assert note.read_text() == text


@pytest.mark.parametrize("edit", ["change", "delete"])
def test_voice_preserves_edits_during_recording(tmp_path, monkeypatch, caplog, edit):
    note = tmp_path / "note.md"
    note.write_text("--voice\n")

    def listen(_):
        if edit == "change":
            note.write_text("user edits\n")
        else:
            note.unlink()
        return TranscriptResult("hello", "offline-vosk", "/model")

    notify = Mock()
    monkeypatch.setattr(voice_mod, "listen_once", listen)
    monkeypatch.setattr(voice_mod, "safe_notify", notify)
    module = Voice()
    caplog.set_level("INFO")

    assert (
        module.modified(_context(note, _args(["--voice"], line=1)), _system(module))
        is None
    )

    if edit == "change":
        assert note.read_text() == "user edits\n"
    else:
        assert not note.exists()
    assert "source_changed_during_recording" in caplog.text
    assert "voice.inline_transcribed" not in caplog.text
    assert "id=evt-test" in caplog.text
    assert notify.call_args.args[0] == f"voice:{note}"
    assert notify.call_count == 1
    assert not list(tmp_path.glob(".*.tmp"))


def test_voice_reports_write_failure_without_losing_note(tmp_path, monkeypatch, caplog):
    note = tmp_path / "note.md"
    note.write_text("--voice\n")
    monkeypatch.setattr(
        voice_mod,
        "listen_once",
        lambda _: TranscriptResult("hello", "offline-vosk", "/model"),
    )
    monkeypatch.setattr(
        "demon_lucy.lib.text_file.os.replace",
        Mock(side_effect=OSError("disk failed")),
    )
    notify = Mock()
    monkeypatch.setattr(voice_mod, "safe_notify", notify)
    caplog.set_level("INFO")
    module = Voice()

    assert (
        module.modified(_context(note, _args(["--voice"], line=1)), _system(module))
        is None
    )

    assert note.read_text() == "--voice\n"
    assert "reason=source_write_failed" in caplog.text
    assert "voice.inline_transcribed" not in caplog.text
    assert notify.call_count == 1
    assert list(tmp_path.iterdir()) == [note]


def test_voice_stops_after_a_provider_failure(tmp_path, monkeypatch, caplog):
    note = tmp_path / "note.md"
    note.write_text("--voice\n--voice\n")
    args = _args(["--voice"], line=1)
    args = args.merged_with(
        ParsedArgs(known=(replace(args.require("voice"), lines=(1, 2)),))
    )
    listen = Mock(side_effect=VoiceError("no model", reason="missing_model_path"))
    notify = Mock()
    monkeypatch.setattr(voice_mod, "listen_once", listen)
    monkeypatch.setattr(voice_mod, "safe_notify", notify)
    module = Voice()

    assert module.modified(_context(note, args), _system(module)) is None

    assert listen.call_count == notify.call_count == 1
    assert note.read_text() == "--voice\n--voice\n"
    assert "reason=missing_model_path" in caplog.text


@pytest.mark.parametrize("provider", ["openai", "groq"])
@pytest.mark.parametrize("succeeds", [True, False])
def test_online_transcription_through_module_manager(
    tmp_path, monkeypatch, caplog, provider, succeeds
):
    from urllib.error import URLError
    import demon_lucy.modules.voice.online as online

    note = tmp_path / "note.md"
    original = f"--voice-provider {provider}\n--voice\n"
    note.write_text(original)
    monkeypatch.setenv(f"{provider.upper()}_API_KEY", "test-private-key")
    monkeypatch.setattr(online, "capture_wav", lambda _: b"fake wav")
    send = Mock(return_value={"text": "привет мир"})
    if not succeeds:
        send.side_effect = URLError("test-private-key")
    monkeypatch.setattr(online, "post_multipart_json", send)
    manager = ModuleManager(
        modules=[Voice()],
        startup_args=parse_args(
            args=_NOTIFICATION_TOKENS, template=DEMON_LUCY_STARTUP_TEMPLATE
        ),
    )

    changes = manager.run(
        str(note), FileModifiedEvent(str(note)), event_id="evt-online"
    )

    if succeeds:
        assert changes == {str(note): 1}
        assert note.read_text() == f"--voice-provider {provider}\nпривет мир\n"
    else:
        assert not changes
        assert note.read_text() == original
        assert "provider_unavailable" in caplog.text
    assert "test-private-key" not in caplog.text
