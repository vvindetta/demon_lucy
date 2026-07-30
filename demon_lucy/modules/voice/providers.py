from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Any

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.lib.path import abs_expand_path


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    provider: str
    model: str


class VoiceError(Exception):
    def __init__(self, message: str, *, reason: str = "failed"):
        super().__init__(message)
        self.reason = reason


_VOSK_MODEL_CACHE: dict[str, Any] = {}
_VOSK_MODEL_CACHE_LOCK = threading.Lock()
_AUDIO_CHUNK_BYTES = 4000
_RECORDER_SHUTDOWN_TIMEOUT_SECONDS = 2


def _clip_error(text: object, limit: int = 500) -> str:
    rendered = str(text).replace("\n", " ").strip()
    if len(rendered) <= limit:
        return rendered
    return rendered[:limit] + "...(clipped)"


def _get_vosk_model(model_path: str) -> Any:
    path = abs_expand_path(model_path)
    if not os.path.isdir(path):
        raise VoiceError(
            f"Vosk model directory does not exist: {path}",
            reason="missing_model_path",
        )

    try:
        from vosk import Model, SetLogLevel
    except ImportError as exc:
        raise VoiceError(
            "Python package 'vosk' is not installed.",
            reason="missing_dependency",
        ) from exc

    with _VOSK_MODEL_CACHE_LOCK:
        if path not in _VOSK_MODEL_CACHE:
            try:
                SetLogLevel(-1)
            except Exception:
                pass
            try:
                _VOSK_MODEL_CACHE[path] = Model(path)
            except Exception as exc:
                raise VoiceError(
                    f"Failed to load Vosk model: {_clip_error(exc)}",
                    reason="model_load_failed",
                ) from exc
        return _VOSK_MODEL_CACHE[path]


def _recorder_command(args: ParsedArgs) -> list[str]:
    recorder_path = args.require("voice-recorder-path").value
    sample_rate = max(1, args.require("voice-sample-rate").value)

    return [
        recorder_path,
        "-q",
        "-f",
        "S16_LE",
        "-r",
        str(sample_rate),
        "-c",
        "1",
        "-t",
        "raw",
    ]


def _read_process_error(process: subprocess.Popen[bytes]) -> str:
    try:
        raw = process.stderr.read() if process.stderr is not None else b""
    except OSError:
        raw = b""
    return _clip_error(raw.decode("utf-8", errors="replace") or process.returncode)


def _stop_recorder(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return

    process.terminate()
    try:
        process.wait(timeout=_RECORDER_SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _open_recorder(args: ParsedArgs) -> subprocess.Popen[bytes]:
    recorder_path = args.require("voice-recorder-path").value

    try:
        process = subprocess.Popen(
            _recorder_command(args),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise VoiceError(
            f"Recorder executable not found: {recorder_path}",
            reason="missing_dependency",
        ) from exc
    except OSError as exc:
        raise VoiceError(
            f"Voice recording failed: {_clip_error(exc)}",
            reason="record_failed",
        ) from exc

    if process.stdout is None:
        _stop_recorder(process)
        raise VoiceError(
            "Recorder stdout is unavailable.",
            reason="record_failed",
        )

    return process


def _decode_vosk_result(payload: str) -> str:
    parsed = json.loads(payload)
    return str(parsed.get("text") or "").strip()


def _recognize_stream(
    *,
    args: ParsedArgs,
    model: Any,
    model_path: str,
    sample_rate: int,
) -> TranscriptResult:
    try:
        from vosk import KaldiRecognizer
    except ImportError as exc:
        raise VoiceError(
            "Python package 'vosk' is not installed.",
            reason="missing_dependency",
        ) from exc

    timeout_seconds = max(1, args.require("voice-timeout-seconds").value)
    deadline = time.monotonic() + timeout_seconds
    chunks: list[str] = []
    recognizer = KaldiRecognizer(model, sample_rate)
    process = _open_recorder(args)

    try:
        while time.monotonic() < deadline:
            data = process.stdout.read(_AUDIO_CHUNK_BYTES)
            if not data:
                returncode = process.poll()
                if returncode is not None:
                    if returncode != 0:
                        raise VoiceError(
                            f"Voice recording failed: {_read_process_error(process)}",
                            reason="record_failed",
                        )
                    break
                continue

            if recognizer.AcceptWaveform(data):
                text = _decode_vosk_result(recognizer.Result())
                if text:
                    chunks.append(text)
                    break
    finally:
        _stop_recorder(process)

    final_text = _decode_vosk_result(recognizer.FinalResult())
    if final_text:
        chunks.append(final_text)

    return TranscriptResult(
        text=" ".join(chunks).strip(),
        provider="offline-vosk",
        model=abs_expand_path(model_path),
    )


def listen_once(args: ParsedArgs) -> TranscriptResult:
    model_path = args.require("voice-offline-vosk-model-path").value.strip()
    if not model_path:
        raise VoiceError(
            "Missing --voice-offline-vosk-model-path.",
            reason="missing_model_path",
        )

    sample_rate = max(1, args.require("voice-sample-rate").value)
    model = _get_vosk_model(model_path)

    try:
        return _recognize_stream(
            args=args,
            model=model,
            model_path=model_path,
            sample_rate=sample_rate,
        )
    except json.JSONDecodeError as exc:
        raise VoiceError(
            "Vosk returned invalid JSON.",
            reason="provider_invalid_response",
        ) from exc
