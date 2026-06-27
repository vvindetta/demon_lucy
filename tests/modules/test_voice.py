from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from watchdog.events import FileModifiedEvent

import demon_lucy.modules.voice as voice_mod
import demon_lucy.modules.voice.providers as voice_providers
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.module_manager import ModuleManager
from demon_lucy.modules.abstract_module import Context, System
from demon_lucy.modules.voice import Voice
from demon_lucy.modules.voice.providers import TranscriptResult, VoiceError, listen_once


def _config(args: list[str] | None = None) -> dict[str, object]:
    parsed, unknown = parse_args(args=args or [], template=Voice.template)
    assert unknown == []
    parsed.update(
        {
            "sys_notification_provider": "disable",
            "sys_notification_min_interval_seconds": 0.0,
            "sys_notification_error_backoff_base_seconds": 0.0,
            "sys_notification_error_backoff_max_seconds": 0.0,
            "sys_notification_error_burst_limit": 0,
            "sys_notification_error_burst_window_seconds": 0.0,
        }
    )
    return parsed


def _system(module: Voice, path: Path) -> System:
    return System(
        event=FileModifiedEvent(str(path)),
        global_template=Voice.template,
        modules=[module],
        event_id="evt-test",
    )


def test_voice_replaces_flag_inline(tmp_path: Path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("before\n--voice\n", encoding="utf-8")

    monkeypatch.setattr(
        voice_mod,
        "listen_once",
        lambda _config: TranscriptResult(
            text="privet mir",
            provider="offline-vosk",
            model="/models/ru",
        ),
    )

    module = Voice()
    ctx = Context(path=str(note), config=_config(), arg_lines={"voice": [2]})

    changed = module.modified(ctx, _system(module, note))

    assert changed == {str(note.resolve()): 1}
    assert note.read_text(encoding="utf-8") == "before\nprivet mir\n"


def test_voice_inline_works_through_module_manager(tmp_path: Path, monkeypatch):
    note = tmp_path / "note.md"
    note.write_text("--voice\n", encoding="utf-8")

    monkeypatch.setattr(
        voice_mod,
        "listen_once",
        lambda _config: TranscriptResult(
            text="hello",
            provider="offline-vosk",
            model="/models/ru",
        ),
    )

    manager = ModuleManager(
        modules=[Voice()],
        args=["--voice-offline-vosk-model-path", "/models/ru"],
        system_config={
            "sys_notification_provider": "disable",
            "sys_notification_min_interval_seconds": 0.0,
            "sys_ignore_paths": [],
        },
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
    ctx = Context(path=str(note), config=_config(), arg_lines={})

    assert module.modified(ctx, _system(module, note)) is None
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
        lambda _config: TranscriptResult(
            text="",
            provider="offline-vosk",
            model="/models/ru",
        ),
    )

    module = Voice()
    ctx = Context(path=str(note), config=_config(), arg_lines={"voice": [1]})

    assert module.modified(ctx, _system(module, note)) is None
    assert note.read_text(encoding="utf-8") == "--voice\n"


def test_voice_provider_requires_model_path():
    with pytest.raises(VoiceError, match="voice-offline-vosk-model-path"):
        listen_once(_config())


def test_voice_provider_streams_until_vosk_endpoint(monkeypatch):
    commands: list[list[str]] = []

    class FakeStdout:
        def __init__(self):
            self.reads = 0

        def read(self, _size: int) -> bytes:
            self.reads += 1
            if self.reads <= 2:
                return b"\0\0" * 2000
            return b""

    class FakeStderr:
        def read(self) -> bytes:
            return b""

    class FakeProcess:
        def __init__(self):
            self.stdout = FakeStdout()
            self.stderr = FakeStderr()
            self.returncode = None
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    process = FakeProcess()

    class FakeRecognizer:
        def __init__(self, _model, _sample_rate):
            self.calls = 0

        def AcceptWaveform(self, _data: bytes) -> bool:
            self.calls += 1
            return self.calls == 2

        def Result(self) -> str:
            return '{"text": "hello"}'

        def FinalResult(self) -> str:
            return '{"text": ""}'

    def fake_popen(command, stdout, stderr):
        commands.append(command)
        assert stdout == voice_providers.subprocess.PIPE
        assert stderr == voice_providers.subprocess.PIPE
        return process

    monkeypatch.setattr(voice_providers, "_get_vosk_model", lambda _path: object())
    monkeypatch.setattr(voice_providers.subprocess, "Popen", fake_popen)
    monkeypatch.setitem(
        sys.modules,
        "vosk",
        SimpleNamespace(KaldiRecognizer=FakeRecognizer),
    )

    result = listen_once(
        _config(
            [
                "--voice-offline-vosk-model-path",
                "/models/ru",
                "--voice-timeout-seconds",
                "5",
            ]
        )
    )

    assert result.text == "hello"
    assert process.terminated
    assert commands
    assert "-d" not in commands[0]
