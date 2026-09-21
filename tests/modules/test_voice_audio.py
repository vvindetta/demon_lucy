from __future__ import annotations

import io
import struct
import wave
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

import demon_lucy.modules.voice.audio as audio
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.voice.config import TEMPLATE, VoiceConfig
from demon_lucy.modules.voice.errors import VoiceError


def _config(*tokens):
    return VoiceConfig.from_args(
        parse_args(
            args=[
                "--voice-provider",
                "openai",
                "--voice-silence-seconds",
                "0.1",
                *tokens,
            ],
            template=TEMPLATE,
        )
    )


def _pcm(value, samples):
    return struct.pack("<h", value) * samples


@pytest.fixture
def stream(monkeypatch):
    state = SimpleNamespace(chunks=[], closed=False, config=None)

    @contextmanager
    def record(config):
        state.config = config
        try:
            yield iter(state.chunks)
        finally:
            state.closed = True

    monkeypatch.setattr(audio, "record_audio", record)
    return state


@pytest.mark.parametrize("chunk_size", [2, 160, 4000, 20000])
def test_capture_stops_after_silence_independent_of_read_sizes(stream, chunk_size):
    speech = _pcm(-2000, 3200)
    silence = _pcm(0, 1600)
    later_speech = _pcm(3000, 3200)
    raw = speech + silence + later_speech
    stream.chunks = [
        raw[offset : offset + chunk_size] for offset in range(0, len(raw), chunk_size)
    ]

    result = audio.capture_wav(_config())

    with wave.open(io.BytesIO(result), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
            1,
            2,
            16000,
        )
        assert wav.readframes(wav.getnframes()) == speech + silence
    assert stream.closed


def test_initial_silence_waits_for_speech_and_brief_pause_does_not_end_it(stream):
    raw = _pcm(0, 3200) + _pcm(3000, 640) + _pcm(0, 320) + _pcm(3000, 640)
    stream.chunks = [raw]
    result = audio.capture_wav(_config())
    with wave.open(io.BytesIO(result), "rb") as wav:
        assert wav.readframes(wav.getnframes()) == raw
    assert stream.closed


@pytest.mark.parametrize("value", [0, 100, -100])
def test_silence_or_low_noise_does_not_produce_an_upload(stream, value):
    stream.chunks = [_pcm(value, 3200)]
    assert audio.capture_wav(_config()) == b""
    assert stream.closed


def test_threshold_can_be_lowered_for_quiet_speech(stream):
    stream.chunks = [_pcm(100, 100)]
    result = audio.capture_wav(_config("--voice-silence-threshold", "50"))
    with wave.open(io.BytesIO(result), "rb") as wav:
        assert wav.getnframes() == 100


def test_empty_recorder_returns_no_upload(stream):
    assert audio.capture_wav(_config()) == b""
    assert stream.closed


def test_audio_size_limit_closes_recorder(stream, monkeypatch):
    monkeypatch.setattr(audio, "_MAX_AUDIO_BYTES", 100)
    stream.chunks = [_pcm(2000, 100)]
    with pytest.raises(VoiceError) as error:
        audio.capture_wav(_config())
    assert error.value.reason == "audio_too_large"
    assert stream.closed


@pytest.mark.parametrize(
    ("provider", "rate", "seconds", "pcm_bytes"),
    [
        ("google", 16000, 60, 16000 * 60 * 2),
        ("yandex", 8000, 30, 8000 * 30 * 2),
        ("yandex", 16000, 30, 16000 * 30 * 2),
        ("yandex", 48000, 30, 1_000_000),
    ],
)
def test_cloud_capture_obeys_duration_and_byte_limits(
    stream, provider, rate, seconds, pcm_bytes
):
    # One second past the allowed duration, supplied faster than real-time recording.
    stream.chunks = [_pcm(2000, rate * (seconds + 1))]
    config = _config(
        "--voice-provider",
        provider,
        "--voice-language",
        "ru-RU",
        "--voice-timeout-seconds",
        "120",
        "--voice-sample-rate",
        str(rate),
    )

    result = audio.capture_wav(config)

    with wave.open(io.BytesIO(result), "rb") as wav:
        assert wav.getnframes() * 2 == pcm_bytes
    assert stream.config.timeout_seconds == seconds
    assert stream.closed


def test_shorter_user_recording_limit_is_preserved(stream):
    stream.chunks = [_pcm(2000, 16000 * 3)]
    config = _config(
        "--voice-provider",
        "yandex",
        "--voice-language",
        "ru-RU",
        "--voice-timeout-seconds",
        "2",
    )
    result = audio.capture_wav(config)
    with wave.open(io.BytesIO(result), "rb") as wav:
        assert wav.getnframes() == 32000
    assert stream.config.timeout_seconds == 2
    assert stream.closed
