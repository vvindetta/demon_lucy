from __future__ import annotations

import getpass
import platform
from collections.abc import Sequence

from demon_lucy.lib.ascii_art import LUCY_EYE_VERTICAL
from demon_lucy.lib.runtime_system import RuntimeSystem
from demon_lucy.modules.abstract_module import RunMode


def _operating_system_name(runtime_system: RuntimeSystem) -> str:
    if runtime_system == "linux":
        try:
            release = platform.freedesktop_os_release()
        except (AttributeError, OSError):
            release = {}
        name = release.get("PRETTY_NAME") or release.get("NAME")
        if name:
            return name

    if runtime_system == "macos":
        version = platform.mac_ver()[0]
        return f"macOS {version}".strip()

    system_name = platform.system().strip() or runtime_system
    system_release = platform.release().strip()
    return f"{system_name} {system_release}".strip()


def _host_identity() -> str:
    try:
        user_name = getpass.getuser().strip()
    except OSError:
        user_name = ""
    host_name = platform.node().strip()
    return f"{user_name or 'unknown'}@{host_name or 'unknown'}"


def _opened_events_state(
    *,
    disabled: bool,
    run_mode: RunMode,
    runtime_system: RuntimeSystem,
) -> str:
    if disabled:
        return "disabled"
    if run_mode == "oneshot" or runtime_system == "linux":
        return "enabled"
    return "unavailable"


def _path_count_text(path_count: int) -> str:
    suffix = "path" if path_count == 1 else "paths"
    return f"{path_count} {suffix}"


def _duration_text(seconds: float) -> str:
    total_minutes = max(0, int(seconds // 60))
    days, remaining_minutes = divmod(total_minutes, 24 * 60)
    hours, minutes = divmod(remaining_minutes, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def neofetch_lines(
    *,
    run_mode: RunMode,
    runtime_system: RuntimeSystem,
    module_count: int,
    watch_path_count: int,
    opened_events_disabled: bool,
    runtime_uptime_seconds: float = 0.0,
    eye: Sequence[str] = LUCY_EYE_VERTICAL,
) -> list[str]:
    left_lines = [
        *eye,
        "",
        "Demon Lucy",
        "-----------",
        f"Mode      {run_mode}",
        f"Uptime    {_duration_text(runtime_uptime_seconds)}",
        f"Modules   {module_count}",
        f"Watch     {_path_count_text(watch_path_count)}",
        "Opened    "
        + _opened_events_state(
            disabled=opened_events_disabled,
            run_mode=run_mode,
            runtime_system=runtime_system,
        ),
    ]
    identity = _host_identity()
    right_lines = [
        identity,
        "-" * len(identity),
        f"OS       {_operating_system_name(runtime_system)}",
        f"Kernel   {platform.release().strip() or 'unknown'}",
        f"Arch     {platform.machine().strip() or 'unknown'}",
        f"Python   {platform.python_version()}",
    ]

    left_width = max(len(line) for line in left_lines)
    line_count = max(len(left_lines), len(right_lines))
    rendered: list[str] = []
    for index in range(line_count):
        left = left_lines[index] if index < len(left_lines) else ""
        right = right_lines[index] if index < len(right_lines) else ""
        line = f"{left:<{left_width}}"
        if right:
            line += f"    {right}"
        rendered.append(line.rstrip() + "\n")
    return rendered
