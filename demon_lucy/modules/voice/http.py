from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.request import HTTPRedirectHandler, Request, build_opener


@dataclass(frozen=True)
class MultipartFile:
    name: str
    filename: str
    content_type: str
    content: bytes


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # An authenticated upload must not forward its credentials or file.
        return None


def post_multipart_json(
    url: str,
    *,
    fields: Mapping[str, str],
    file: MultipartFile,
    headers: Mapping[str, str],
    timeout_seconds: int,
    max_response_bytes: int,
) -> Any:
    boundary = uuid.uuid4().hex
    for name in (*fields, file.name, file.filename, file.content_type):
        if any(character in name for character in '\r\n"'):
            raise ValueError("Invalid multipart header value")
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode(
                "utf-8"
            )
        )
    parts.extend(
        [
            f'--{boundary}\r\nContent-Disposition: form-data; name="{file.name}"; filename="{file.filename}"\r\nContent-Type: {file.content_type}\r\n\r\n'.encode(
                "utf-8"
            ),
            file.content,
            f"\r\n--{boundary}--\r\n".encode("ascii"),
        ]
    )
    return post_json_response(
        url,
        data=b"".join(parts),
        headers={
            **headers,
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        timeout_seconds=timeout_seconds,
        max_response_bytes=max_response_bytes,
    )


def post_json_response(
    url: str,
    *,
    data: bytes,
    headers: Mapping[str, str],
    timeout_seconds: int,
    max_response_bytes: int,
) -> Any:
    """POST encoded data with bounded JSON reading and no credential redirects."""
    request = Request(url, data=data, headers=dict(headers), method="POST")
    with build_opener(_NoRedirect()).open(request, timeout=timeout_seconds) as response:
        payload = response.read(max_response_bytes + 1)
    if len(payload) > max_response_bytes:
        raise ValueError("HTTP response exceeds the size limit")
    return json.loads(payload)
