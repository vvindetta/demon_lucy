from __future__ import annotations

import io
import sys
import wave
from array import array
from collections.abc import Iterable, Iterator
from dataclasses import replace

from demon_lucy.modules.voice.config import VoiceConfig, VoiceProvider
from demon_lucy.modules.voice.errors import VoiceError
from demon_lucy.modules.voice.recorder import record_audio


# OpenAI/Groq accept 25 MB files. Leave room for WAV and multipart headers.
_MAX_AUDIO_BYTES = 24_000_000
# Synchronous API limits. Google's PCM limit leaves room for base64/JSON in 10 MB.
_SYNC_LIMITS = {
    VoiceProvider.GOOGLE: (60, 7_000_000),
    VoiceProvider.YANDEX: (30, 1_000_000),
}


def _pcm_frames(chunks: Iterable[bytes], sample_rate: int) -> Iterator[bytes]:
    """Use 20 ms windows so silence detection is independent of pipe read sizes."""
    frame_bytes = max(1, sample_rate // 50) * 2
    pending = bytearray()
    recorded_bytes = 0
    for chunk in chunks:
        recorded_bytes += len(chunk)
        if recorded_bytes > _MAX_AUDIO_BYTES:
            raise VoiceError(
                "Recording is too large to upload; reduce --voice-timeout-seconds or --voice-sample-rate.",
                reason="audio_too_large",
            )
        pending.extend(chunk)
        end = len(pending) - len(pending) % frame_bytes
        for offset in range(0, end, frame_bytes):
            yield bytes(pending[offset : offset + frame_bytes])
        del pending[:end]
    if pending:
        yield bytes(pending)


def capture_wav(config: VoiceConfig) -> bytes:
    """Capture one utterance; return no upload for audio below the speech threshold."""
    assert config.online is not None
    remaining_samples = None
    if config.provider in _SYNC_LIMITS:
        seconds, pcm_bytes = _SYNC_LIMITS[config.provider]
        config = replace(config, timeout_seconds=min(config.timeout_seconds, seconds))
        remaining_samples = min(
            config.sample_rate * config.timeout_seconds, pcm_bytes // 2
        )
    heard_speech = False
    silent_samples = 0
    silence_samples = config.online.silence_seconds * config.sample_rate
    threshold_squared = config.online.silence_threshold**2
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(config.sample_rate)
        with record_audio(config) as chunks:
            for frame in _pcm_frames(chunks, config.sample_rate):
                if remaining_samples is not None:
                    frame = frame[: remaining_samples * 2]
                wav.writeframesraw(frame)
                samples = array("h", frame)
                if sys.byteorder != "little":
                    samples.byteswap()
                loud = sum(
                    sample * sample for sample in samples
                ) >= threshold_squared * len(samples)
                if loud:
                    heard_speech = True
                    silent_samples = 0
                elif heard_speech:
                    silent_samples += len(samples)
                    if silent_samples >= silence_samples:
                        break
                if remaining_samples is not None:
                    remaining_samples -= len(samples)
                    if remaining_samples == 0:
                        break
    return buffer.getvalue() if heard_speech else b""
