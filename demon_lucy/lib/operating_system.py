from __future__ import annotations

import os
import sys
from enum import StrEnum


class OperatingSystem(StrEnum):
    LINUX = "linux"
    MACOS = "macos"
    WINDOWS = "windows"
    OTHER = "other"


def detect_operating_system() -> OperatingSystem:
    if os.name == "nt":
        return OperatingSystem.WINDOWS
    if sys.platform == "darwin":
        return OperatingSystem.MACOS
    if sys.platform.startswith("linux"):
        return OperatingSystem.LINUX
    return OperatingSystem.OTHER
