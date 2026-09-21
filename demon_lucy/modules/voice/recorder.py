from __future__ import annotations

import queue
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from demon_lucy.modules.voice.config import VoiceConfig
from demon_lucy.modules.voice.errors import VoiceError

_AUDIO_CHUNK_BYTES = 4000
_ERROR_TAIL_BYTES = 400
_SHUTDOWN_TIMEOUT_SECONDS = 2


def _stop_recorder(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        process.terminate()
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        process.wait(timeout=_SHUTDOWN_TIMEOUT_SECONDS)


@contextmanager
def record_audio(config: VoiceConfig) -> Iterator[Iterator[bytes]]:
    """Stream PCM with a deadline, bounded buffers, and deterministic cleanup.

    Pipe readers keep a stalled recorder or a full stderr pipe from blocking the
    recognition loop. A bounded audio queue applies backpressure to the recorder.
    """
    command = [
        config.recorder_path,
        "-q",
        "-f",
        "S16_LE",
        "-r",
        str(config.sample_rate),
        "-c",
        "1",
        "-t",
        "raw",
    ]
    try:
        process = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0
        )
    except FileNotFoundError as exc:
        raise VoiceError(
            f"Recorder executable not found: {config.recorder_path}",
            reason="missing_dependency",
        ) from exc
    except OSError as exc:
        raise VoiceError(
            f"Voice recording failed: {exc}", reason="record_failed"
        ) from exc

    deadline = time.monotonic() + config.timeout_seconds
    audio: queue.Queue[bytes | OSError | None] = queue.Queue(maxsize=8)
    stopped = threading.Event()
    error_tail = bytearray()

    def enqueue(item: bytes | OSError | None) -> None:
        while not stopped.is_set():
            try:
                audio.put(item, timeout=0.05)
                return
            except queue.Full:
                continue

    def read_audio() -> None:
        pending = b""
        try:
            while not stopped.is_set():
                data = process.stdout.read(_AUDIO_CHUNK_BYTES)
                if not data:
                    if pending:
                        enqueue(
                            OSError("Recorder returned an incomplete PCM16 sample.")
                        )
                    break
                data = pending + data
                end = len(data) - len(data) % 2
                pending = data[end:]
                if end:
                    enqueue(data[:end])
        except (OSError, ValueError) as exc:
            enqueue(OSError(str(exc)))
        finally:
            enqueue(None)

    def read_errors() -> None:
        try:
            while not stopped.is_set():
                data = process.stderr.read(_AUDIO_CHUNK_BYTES)
                if not data:
                    break
                error_tail.extend(data)
                del error_tail[:-_ERROR_TAIL_BYTES]
        except (OSError, ValueError):
            # Closing the recorder can interrupt a pipe read.
            pass

    readers = [
        threading.Thread(target=read_audio, name="lucy-voice-audio", daemon=True),
        threading.Thread(target=read_errors, name="lucy-voice-stderr", daemon=True),
    ]

    def frames() -> Iterator[bytes]:
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                item = audio.get(timeout=remaining)
            except queue.Empty:
                return
            if isinstance(item, OSError):
                raise VoiceError(
                    f"Cannot read recorder audio: {item}", reason="record_failed"
                ) from item
            if item is None:
                try:
                    returncode = process.wait(
                        timeout=max(0, deadline - time.monotonic())
                    )
                except subprocess.TimeoutExpired:
                    return
                readers[1].join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
                if returncode != 0:
                    diagnostic = error_tail.decode("utf-8", errors="replace").strip()
                    raise VoiceError(
                        f"Voice recording failed (exit {returncode}): {diagnostic}",
                        reason="record_failed",
                    )
                return
            yield item

    started: list[threading.Thread] = []
    failed = False
    try:
        for reader in readers:
            reader.start()
            started.append(reader)
        yield frames()
    except BaseException:
        failed = True
        raise
    finally:
        stopped.set()
        try:
            _stop_recorder(process)
        except (OSError, subprocess.TimeoutExpired) as exc:
            if not failed:
                raise VoiceError(
                    f"Cannot stop voice recorder: {exc}", reason="recorder_stop_failed"
                ) from exc
        finally:
            process.stdout.close()
            process.stderr.close()
            for reader in started:
                reader.join(timeout=_SHUTDOWN_TIMEOUT_SECONDS)
