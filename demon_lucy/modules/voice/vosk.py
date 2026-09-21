from __future__ import annotations

import json
import os
import threading
from typing import Any

from demon_lucy.modules.voice.config import VoiceConfig
from demon_lucy.modules.voice.errors import VoiceError
from demon_lucy.modules.voice.recorder import record_audio


_VOSK_MODEL_CACHE: dict[str, Any] = {}
_VOSK_MODEL_CACHE_LOCK = threading.Lock()


def _get_vosk_model(path: str) -> Any:
    if not os.path.isdir(path):
        raise VoiceError(
            f"Vosk model directory does not exist: {path}", reason="missing_model_path"
        )
    try:
        from vosk import Model, SetLogLevel
    except ImportError as exc:
        raise VoiceError(
            "Python package 'vosk' is not installed.", reason="missing_dependency"
        ) from exc

    with _VOSK_MODEL_CACHE_LOCK:
        if path not in _VOSK_MODEL_CACHE:
            try:
                SetLogLevel(-1)
                _VOSK_MODEL_CACHE[path] = Model(path)
            except Exception as exc:
                raise VoiceError(
                    f"Failed to load Vosk model: {exc}", reason="model_load_failed"
                ) from exc
        return _VOSK_MODEL_CACHE[path]


def _decode_vosk_result(payload: str) -> str:
    try:
        parsed = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise VoiceError(
            "Vosk returned invalid JSON.", reason="provider_invalid_response"
        ) from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("text"), str):
        raise VoiceError(
            "Vosk returned a result without a text string.",
            reason="provider_invalid_response",
        )
    return parsed["text"].strip()


def transcribe(config: VoiceConfig) -> str:
    model = _get_vosk_model(config.model_path)
    try:
        from vosk import KaldiRecognizer
    except ImportError as exc:
        raise VoiceError(
            "Python package 'vosk' is not installed.", reason="missing_dependency"
        ) from exc

    try:
        recognizer = KaldiRecognizer(model, config.sample_rate)
        with record_audio(config) as frames:
            for data in frames:
                if recognizer.AcceptWaveform(data):
                    text = _decode_vosk_result(recognizer.Result())
                    if text:
                        break
            else:
                text = _decode_vosk_result(recognizer.FinalResult())
    except VoiceError:
        raise
    except Exception as exc:
        raise VoiceError(
            f"Vosk transcription failed: {exc}", reason="provider_failed"
        ) from exc

    return text
