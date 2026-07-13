from __future__ import annotations

import os
import sys
from typing import Literal

RuntimeSystem = Literal["linux", "macos", "windows", "other"]


def detect_runtime_system() -> RuntimeSystem:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return "other"
