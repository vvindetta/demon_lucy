from __future__ import annotations

import os
import shutil

from demon_lucy.lib.operating_system import (
    OperatingSystem,
    detect_operating_system,
)

SYSTEMD_SETUP_DIR = "setup-systemd"


def systemd_setup_supported() -> bool:
    return (
        detect_operating_system() is OperatingSystem.LINUX
        and os.path.isdir("/run/systemd/system")
        and shutil.which("systemctl") is not None
    )


def _validate(value: str) -> None:
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("systemd values cannot contain control characters")


def _path(value: str) -> str:
    _validate(value)
    return (
        value.replace("\\", "\\x5c")
        .replace(" ", "\\x20")
        .replace('"', "\\x22")
        .replace("'", "\\x27")
        .replace("%", "%%")
    )


def _quote(value: str) -> str:
    _validate(value)
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    escaped = escaped.replace("$", "$$")
    return f'"{escaped}"'


def _command(arguments: tuple[str, ...]) -> str:
    return " ".join(_quote(argument) for argument in arguments)


def render_systemd_setup(
    *,
    lucy_home: str,
    workspace_root: str,
    config_path: str,
) -> dict[str, str]:
    daemon_command = _command(
        (
            "/usr/bin/python3",
            os.path.join(lucy_home, "main_daemon.py"),
            "--sys-config-path",
            config_path,
        )
    )
    oneshot_command = _command(
        (
            "/usr/bin/python3",
            os.path.join(lucy_home, "main_oneshot.py"),
            "--sys-config-path",
            config_path,
            "--oneshot-paths",
            workspace_root,
            "--sys-modules",
            "git",
            "archive",
        )
    )
    working_directory = _path(lucy_home)

    return {
        os.path.join(SYSTEMD_SETUP_DIR, "lucy-daemon.service"): (
            "[Unit]\n"
            "Description=Lucy Notes Daemon\n"
            "\n"
            "[Service]\n"
            "Type=exec\n"
            f"WorkingDirectory={working_directory}\n"
            f"ExecStart={daemon_command}\n"
            "Restart=on-failure\n"
            "RestartSec=2\n"
            "Environment=PYTHONUNBUFFERED=1\n"
        ),
        os.path.join(SYSTEMD_SETUP_DIR, "lucy-daemon.timer"): (
            "[Unit]\n"
            "Description=Start Lucy Notes Daemon after user startup\n"
            "\n"
            "[Timer]\n"
            "OnStartupSec=30s\n"
            "Unit=lucy-daemon.service\n"
            "\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        ),
        os.path.join(SYSTEMD_SETUP_DIR, "lucy-oneshot.service"): (
            "[Unit]\n"
            "Description=Lucy Notes oneshot run\n"
            "\n"
            "[Service]\n"
            "Type=oneshot\n"
            f"WorkingDirectory={working_directory}\n"
            f"ExecStart={oneshot_command}\n"
        ),
        os.path.join(SYSTEMD_SETUP_DIR, "lucy-oneshot.timer"): (
            "[Unit]\n"
            "Description=Run Lucy Notes oneshot periodically\n"
            "\n"
            "[Timer]\n"
            "OnStartupSec=5s\n"
            "OnCalendar=hourly\n"
            "Persistent=true\n"
            "Unit=lucy-oneshot.service\n"
            "\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        ),
    }
