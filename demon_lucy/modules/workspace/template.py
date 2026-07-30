from __future__ import annotations

import os

from demon_lucy.lib.path import canonical_path

DEFAULT_TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "default_workspace")
REPO_ROOT = canonical_path(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SETUP_TEMPLATE_DIRS = ((os.path.join(REPO_ROOT, "setup-systemd"), "setup-systemd"),)


class WorkspaceTemplate:
    def __init__(self, template_dir: str = DEFAULT_TEMPLATE_DIR):
        self.template_dir = template_dir

    def source_path(self, relative_path: str) -> str:
        return os.path.join(self.template_dir, relative_path)

    @staticmethod
    def default_path_replacements(
        values: dict[str, str],
    ) -> tuple[tuple[str, str], ...]:
        return (
            ("/home/user/Notes/.lucy/config.txt", values["CONFIG_PATH"]),
            ("/home/user/demon_lucy", values["LUCY_HOME"]),
            ("/home/user/Notes", values["WORKSPACE_ROOT"]),
            ("/usr/bin/python3", values["PYTHON_BIN"]),
        )

    @staticmethod
    def render_text(text: str, values: dict[str, str]) -> str:
        rendered = text
        for key, value in values.items():
            token = "{{" + key + "}}"
            if value.endswith("\n"):
                rendered = rendered.replace(token + "\n", value)
            rendered = rendered.replace(token, value)
        for old_value, new_value in WorkspaceTemplate.default_path_replacements(values):
            rendered = rendered.replace(old_value, new_value)
        return rendered

    def render_source_file(self, source_path: str, values: dict[str, str]) -> str:
        with open(source_path, "r", encoding="utf-8") as handle:
            return self.render_text(handle.read(), values)

    def render_file(self, relative_path: str, values: dict[str, str]) -> str:
        return self.render_source_file(self.source_path(relative_path), values)

    @staticmethod
    def write_file_if_missing(
        path: str,
        text: str,
        *,
        executable: bool = False,
    ) -> bool:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "x", encoding="utf-8") as handle:
                handle.write(text)
            if executable and os.name != "nt":
                os.chmod(path, 0o755)
            return True
        except FileExistsError:
            return False

    @staticmethod
    def should_be_executable(relative_path: str) -> bool:
        return False

    def copy_file_if_missing(
        self,
        workspace_root: str,
        source_path: str,
        relative_path: str,
        values: dict[str, str],
    ) -> bool:
        destination = os.path.join(workspace_root, relative_path)
        text = self.render_source_file(source_path, values)
        return self.write_file_if_missing(
            destination,
            text,
            executable=self.should_be_executable(relative_path),
        )

    def source_dirs(self) -> tuple[tuple[str, str], ...]:
        return ((self.template_dir, ""), *SETUP_TEMPLATE_DIRS)

    def copy_files(
        self,
        workspace_root: str,
        values: dict[str, str],
        *,
        skip: set[str] | None = None,
    ) -> dict[str, int]:
        skipped = skip or set()
        changed: dict[str, int] = {}
        for source_dir, destination_prefix in self.source_dirs():
            for root, _dirs, files in os.walk(source_dir):
                for filename in files:
                    source_path = os.path.join(root, filename)
                    relative_path = os.path.relpath(source_path, source_dir)
                    if destination_prefix:
                        relative_path = os.path.join(destination_prefix, relative_path)
                    if relative_path in skipped:
                        continue
                    if self.copy_file_if_missing(
                        workspace_root,
                        source_path,
                        relative_path,
                        values,
                    ):
                        changed[
                            canonical_path(os.path.join(workspace_root, relative_path))
                        ] = 1
        return changed

    def welcome_text(self, values: dict[str, str]) -> str:
        return self.render_file("welcome.md", values)
