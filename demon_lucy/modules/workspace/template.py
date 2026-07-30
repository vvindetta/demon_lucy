from __future__ import annotations

import os

from demon_lucy.lib.path import canonical_path

DEFAULT_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "default_workspace")


class WorkspaceTemplate:
    def __init__(self, template_dir: str = DEFAULT_TEMPLATE_DIR):
        self.template_dir = template_dir

    def source_path(self, relative_path: str) -> str:
        return os.path.join(self.template_dir, relative_path)

    @staticmethod
    def render_text(text: str, values: dict[str, str]) -> str:
        rendered = text
        for key, value in values.items():
            token = "{{" + key + "}}"
            rendered = rendered.replace(token + "\n", value)
            rendered = rendered.replace(token, value)
        return rendered

    def render_file(self, relative_path: str, values: dict[str, str]) -> str:
        with open(self.source_path(relative_path), "r", encoding="utf-8") as handle:
            return self.render_text(handle.read(), values)

    @staticmethod
    def write_file_if_missing(path: str, text: str) -> bool:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "x", encoding="utf-8") as handle:
                handle.write(text)
            return True
        except FileExistsError:
            return False

    def copy_file_if_missing(
        self,
        workspace_root: str,
        relative_path: str,
        values: dict[str, str],
    ) -> bool:
        destination = os.path.join(workspace_root, relative_path)
        text = self.render_file(relative_path, values)
        return self.write_file_if_missing(destination, text)

    def copy_files(
        self,
        workspace_root: str,
        values: dict[str, str],
        *,
        skip: set[str] | None = None,
    ) -> dict[str, int]:
        skipped = skip or set()
        changed: dict[str, int] = {}
        for root, _dirs, files in os.walk(self.template_dir):
            for filename in files:
                source_path = os.path.join(root, filename)
                relative_path = os.path.relpath(source_path, self.template_dir)
                if relative_path in skipped:
                    continue
                if self.copy_file_if_missing(workspace_root, relative_path, values):
                    changed[
                        canonical_path(os.path.join(workspace_root, relative_path))
                    ] = 1
        return changed

    @staticmethod
    def config_summary_lines(config_text: str) -> list[str]:
        lines: list[str] = []
        for raw_line in config_text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            lines.append(line)
        return lines

    def config_summary_text(self, config_text: str) -> str:
        config_lines = self.config_summary_lines(config_text)
        if config_lines:
            return "".join(f"- `{line}`\n" for line in config_lines)
        return "- No non-default config values.\n"

    def welcome_text(self, workspace_root: str, config_text: str) -> str:
        return self.render_file(
            "welcome.md",
            {
                "WORKSPACE_ROOT": workspace_root,
                "CONFIG_SUMMARY": self.config_summary_text(config_text),
            },
        )
