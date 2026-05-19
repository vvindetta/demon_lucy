from __future__ import annotations

import socket
import threading
from typing import Callable, Optional
from urllib.parse import urlparse


def resolve_address_infos(
    host_name: str,
    port_number: int,
    timeout_seconds: float,
) -> tuple[list[tuple], bool]:
    resolver_result: dict[str, object] = {}
    resolver_done = threading.Event()

    def _resolve() -> None:
        try:
            resolver_result["address_infos"] = socket.getaddrinfo(
                host_name,
                port_number,
                family=socket.AF_UNSPEC,
                type=socket.SOCK_STREAM,
            )
        except OSError as exception:
            resolver_result["error"] = exception
        finally:
            resolver_done.set()

    resolver_thread = threading.Thread(target=_resolve, daemon=True)
    resolver_thread.start()
    if not resolver_done.wait(timeout_seconds):
        return [], True

    error_value = resolver_result.get("error")
    if isinstance(error_value, OSError):
        raise error_value

    address_infos = resolver_result.get("address_infos")
    if isinstance(address_infos, list):
        return address_infos, False
    return [], False


def parse_remote_endpoint(remote_url_value: str) -> tuple[Optional[str], Optional[int]]:
    remote_url_text = (remote_url_value or "").strip()
    if not remote_url_text:
        return None, None

    if remote_url_text.startswith("file://"):
        return None, None
    if remote_url_text.startswith("/") or remote_url_text.startswith("./"):
        return None, None
    if remote_url_text.startswith("../"):
        return None, None
    if remote_url_text.startswith("\\\\") or remote_url_text.startswith("//"):
        return None, None
    if (
        len(remote_url_text) >= 3
        and remote_url_text[0].isalpha()
        and remote_url_text[1] == ":"
        and remote_url_text[2] in {"/", "\\"}
    ):
        return None, None

    if "://" in remote_url_text:
        parsed = urlparse(remote_url_text)
        host_name = parsed.hostname
        if not host_name:
            return None, None

        scheme_name = (parsed.scheme or "").lower()
        if parsed.port is not None:
            return host_name, parsed.port

        default_port_by_scheme = {
            "http": 80,
            "https": 443,
            "ssh": 22,
            "git": 9418,
            "git+ssh": 22,
            "ssh+git": 22,
            "sftp": 22,
        }
        return host_name, default_port_by_scheme.get(scheme_name, 22)

    if ":" in remote_url_text:
        host_part, _, _path_part = remote_url_text.partition(":")
        if "@" in host_part:
            host_part = host_part.rsplit("@", 1)[1]
        if host_part and "/" not in host_part and "\\" not in host_part:
            return host_part, 22

    return None, None


def remote_is_reachable(
    repo_root: str,
    remote_name: str,
    timeout_seconds: float,
    network_probe_timeout_seconds: float,
    *,
    remote_url_getter: Callable[[], str | None],
    parse_remote_endpoint_fn: Callable[[str], tuple[Optional[str], Optional[int]]],
    resolve_address_infos_fn: Callable[[str, int, float], tuple[list[tuple], bool]],
    logger,
) -> bool:
    remote_url_value = remote_url_getter()
    if not remote_url_value:
        return True

    host_name, port_number = parse_remote_endpoint_fn(remote_url_value)
    if not host_name or not port_number:
        return True

    timeout_candidates = [
        candidate
        for candidate in (timeout_seconds, network_probe_timeout_seconds)
        if candidate > 0.0
    ]
    if not timeout_candidates:
        logger.info(
            "invalid network probe timeout; waiting for network before pull | repo=%s | remote=%s",
            repo_root,
            remote_name,
        )
        return False
    connect_timeout_seconds = min(timeout_candidates)

    try:
        address_infos, dns_resolution_timed_out = resolve_address_infos_fn(
            host_name=host_name,
            port_number=port_number,
            timeout_seconds=connect_timeout_seconds,
        )
    except OSError:
        logger.info(
            "remote host resolution failed; waiting for network before pull | repo=%s | remote=%s | host=%s",
            repo_root,
            remote_name,
            host_name,
        )
        return False
    if dns_resolution_timed_out:
        logger.info(
            "remote host resolution timed out; waiting for network before pull | repo=%s | remote=%s | host=%s",
            repo_root,
            remote_name,
            host_name,
        )
        return False

    seen_sockaddrs = set()
    for family, socktype, proto, _canonname, sockaddr in address_infos:
        if sockaddr in seen_sockaddrs:
            continue
        seen_sockaddrs.add(sockaddr)
        probe_socket: socket.socket | None = None
        try:
            probe_socket = socket.socket(family, socktype, proto)
            probe_socket.settimeout(connect_timeout_seconds)
            probe_socket.connect(sockaddr)
            probe_socket.close()
            probe_socket = None
            return True
        except OSError:
            if probe_socket is not None:
                try:
                    probe_socket.close()
                except OSError:
                    pass
            continue

    logger.info(
        "remote endpoint unreachable; waiting for network before pull | repo=%s | remote=%s | host=%s | port=%s",
        repo_root,
        remote_name,
        host_name,
        port_number,
    )
    return False
