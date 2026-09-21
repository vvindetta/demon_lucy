from __future__ import annotations

import sys
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import demon_lucy.modules.voice.providers as providers
import demon_lucy.modules.voice.vosk as vosk
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.voice import Voice
from demon_lucy.modules.voice.config import VoiceConfig
from demon_lucy.modules.voice.errors import VoiceError


def _args(*tokens):
    return parse_args(
        args=["--voice-offline-vosk-model-path", "/models/ru", *tokens],
        template=Voice.template,
    )


@pytest.mark.parametrize(
    ("tokens", "reason"),
    [
        (["--voice-offline-vosk-model-path", ""], "missing_model_path"),
        (["--voice-recorder-path", ""], "invalid_config"),
        (["--voice-timeout-seconds", "0"], "invalid_config"),
        (["--voice-timeout-seconds", "-1"], "invalid_config"),
        (["--voice-sample-rate", "0"], "invalid_config"),
        (["--voice-sample-rate", "-16000"], "invalid_config"),
    ],
)
def test_invalid_config_fails_before_loading_model(monkeypatch, tokens, reason):
    load = Mock()
    monkeypatch.setattr(vosk, "_get_vosk_model", load)

    with pytest.raises(VoiceError) as error:
        providers.listen_once(_args(*tokens))

    assert error.value.reason == reason
    load.assert_not_called()


def test_config_expands_recorder_and_model_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    config = VoiceConfig.from_args(
        _args(
            "--voice-recorder-path",
            "./my recorder",
            "--voice-offline-vosk-model-path",
            "./model",
        )
    )
    assert config.recorder_path == str(tmp_path / "my recorder")
    assert config.model_path == str(tmp_path / "model")
    assert VoiceConfig.from_args(_args()).recorder_path == "arecord"


@pytest.fixture
def recognition(monkeypatch):
    recognizer = Mock()
    recognizer.AcceptWaveform.return_value = False
    recognizer.FinalResult.return_value = '{"text": "final words"}'
    model = object()
    factory = Mock(return_value=recognizer)
    state = SimpleNamespace(
        recognizer=recognizer,
        factory=factory,
        model=model,
        audio=[b"\0\0", b"\1\1"],
        closed=False,
        config=None,
    )

    @contextmanager
    def record(config):
        state.config = config
        try:
            yield iter(state.audio)
        finally:
            state.closed = True

    monkeypatch.setattr(vosk, "_get_vosk_model", lambda _: model)
    monkeypatch.setitem(sys.modules, "vosk", SimpleNamespace(KaldiRecognizer=factory))
    monkeypatch.setattr(vosk, "record_audio", record)
    return state


def test_endpoint_stops_without_appending_final_text(recognition):
    recognition.recognizer.AcceptWaveform.return_value = True
    recognition.recognizer.Result.return_value = '{"text": "hello"}'

    result = providers.listen_once(_args())

    assert result.text == "hello"
    assert result.provider == "offline-vosk"
    assert result.model == "/models/ru"
    assert recognition.recognizer.AcceptWaveform.call_count == 1
    recognition.recognizer.FinalResult.assert_not_called()
    assert recognition.closed


def test_empty_endpoint_keeps_listening(recognition):
    recognition.recognizer.AcceptWaveform.side_effect = [True, True]
    recognition.recognizer.Result.side_effect = ['{"text": ""}', '{"text": "hello"}']

    assert providers.listen_once(_args()).text == "hello"
    assert recognition.recognizer.AcceptWaveform.call_count == 2
    assert recognition.closed


def test_eof_or_deadline_uses_final_result(recognition):
    result = providers.listen_once(_args("--voice-sample-rate", "8000"))

    assert result.text == "final words"
    recognition.factory.assert_called_once_with(recognition.model, 8000)
    assert recognition.config.sample_rate == 8000
    recognition.recognizer.FinalResult.assert_called_once()
    assert recognition.closed


@pytest.mark.parametrize("payload", ["not json", "[]", "null", "{}", '{"text": 123}'])
@pytest.mark.parametrize("phase", ["Result", "FinalResult"])
def test_invalid_provider_results_are_reported_and_close_recorder(
    recognition, payload, phase
):
    recognition.recognizer.AcceptWaveform.return_value = phase == "Result"
    getattr(recognition.recognizer, phase).return_value = payload

    with pytest.raises(VoiceError) as error:
        providers.listen_once(_args())

    assert error.value.reason == "provider_invalid_response"
    assert recognition.closed


@pytest.mark.parametrize("phase", ["init", "AcceptWaveform", "Result", "FinalResult"])
def test_provider_errors_are_reported(recognition, phase):
    if phase == "init":
        recognition.factory.side_effect = RuntimeError("Vosk failed")
    else:
        recognition.recognizer.AcceptWaveform.return_value = phase == "Result"
        getattr(recognition.recognizer, phase).side_effect = RuntimeError("Vosk failed")

    with pytest.raises(VoiceError, match="Vosk failed") as error:
        providers.listen_once(_args())

    assert error.value.reason == "provider_failed"
    assert recognition.closed is (phase != "init")


def test_models_are_cached_by_normalized_path(tmp_path, monkeypatch):
    model = object()
    load = Mock(return_value=model)
    monkeypatch.setitem(
        sys.modules, "vosk", SimpleNamespace(Model=load, SetLogLevel=Mock())
    )
    monkeypatch.setattr(vosk, "_VOSK_MODEL_CACHE", {})
    first = VoiceConfig.from_args(
        _args("--voice-offline-vosk-model-path", str(tmp_path))
    )
    second = VoiceConfig.from_args(
        _args("--voice-offline-vosk-model-path", str(tmp_path / "."))
    )

    assert vosk._get_vosk_model(first.model_path) is model
    assert vosk._get_vosk_model(second.model_path) is model
    load.assert_called_once_with(str(tmp_path))


def test_missing_vosk_is_an_optional_dependency_error(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "vosk", None)

    with pytest.raises(VoiceError) as error:
        providers.listen_once(_args("--voice-offline-vosk-model-path", str(tmp_path)))

    assert error.value.reason == "missing_dependency"


def test_model_load_error_is_reported(tmp_path, monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "vosk",
        SimpleNamespace(
            Model=Mock(side_effect=RuntimeError("broken model")),
            SetLogLevel=Mock(),
        ),
    )
    monkeypatch.setattr(vosk, "_VOSK_MODEL_CACHE", {})

    with pytest.raises(VoiceError, match="broken model") as error:
        providers.listen_once(_args("--voice-offline-vosk-model-path", str(tmp_path)))

    assert error.value.reason == "model_load_failed"
    assert vosk._VOSK_MODEL_CACHE == {}


def test_missing_model_directory_fails_without_recorder(tmp_path):
    with pytest.raises(VoiceError) as error:
        providers.listen_once(
            _args("--voice-offline-vosk-model-path", str(tmp_path / "missing"))
        )
    assert error.value.reason == "missing_model_path"
