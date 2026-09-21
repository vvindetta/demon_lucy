from __future__ import annotations

import io
from http.client import IncompleteRead
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

import pytest

import demon_lucy.modules.voice.online as online
from demon_lucy.lib.args.parser import parse_args
from demon_lucy.modules.voice import Voice
from demon_lucy.modules.voice.config import VoiceConfig, VoiceProvider
from demon_lucy.modules.voice.errors import VoiceError
from demon_lucy.modules.voice.providers import listen_once


def _args(provider="openai", *tokens):
    return parse_args(
        args=["--voice-provider", provider, *tokens],
        template=Voice.template,
    )


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-secret")
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-secret")
    capture = Mock(return_value=b"test wav")
    send = Mock(return_value={"text": " hello "})
    monkeypatch.setattr(online, "capture_wav", capture)
    monkeypatch.setattr(online, "post_multipart_json", send)
    return capture, send


@pytest.mark.parametrize(
    ("provider", "key", "url", "model"),
    [
        (
            "openai",
            "test-openai-secret",
            "https://api.openai.com/v1/audio/transcriptions",
            "gpt-transcribe",
        ),
        (
            "groq",
            "test-groq-secret",
            "https://api.groq.com/openai/v1/audio/transcriptions",
            "whisper-large-v3-turbo",
        ),
    ],
)
def test_online_provider_routing_without_vosk(
    api, monkeypatch, provider, key, url, model
):
    capture, send = api
    offline = Mock(side_effect=AssertionError("online must not load Vosk"))
    monkeypatch.setattr("demon_lucy.modules.voice.vosk.transcribe", offline)

    result = listen_once(_args(provider))

    assert result.provider is VoiceProvider(provider)
    assert result.model == model
    assert result.text == "hello"
    offline.assert_not_called()
    assert capture.call_count == send.call_count == 1
    assert send.call_args.args == (url,)
    assert send.call_args.kwargs["headers"]["Authorization"] == f"Bearer {key}"
    assert send.call_args.kwargs["fields"] == {
        "model": model,
        "response_format": "json",
    }
    assert send.call_args.kwargs["file"].content == b"test wav"
    assert send.call_args.kwargs["file"].filename == "voice.wav"


@pytest.mark.parametrize("provider", ["openai", "groq"])
def test_online_model_language_and_timeout_options(api, provider):
    _, send = api
    result = listen_once(
        _args(
            provider,
            f"--voice-{provider}-model",
            "custom-transcription-model",
            "--voice-language",
            "RU",
            "--voice-request-timeout-seconds",
            "17",
        )
    )
    assert result.model == "custom-transcription-model"
    assert send.call_args.kwargs["fields"] == {
        "model": "custom-transcription-model",
        "response_format": "json",
        "language": "ru",
    }
    assert send.call_args.kwargs["timeout_seconds"] == 17


@pytest.mark.parametrize(
    ("provider", "key"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("groq", "GROQ_API_KEY"),
    ],
)
def test_missing_api_key_fails_before_recording(api, monkeypatch, provider, key):
    capture, send = api
    monkeypatch.delenv(key)
    with pytest.raises(VoiceError, match=key) as error:
        listen_once(_args(provider))
    assert error.value.reason == "missing_api_key"
    capture.assert_not_called()
    send.assert_not_called()


@pytest.mark.parametrize(
    "key", ["secret\ninjected-header", "секрет", "secret with space"]
)
def test_invalid_api_key_is_not_exposed_or_recorded(api, monkeypatch, key):
    capture, send = api
    monkeypatch.setenv("OPENAI_API_KEY", key)
    with pytest.raises(VoiceError) as error:
        listen_once(_args())
    assert error.value.reason == "invalid_api_key"
    assert key not in str(error.value)
    capture.assert_not_called()
    send.assert_not_called()


@pytest.mark.parametrize(
    "tokens",
    [
        ["--voice-openai-model", ""],
        ["--voice-language", "russian"],
        ["--voice-language", "рф"],
        ["--voice-request-timeout-seconds", "0"],
        ["--voice-silence-seconds", "0"],
        ["--voice-silence-seconds", "-1"],
        ["--voice-silence-seconds", "nan"],
        ["--voice-silence-seconds", "inf"],
        ["--voice-silence-threshold", "0"],
        ["--voice-silence-threshold", "32768"],
    ],
)
def test_invalid_online_config_is_rejected_before_recording(api, tokens):
    capture, send = api
    with pytest.raises(VoiceError) as error:
        listen_once(_args("openai", *tokens))
    assert error.value.reason == "invalid_config"
    capture.assert_not_called()
    send.assert_not_called()


def test_silent_recording_is_not_uploaded(api):
    capture, send = api
    capture.return_value = b""
    assert listen_once(_args()).text == ""
    send.assert_not_called()


@pytest.mark.parametrize("status", [301, 400, 401, 403, 429, 500])
def test_http_errors_do_not_leak_body_or_credentials(api, status):
    _, send = api
    body = io.BytesIO(b"test-openai-secret private audio")
    send.side_effect = HTTPError(
        "https://api.openai.com/v1/audio/transcriptions",
        status,
        "test-openai-secret",
        {},
        body,
    )
    with pytest.raises(VoiceError, match=f"HTTP {status}") as error:
        listen_once(_args())
    assert error.value.reason == "provider_http_error"
    assert "test-openai-secret" not in str(error.value)
    assert "private audio" not in str(error.value)
    assert body.closed
    assert send.call_count == 1


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("secret"),
        URLError("secret"),
        OSError("secret"),
        IncompleteRead(b"secret"),
    ],
)
def test_network_errors_are_reported_without_credentials(api, failure):
    _, send = api
    send.side_effect = failure
    with pytest.raises(VoiceError) as error:
        listen_once(_args())
    assert error.value.reason == "provider_unavailable"
    assert "secret" not in str(error.value)
    assert send.call_count == 1


@pytest.mark.parametrize("result", [None, [], "hello", {}, {"text": 1}, {"text": None}])
def test_bad_response_shape_is_reported(api, result):
    _, send = api
    send.return_value = result
    with pytest.raises(VoiceError) as error:
        listen_once(_args())
    assert error.value.reason == "provider_invalid_response"


def test_invalid_json_is_reported(api):
    _, send = api
    send.side_effect = ValueError("private content")
    with pytest.raises(VoiceError) as error:
        listen_once(_args())
    assert error.value.reason == "provider_invalid_response"
    assert "private content" not in str(error.value)


def test_offline_does_not_require_online_configuration():
    config = VoiceConfig.from_args(
        _args(
            "offline-vosk",
            "--voice-offline-vosk-model-path",
            "/model",
            "--voice-silence-seconds",
            "0",
            "--voice-language",
            "invalid",
        )
    )
    assert config.provider is VoiceProvider.OFFLINE_VOSK
    assert config.online is None


@pytest.fixture
def cloud_api(monkeypatch):
    import wave

    monkeypatch.setenv("GOOGLE_ACCESS_TOKEN", "test-google-secret")
    monkeypatch.setenv("YANDEX_API_KEY", "test-yandex-secret")
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    pcm = b"\x10\x20" * 160
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    capture = Mock(return_value=buffer.getvalue())
    send = Mock()
    monkeypatch.setattr(online, "capture_wav", capture)
    monkeypatch.setattr(online, "post_json_response", send)
    return capture, send, pcm


def test_google_json_audio_and_ranked_segments(cloud_api, monkeypatch):
    import base64
    import json

    capture, send, _ = cloud_api
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "my-cloud-project")
    send.return_value = {
        "results": [
            {"alternatives": [{"transcript": "привет"}, {"transcript": "incorrect"}]},
            {"alternatives": [{"transcript": " мир"}]},
        ]
    }

    result = listen_once(
        _args(
            "google",
            "--voice-language",
            "ru-RU",
            "--voice-google-model",
            "latest_short",
            "--voice-request-timeout-seconds",
            "19",
        )
    )

    assert result.provider is VoiceProvider.GOOGLE
    assert result.model == "latest_short"
    assert result.text == "привет мир"
    assert send.call_args.args == ("https://speech.googleapis.com/v1/speech:recognize",)
    sent = send.call_args.kwargs
    assert sent["headers"]["Authorization"] == "Bearer test-google-secret"
    assert sent["headers"]["x-goog-user-project"] == "my-cloud-project"
    assert sent["headers"]["Content-Type"] == "application/json"
    assert sent["timeout_seconds"] == 19
    payload = json.loads(sent["data"])
    assert payload["config"] == {
        "encoding": "LINEAR16",
        "sampleRateHertz": 16000,
        "languageCode": "ru-RU",
        "model": "latest_short",
    }
    assert base64.b64decode(payload["audio"]["content"]) == capture.return_value
    assert "test-google-secret" not in sent["data"].decode()


def test_yandex_sends_raw_pcm_without_folder_or_wav_header(cloud_api):
    from urllib.parse import parse_qs, urlsplit

    _, send, pcm = cloud_api
    send.return_value = {"result": " привет мир "}

    result = listen_once(_args("yandex", "--voice-language", "ru-RU"))

    assert result.provider is VoiceProvider.YANDEX
    assert result.model == "general"
    assert result.text == "привет мир"
    url = urlsplit(send.call_args.args[0])
    assert (
        f"{url.scheme}://{url.netloc}{url.path}"
        == "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
    )
    assert parse_qs(url.query) == {
        "lang": ["ru-RU"],
        "topic": ["general"],
        "format": ["lpcm"],
        "sampleRateHertz": ["16000"],
    }
    assert (
        send.call_args.kwargs["headers"]["Authorization"]
        == "Api-Key test-yandex-secret"
    )
    assert (
        send.call_args.kwargs["headers"]["Content-Type"] == "application/octet-stream"
    )
    assert send.call_args.kwargs["data"] == pcm
    assert "test-yandex-secret" not in url.query


@pytest.mark.parametrize(
    ("provider", "variable", "reason"),
    [
        ("google", "GOOGLE_ACCESS_TOKEN", "missing_access_token"),
        ("yandex", "YANDEX_API_KEY", "missing_api_key"),
    ],
)
def test_cloud_missing_credentials_do_not_start_recording(
    cloud_api, monkeypatch, provider, variable, reason
):
    capture, send, _ = cloud_api
    monkeypatch.delenv(variable)
    with pytest.raises(VoiceError, match=variable) as error:
        listen_once(_args(provider, "--voice-language", "ru-RU"))
    assert error.value.reason == reason
    capture.assert_not_called()
    send.assert_not_called()


@pytest.mark.parametrize("provider", ["google", "yandex"])
@pytest.mark.parametrize(
    "language", ["", "ru", "ru_RU", "invalid", "ru-RU&folderId=other"]
)
def test_cloud_requires_language_tag_before_recording(cloud_api, provider, language):
    capture, send, _ = cloud_api
    with pytest.raises(VoiceError, match="voice-language") as error:
        listen_once(_args(provider, "--voice-language", language))
    assert error.value.reason == "invalid_config"
    capture.assert_not_called()
    send.assert_not_called()


@pytest.mark.parametrize(
    ("provider", "rate"),
    [
        ("yandex", "44100"),
        ("yandex", "24000"),
        ("google", "7999"),
        ("google", "48001"),
    ],
)
def test_cloud_sample_rate_limits_are_checked_before_recording(
    cloud_api, provider, rate
):
    capture, send, _ = cloud_api
    with pytest.raises(VoiceError) as error:
        listen_once(
            _args(provider, "--voice-language", "ru-RU", "--voice-sample-rate", rate)
        )
    assert error.value.reason == "invalid_config"
    capture.assert_not_called()
    send.assert_not_called()


@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("google", None),
        ("google", {"error": {"message": "secret"}}),
        ("google", {"results": None}),
        ("google", {"results": [1]}),
        ("google", {"results": [{"alternatives": []}]}),
        ("google", {"results": [{"alternatives": [{"transcript": 3}]}]}),
        ("yandex", {}),
        ("yandex", {"result": None}),
        ("yandex", {"error_code": "UNAUTHENTICATED", "error_message": "secret"}),
    ],
)
def test_cloud_malformed_responses_do_not_expose_body(cloud_api, provider, payload):
    _, send, _ = cloud_api
    send.return_value = payload
    with pytest.raises(VoiceError) as error:
        listen_once(_args(provider, "--voice-language", "ru-RU"))
    assert error.value.reason == "provider_invalid_response"
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    ("provider", "payload"),
    [
        ("google", {}),
        ("google", {"results": []}),
        ("yandex", {"result": ""}),
    ],
)
def test_cloud_empty_recognition_leaves_command(cloud_api, provider, payload):
    _, send, _ = cloud_api
    send.return_value = payload
    assert listen_once(_args(provider, "--voice-language", "ru-RU")).text == ""


@pytest.mark.parametrize("provider", ["google", "yandex"])
@pytest.mark.parametrize("failure", [401, 403, 429, "timeout"])
def test_cloud_network_and_auth_errors_are_sanitized(cloud_api, provider, failure):
    _, send, _ = cloud_api
    if failure == "timeout":
        send.side_effect = TimeoutError("test-secret")
    else:
        send.side_effect = HTTPError(
            "https://example.test", failure, "test-secret", {}, io.BytesIO(b"secret")
        )
    with pytest.raises(VoiceError) as error:
        listen_once(_args(provider, "--voice-language", "ru-RU"))
    assert error.value.reason in ("provider_unavailable", "provider_http_error")
    assert "secret" not in str(error.value)
    assert send.call_count == 1


def test_invalid_google_project_is_rejected_before_recording(cloud_api, monkeypatch):
    capture, send, _ = cloud_api
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "project\ninjected")
    with pytest.raises(VoiceError, match="GOOGLE_CLOUD_PROJECT"):
        listen_once(_args("google", "--voice-language", "en-US"))
    capture.assert_not_called()
    send.assert_not_called()
