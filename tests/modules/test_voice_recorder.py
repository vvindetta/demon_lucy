from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from unittest.mock import Mock

import pytest

import demon_lucy.modules.voice.recorder as recorder
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.voice.config import TEMPLATE, VoiceConfig
from demon_lucy.modules.voice.errors import VoiceError


@pytest.fixture
def config():
    return VoiceConfig.from_args(
        parse_args(
            args=[
                "--voice-offline-vosk-model-path",
                "/models/ru",
                "--voice-recorder-path",
                "test-recorder",
                "--voice-timeout-seconds",
                "1",
            ],
            template=TEMPLATE,
        )
    )


@pytest.fixture
def child(monkeypatch):
    popen = subprocess.Popen
    processes = []

    def install(script):
        def start(command, **kwargs):
            process = popen([sys.executable, "-c", script], **kwargs)
            processes.append(process)
            return process

        spawn = Mock(side_effect=start)
        monkeypatch.setattr(recorder.subprocess, "Popen", spawn)
        return spawn

    yield install

    # Ensure tests never leave child processes behind, even if an assertion fails.
    for process in processes:
        alive = process.poll() is None
        if alive:
            process.kill()
            process.wait(timeout=2)
        assert not alive, "recorder was not reaped"
        assert process.stdout.closed
        assert process.stderr.closed
    assert not any(t.name.startswith("lucy-voice-") for t in threading.enumerate())


def test_clean_eof_and_recorder_arguments(child, config):
    spawn = child("import os; os.write(1, b'\\x01\\x02' * 3000)")
    with recorder.record_audio(config) as frames:
        data = b"".join(frames)

    assert data == b"\x01\x02" * 3000
    assert spawn.call_args.args[0] == [
        "test-recorder",
        "-q",
        "-f",
        "S16_LE",
        "-r",
        "16000",
        "-c",
        "1",
        "-t",
        "raw",
    ]


@pytest.mark.parametrize(
    "script",
    [
        "import time; time.sleep(30)",
        "import os,time; os.close(1); time.sleep(30)",
    ],
)
def test_deadline_stops_recorder_even_without_audio(child, config, script):
    child(script)
    start = time.monotonic()
    with recorder.record_audio(config) as frames:
        assert list(frames) == []
    assert time.monotonic() - start < 3


def test_full_stderr_pipe_does_not_block_audio(child, config):
    child("import os; os.write(2, b'x' * 300000); os.write(1, b'\\0\\0')")
    with recorder.record_audio(config) as frames:
        assert b"".join(frames) == b"\0\0"


def test_recorder_failure_reports_bounded_diagnostic(child, config):
    child(
        "import os; os.write(2, b'x' * 300000 + b' microphone unavailable\\n'); os._exit(13)"
    )
    with pytest.raises(VoiceError, match="microphone unavailable") as error:
        with recorder.record_audio(config) as frames:
            list(frames)
    assert error.value.reason == "record_failed"
    assert len(str(error.value)) < 550


@pytest.mark.skipif(os.name == "nt", reason="POSIX signal behavior")
def test_endpoint_cleanup_kills_recorder_that_ignores_termination(
    child, config, monkeypatch
):
    monkeypatch.setattr(recorder, "_SHUTDOWN_TIMEOUT_SECONDS", 0.1)
    child(
        "import os,signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "os.write(1, b'\\0\\0'); time.sleep(30)"
    )
    with recorder.record_audio(config) as frames:
        assert next(frames) == b"\0\0"


def test_early_exit_cleans_up_when_audio_queue_is_full(child, config):
    child("import os\nwhile True: os.write(1, b'\\0\\0' * 2000)")
    with recorder.record_audio(config) as frames:
        assert next(frames)
        time.sleep(0.05)


def test_consumer_failure_still_cleans_up(child, config):
    child("import os,time; os.write(1, b'\\0\\0'); time.sleep(30)")
    with pytest.raises(RuntimeError, match="recognition failed"):
        with recorder.record_audio(config) as frames:
            next(frames)
            raise RuntimeError("recognition failed")


def test_partial_pcm_samples_are_joined(child, config):
    child(
        "import os,time; os.write(1, b'a'); time.sleep(.05); "
        "os.write(1, b'bc'); time.sleep(.05); os.write(1, b'd')"
    )
    with recorder.record_audio(config) as frames:
        chunks = list(frames)
    assert b"".join(chunks) == b"abcd"
    assert all(len(chunk) % 2 == 0 for chunk in chunks)


def test_incomplete_pcm_is_reported(child, config):
    child("import os; os.write(1, b'a')")
    with pytest.raises(VoiceError, match="incomplete PCM16") as error:
        with recorder.record_audio(config) as frames:
            list(frames)
    assert error.value.reason == "record_failed"


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        (FileNotFoundError("missing"), "missing_dependency"),
        (PermissionError("permission denied"), "record_failed"),
    ],
)
def test_recorder_start_errors(config, monkeypatch, failure, reason):
    monkeypatch.setattr(recorder.subprocess, "Popen", Mock(side_effect=failure))
    with pytest.raises(VoiceError) as error:
        with recorder.record_audio(config):
            pytest.fail("should not open recorder")
    assert error.value.reason == reason
