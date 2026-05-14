from __future__ import annotations

import os
from typing import Optional

from lucy_notes_manager.lib.args import Template
from lucy_notes_manager.lib.path import find_parent_with
from lucy_notes_manager.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class Linker(AbstractModule):
    name: str = "linker"
    priority: int = 22

    template: Template = [
        (
            "--linker-top",
            bool,
            False,
            "Create symlink in repository root with the same filename as current note.",
        ),
        (
            "--linker-auto-clean-up",
            bool,
            False,
            "If enabled and --linker-top is not set, delete all symlinks from repository root.",
        ),
    ]

    def _create_top_link(self, *, source_path: str, repo_root: str) -> Optional[IgnoreMap]:
        link_path = os.path.abspath(os.path.join(repo_root, os.path.basename(source_path)))
        if link_path == source_path:
            return None

        if os.path.islink(link_path):
            if os.path.realpath(link_path) == os.path.realpath(source_path):
                return None
            return None

        if os.path.exists(link_path):
            return None

        try:
            link_target = os.path.relpath(source_path, repo_root)
        except ValueError:
            link_target = source_path

        try:
            os.symlink(link_target, link_path)
        except OSError:
            return None

        return {link_path: 1}

    def _cleanup_top_links(self, *, repo_root: str) -> Optional[IgnoreMap]:
        deleted: IgnoreMap = {}
        try:
            entries = os.listdir(repo_root)
        except OSError:
            return None

        for name in entries:
            if name == ".git":
                continue
            abs_path = os.path.abspath(os.path.join(repo_root, name))
            if not os.path.islink(abs_path):
                continue

            try:
                os.unlink(abs_path)
            except OSError:
                continue

            deleted[abs_path] = 1

        return deleted or None

    def _apply(self, *, path: str, config: dict) -> Optional[IgnoreMap]:
        use_link_top = bool(config.get("linker_top"))
        auto_cleanup = bool(config.get("linker_auto_clean_up"))

        if not use_link_top and not auto_cleanup:
            return None

        source_path = os.path.abspath(path)
        if os.path.isdir(source_path):
            return None

        repo_root = find_parent_with(source_path, ".git")
        if not repo_root:
            return None

        if use_link_top:
            return self._create_top_link(source_path=source_path, repo_root=repo_root)

        if auto_cleanup:
            return self._cleanup_top_links(repo_root=repo_root)

        return None

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply(path=ctx.path, config=ctx.config)
