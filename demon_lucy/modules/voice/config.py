from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from enum import StrEnum

from demon_lucy.lib.args.models import KnownArg, ParsedArgs, Template
from demon_lucy.lib.path import abs_expand_path
from demon_lucy.modules.voice.errors import VoiceError


class VoiceProvider(StrEnum):
    OFFLINE_VOSK = "offline-vosk"
    OPENAI = "openai"
    GROQ = "groq"
    GOOGLE = "google"
    YANDEX = "yandex"


TEMPLATE: Template = [
    KnownArg(
        name="voice",
        value_type=bool,
        default=False,
        description="Record speech and write the transcript on this line using --voice-provider.",
    ),
    KnownArg(
        name="voice-provider",
        value_type=VoiceProvider,
        default=VoiceProvider.OFFLINE_VOSK,
        description="Speech provider: offline-vosk, openai, groq, google, or yandex. Online providers upload microphone audio.",
    ),
    KnownArg(
        name="voice-offline-vosk-model-path",
        value_type=str,
        default="",
        description="Path to a local Vosk model directory. Requires the optional vosk package.",
    ),
    KnownArg(
        name="voice-timeout-seconds",
        value_type=int,
        default=60,
        description="Maximum recording time in seconds (positive), subject to provider limits. Stops earlier after speech ends.",
    ),
    KnownArg(
        name="voice-recorder-path",
        value_type=str,
        default="arecord",
        description="Executable with arecord-compatible arguments for raw mono PCM16 audio.",
    ),
    KnownArg(
        name="voice-sample-rate",
        value_type=int,
        default=16000,
        description="Recording sample rate in Hz (positive).",
    ),
    KnownArg(
        name="voice-openai-model",
        value_type=str,
        default="gpt-transcribe",
        description="OpenAI transcription model. Requires OPENAI_API_KEY in Lucy's environment.",
    ),
    KnownArg(
        name="voice-groq-model",
        value_type=str,
        default="whisper-large-v3-turbo",
        description="Groq transcription model. Requires GROQ_API_KEY in Lucy's environment.",
    ),
    KnownArg(
        name="voice-google-model",
        value_type=str,
        default="default",
        description="Google Cloud Speech-to-Text v1 model. Requires GOOGLE_ACCESS_TOKEN in Lucy's environment.",
    ),
    KnownArg(
        name="voice-yandex-model",
        value_type=str,
        default="general",
        description="Yandex SpeechKit v1 topic/model. Requires a service-account YANDEX_API_KEY in Lucy's environment.",
    ),
    KnownArg(
        name="voice-language",
        value_type=str,
        default="",
        description="OpenAI/Groq: optional two-letter code (en, ru). Google/Yandex: required language tag (en-US, ru-RU).",
    ),
    KnownArg(
        name="voice-request-timeout-seconds",
        value_type=int,
        default=60,
        description="Online transcription network timeout in seconds (positive).",
    ),
    KnownArg(
        name="voice-silence-seconds",
        value_type=float,
        default=1.5,
        description="Online recording stops after this much silence following speech (positive seconds).",
    ),
    KnownArg(
        name="voice-silence-threshold",
        value_type=int,
        default=500,
        description="Online speech detection RMS threshold for PCM16 audio (1..32767). Lower for a quiet microphone.",
    ),
]


@dataclass(frozen=True)
class OnlineConfig:
    model: str
    language: str
    request_timeout_seconds: int
    silence_seconds: float
    silence_threshold: int

    @classmethod
    def from_args(cls, args: ParsedArgs, provider: VoiceProvider) -> OnlineConfig:
        model_flag = {
            VoiceProvider.OPENAI: "voice-openai-model",
            VoiceProvider.GROQ: "voice-groq-model",
            VoiceProvider.GOOGLE: "voice-google-model",
            VoiceProvider.YANDEX: "voice-yandex-model",
        }[provider]
        model = args.require(model_flag).value.strip()
        language = args.require("voice-language").value.strip()
        request_timeout = args.require("voice-request-timeout-seconds").value
        silence_seconds = args.require("voice-silence-seconds").value
        threshold = args.require("voice-silence-threshold").value
        if not model:
            raise VoiceError(
                f"--{model_flag} must not be empty.", reason="invalid_config"
            )
        if provider in (VoiceProvider.GOOGLE, VoiceProvider.YANDEX):
            if not re.fullmatch(r"[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})+", language):
                raise VoiceError(
                    f"Set --voice-language to a language tag such as ru-RU or en-US for {provider}.",
                    reason="invalid_config",
                )
        else:
            language = language.lower()
            if language and (
                len(language) != 2 or not language.isascii() or not language.isalpha()
            ):
                raise VoiceError(
                    "--voice-language must be a two-letter language code.",
                    reason="invalid_config",
                )
        if request_timeout <= 0:
            raise VoiceError(
                "--voice-request-timeout-seconds must be positive.",
                reason="invalid_config",
            )
        if not math.isfinite(silence_seconds) or silence_seconds <= 0:
            raise VoiceError(
                "--voice-silence-seconds must be finite and positive.",
                reason="invalid_config",
            )
        if not 1 <= threshold <= 32767:
            raise VoiceError(
                "--voice-silence-threshold must be between 1 and 32767.",
                reason="invalid_config",
            )
        return cls(model, language, request_timeout, silence_seconds, threshold)


@dataclass(frozen=True)
class VoiceConfig:
    provider: VoiceProvider
    model_path: str
    recorder_path: str
    timeout_seconds: int
    sample_rate: int
    online: OnlineConfig | None

    @classmethod
    def from_args(cls, args: ParsedArgs) -> VoiceConfig:
        provider = args.require("voice-provider").value
        model_path = args.require("voice-offline-vosk-model-path").value.strip()
        if provider is VoiceProvider.OFFLINE_VOSK and not model_path:
            raise VoiceError(
                "Missing --voice-offline-vosk-model-path.", reason="missing_model_path"
            )
        recorder_path = args.require("voice-recorder-path").value.strip()
        if not recorder_path:
            raise VoiceError(
                "--voice-recorder-path must not be empty.", reason="invalid_config"
            )
        timeout_seconds = args.require("voice-timeout-seconds").value
        sample_rate = args.require("voice-sample-rate").value
        for flag, value in (
            ("voice-timeout-seconds", timeout_seconds),
            ("voice-sample-rate", sample_rate),
        ):
            if value <= 0:
                raise VoiceError(f"--{flag} must be positive.", reason="invalid_config")
        if provider is VoiceProvider.YANDEX and sample_rate not in (8000, 16000, 48000):
            raise VoiceError(
                "Yandex requires --voice-sample-rate 8000, 16000, or 48000.",
                reason="invalid_config",
            )
        if provider is VoiceProvider.GOOGLE and not 8000 <= sample_rate <= 48000:
            raise VoiceError(
                "Google requires --voice-sample-rate between 8000 and 48000.",
                reason="invalid_config",
            )
        if os.path.dirname(recorder_path) or recorder_path.startswith("~"):
            recorder_path = abs_expand_path(recorder_path)
        return cls(
            provider=provider,
            model_path=abs_expand_path(model_path) if model_path else "",
            recorder_path=recorder_path,
            timeout_seconds=timeout_seconds,
            sample_rate=sample_rate,
            online=None
            if provider is VoiceProvider.OFFLINE_VOSK
            else OnlineConfig.from_args(args, provider),
        )
