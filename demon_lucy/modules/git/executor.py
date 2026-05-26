from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Dict


@dataclass(frozen=True)
class GitExecutor:
    repo_root: str
    environment: Dict[str, str]

    def run(
        self,
        arguments: list[str],
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git"] + arguments,
            cwd=self.repo_root,
            env=self.environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )


def combined_output(result: subprocess.CompletedProcess[str]) -> str:
    return ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
