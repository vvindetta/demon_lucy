from __future__ import annotations

from dataclasses import dataclass

from demon_lucy.lib.args.models import ParsedArgs
from demon_lucy.modules.voice import online, vosk
from demon_lucy.modules.voice.config import VoiceConfig, VoiceProvider


@dataclass(frozen=True)
class TranscriptResult:
    text: str
    provider: VoiceProvider
    model: str


def listen_once(args: ParsedArgs) -> TranscriptResult:
    config = VoiceConfig.from_args(args)
    if config.provider is VoiceProvider.OFFLINE_VOSK:
        text = vosk.transcribe(config)
        model = config.model_path
    else:
        text = online.transcribe(config)
        assert config.online is not None
        model = config.online.model
    return TranscriptResult(text=text, provider=config.provider, model=model)
