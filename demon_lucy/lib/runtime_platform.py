from __future__ import annotations

import os
from typing import Literal

RuntimePlatform = Literal["posix", "windows"]


def detect_runtime_platform() -> RuntimePlatform:
    if os.name == "nt":
        return "windows"
    return "posix"
