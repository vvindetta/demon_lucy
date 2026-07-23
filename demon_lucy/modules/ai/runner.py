from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


_PROFILE_NAME = "lucy_ai"
_DEVELOPER_INSTRUCTIONS = (
    "Edit exactly one user-provided file represented by the JSON task input. "
    "The input contains its path as an identifier, the user's request, and the "
    "complete current content. Do not use tools, inspect the filesystem, search "
    "the web, or obtain any external context. Return the complete revised file "
    "content in the required JSON object. Do not restore the --ai command unless "
    "the user explicitly asks for that text."
)
_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"content": {"type": "string"}},
    "required": ["content"],
    "additionalProperties": False,
}


class CodexRunError(RuntimeError):
    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        super().__init__(detail or reason)


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _codex_command(
    *,
    executable: str,
    workdir: str,
    schema_path: str,
    output_path: str,
) -> list[str]:
    return [
        executable,
        "--ask-for-approval",
        "never",
        "exec",
        "--strict-config",
        "--ignore-user-config",
        "--ignore-rules",
        "--ephemeral",
        "--skip-git-repo-check",
        "--color",
        "never",
        "--output-schema",
        schema_path,
        "--output-last-message",
        output_path,
        "--cd",
        workdir,
        "--config",
        f'default_permissions={_toml_string(_PROFILE_NAME)}',
        "--config",
        f'permissions.{_PROFILE_NAME}.filesystem={{":minimal"="read"}}',
        "--config",
        f"developer_instructions={_toml_string(_DEVELOPER_INSTRUCTIONS)}",
        "--config",
        'web_search="disabled"',
        "--config",
        "tools.web_search=false",
        "--config",
        "features.apps=false",
        "--config",
        "features.remote_plugin=false",
        "--config",
        "features.multi_agent=false",
        "--config",
        "features.hooks=false",
        "--config",
        "features.memories=false",
        "--config",
        "features.goals=false",
        "--config",
        "features.shell_tool=false",
        "--config",
        "features.unified_exec=false",
        "--config",
        "features.shell_snapshot=false",
        "--config",
        "features.skill_mcp_dependency_install=false",
        "--config",
        'shell_environment_policy.inherit="none"',
        "--config",
        'history.persistence="none"',
        "--config",
        "project_doc_max_bytes=0",
        "--config",
        "project_root_markers=[]",
        "--config",
        'file_opener="none"',
        "--config",
        "feedback.enabled=false",
        "-",
    ]


def _error_detail(result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    if len(detail) > 1000:
        return detail[-1000:]
    return detail


def run_codex(
    *,
    source_path: str,
    source_content: str,
    prompt: str,
    timeout_seconds: int,
) -> str:
    executable = shutil.which("codex")
    if executable is None:
        raise CodexRunError("codex_not_found", "codex executable was not found")
    if timeout_seconds <= 0:
        raise CodexRunError("invalid_timeout", "AI timeout must be greater than zero")

    task_input = json.dumps(
        {
            "path": source_path,
            "prompt": prompt,
            "content": source_content,
        },
        ensure_ascii=False,
    )

    with tempfile.TemporaryDirectory(prefix="demon-lucy-ai-") as workdir:
        schema_path = os.path.join(workdir, "output.schema.json")
        output_path = os.path.join(workdir, "output.json")
        Path(schema_path).write_text(
            json.dumps(_OUTPUT_SCHEMA),
            encoding="utf-8",
        )
        command = _codex_command(
            executable=executable,
            workdir=workdir,
            schema_path=schema_path,
            output_path=output_path,
        )
        try:
            result = subprocess.run(
                command,
                input=task_input,
                cwd=workdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexRunError(
                "timeout",
                f"Codex exceeded the {timeout_seconds} second timeout",
            ) from exc
        except OSError as exc:
            raise CodexRunError("process_start_failed", str(exc)) from exc

        if result.returncode != 0:
            raise CodexRunError("codex_failed", _error_detail(result))

        try:
            response = json.loads(Path(output_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CodexRunError("invalid_response", str(exc)) from exc
        content = response.get("content") if isinstance(response, dict) else None
        if not isinstance(content, str):
            raise CodexRunError("invalid_response", "response content is not a string")
        return content
