from __future__ import annotations

import base64
import io
import json
import os
import wave
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode

from demon_lucy.modules.voice.audio import capture_wav
from demon_lucy.modules.voice.config import VoiceConfig, VoiceProvider
from demon_lucy.modules.voice.errors import VoiceError
from demon_lucy.modules.voice.http import (
    MultipartFile,
    post_json_response,
    post_multipart_json,
)


_ENDPOINTS = {
    VoiceProvider.OPENAI: (
        "https://api.openai.com/v1/audio/transcriptions",
        "OPENAI_API_KEY",
    ),
    VoiceProvider.GROQ: (
        "https://api.groq.com/openai/v1/audio/transcriptions",
        "GROQ_API_KEY",
    ),
    VoiceProvider.GOOGLE: (
        "https://speech.googleapis.com/v1/speech:recognize",
        "GOOGLE_ACCESS_TOKEN",
    ),
    VoiceProvider.YANDEX: (
        "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize",
        "YANDEX_API_KEY",
    ),
}


def _auth_headers(provider: VoiceProvider, variable: str) -> dict[str, str]:
    credential = os.environ.get(variable, "").strip()
    if not credential:
        raise VoiceError(
            f"Set {variable} in Lucy's environment before using {provider}.",
            reason="missing_access_token"
            if provider is VoiceProvider.GOOGLE
            else "missing_api_key",
        )
    if (
        not credential.isascii()
        or not credential.isprintable()
        or any(character.isspace() for character in credential)
    ):
        raise VoiceError(
            f"{variable} must contain only ASCII characters without whitespace.",
            reason="invalid_api_key",
        )
    scheme = "Api-Key" if provider is VoiceProvider.YANDEX else "Bearer"
    headers = {"Authorization": f"{scheme} {credential}", "Accept": "application/json"}
    if provider is VoiceProvider.GOOGLE:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        if project:
            if not project.isascii() or not all(
                c.isalnum() or c in "-:." for c in project
            ):
                raise VoiceError(
                    "GOOGLE_CLOUD_PROJECT is invalid.", reason="invalid_config"
                )
            headers["x-goog-user-project"] = project
    return headers


def _request_transcript(
    config: VoiceConfig, endpoint: str, audio: bytes, headers: dict[str, str]
) -> object:
    assert config.online is not None
    if config.provider in (VoiceProvider.OPENAI, VoiceProvider.GROQ):
        fields = {"model": config.online.model, "response_format": "json"}
        if config.online.language:
            fields["language"] = config.online.language
        return post_multipart_json(
            endpoint,
            fields=fields,
            file=MultipartFile("file", "voice.wav", "audio/wav", audio),
            headers=headers,
            timeout_seconds=config.online.request_timeout_seconds,
            max_response_bytes=1_000_000,
        )
    if config.provider is VoiceProvider.GOOGLE:
        payload = {
            "config": {
                "encoding": "LINEAR16",
                "sampleRateHertz": config.sample_rate,
                "languageCode": config.online.language,
                "model": config.online.model,
            },
            "audio": {"content": base64.b64encode(audio).decode("ascii")},
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    else:
        # SpeechKit v1 requires raw LPCM, not a WAV file or multipart upload.
        try:
            with wave.open(io.BytesIO(audio), "rb") as wav:
                if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (
                    1,
                    2,
                    config.sample_rate,
                ):
                    raise wave.Error("Unexpected recording format")
                data = wav.readframes(wav.getnframes())
        except (wave.Error, EOFError):
            raise VoiceError(
                "Recorded WAV audio is invalid.", reason="record_failed"
            ) from None
        endpoint += "?" + urlencode(
            {
                "lang": config.online.language,
                "topic": config.online.model,
                "format": "lpcm",
                "sampleRateHertz": config.sample_rate,
            }
        )
        headers = {**headers, "Content-Type": "application/octet-stream"}
    return post_json_response(
        endpoint,
        data=data,
        headers=headers,
        timeout_seconds=config.online.request_timeout_seconds,
        max_response_bytes=1_000_000,
    )


def _transcript_text(result: object, provider: VoiceProvider) -> str:
    if not isinstance(result, dict) or "error" in result:
        raise ValueError("Invalid response")
    if provider is VoiceProvider.GOOGLE:
        segments = result.get("results", [])
        if not isinstance(segments, list):
            raise ValueError("Invalid results")
        parts: list[str] = []
        for segment in segments:
            if not isinstance(segment, dict):
                raise ValueError("Invalid result")
            alternatives = segment.get("alternatives")
            if not isinstance(alternatives, list) or not alternatives:
                raise ValueError("Missing alternatives")
            first = alternatives[0]
            if not isinstance(first, dict) or not isinstance(
                first.get("transcript"), str
            ):
                raise ValueError("Missing transcript")
            parts.append(first["transcript"])
        return "".join(parts).strip()
    text = result.get("result" if provider is VoiceProvider.YANDEX else "text")
    if not isinstance(text, str):
        raise ValueError("Missing transcript")
    return text.strip()


def transcribe(config: VoiceConfig) -> str:
    assert config.online is not None
    endpoint, credential_variable = _ENDPOINTS[config.provider]
    headers = _auth_headers(config.provider, credential_variable)
    audio = capture_wav(config)
    if not audio:
        return ""
    try:
        result = _request_transcript(config, endpoint, audio, headers)
        return _transcript_text(result, config.provider)
    except HTTPError as exc:
        status = exc.code
        exc.close()
        if status in (401, 403):
            hint = f"Check {credential_variable} and account access."
        elif status == 429:
            hint = "Check the provider's quota or rate limits, then retry --voice."
        else:
            hint = "Check the transcription model and provider availability."
        # Error bodies can echo credentials or audio content; do not log them.
        raise VoiceError(
            f"{config.provider} transcription failed (HTTP {status}). {hint}",
            reason="provider_http_error",
        ) from None
    except (TimeoutError, URLError, OSError, HTTPException):
        raise VoiceError(
            f"Cannot reach {config.provider} or the request timed out. Retry --voice when connected.",
            reason="provider_unavailable",
        ) from None
    except (ValueError, UnicodeError):
        raise VoiceError(
            f"{config.provider} returned an invalid transcription response.",
            reason="provider_invalid_response",
        ) from None
