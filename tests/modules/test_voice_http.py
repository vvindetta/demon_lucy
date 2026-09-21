from __future__ import annotations

import io
from email import policy
from email.parser import BytesParser
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

import demon_lucy.modules.voice.http as http


def _post(**kwargs):
    return http.post_multipart_json(
        "https://example.test/transcriptions",
        fields={"model": "test-model", "language": "ru"},
        file=http.MultipartFile("file", "voice.wav", "audio/wav", b"\x00\xff\r\n"),
        headers={"Authorization": "Bearer test-key"},
        timeout_seconds=17,
        max_response_bytes=100,
        **kwargs,
    )


def test_multipart_upload_keeps_binary_data_and_fields(monkeypatch):
    response = io.BytesIO(b'{"text": "hello"}')
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(http, "build_opener", Mock(return_value=opener))

    assert _post() == {"text": "hello"}

    request = opener.open.call_args.args[0]
    content_type = request.get_header("Content-type")
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
        + request.data
    )
    parts = {
        part.get_param("name", header="content-disposition"): part
        for part in message.iter_parts()
    }
    assert parts["model"].get_payload(decode=True) == b"test-model"
    assert parts["language"].get_payload(decode=True) == b"ru"
    assert parts["file"].get_payload(decode=True) == b"\x00\xff\r\n"
    assert parts["file"].get_filename() == "voice.wav"
    assert parts["file"].get_content_type() == "audio/wav"
    assert request.get_method() == "POST"
    assert request.get_header("Authorization") == "Bearer test-key"
    assert opener.open.call_args.kwargs == {"timeout": 17}
    assert response.closed


@pytest.mark.parametrize("body", [b"invalid json", b"x" * 101, b"\xff"])
def test_invalid_or_oversized_responses_close_the_stream(monkeypatch, body):
    response = io.BytesIO(body)
    opener = Mock()
    opener.open.return_value = response
    monkeypatch.setattr(http, "build_opener", Mock(return_value=opener))
    with pytest.raises((ValueError, UnicodeError)):
        _post()
    assert response.closed


def test_authenticated_upload_redirect_is_rejected():
    response = io.BytesIO()
    request = http.Request(
        "https://example.test/upload",
        data=b"audio",
        headers={"Authorization": "Bearer secret"},
    )
    opener = http.build_opener(http._NoRedirect())
    with pytest.raises(HTTPError):
        opener.error(
            "http",
            request,
            response,
            302,
            "Found",
            {"location": "https://other.test/"},
        )
    response.close()


def test_multipart_header_injection_is_rejected_before_network(monkeypatch):
    opener = Mock()
    monkeypatch.setattr(http, "build_opener", opener)
    with pytest.raises(ValueError):
        http.post_multipart_json(
            "https://example.test/upload",
            fields={},
            file=http.MultipartFile("file", 'voice"\r\n.txt', "audio/wav", b"audio"),
            headers={},
            timeout_seconds=1,
            max_response_bytes=100,
        )
    opener.assert_not_called()
