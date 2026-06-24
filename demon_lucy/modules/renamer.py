import os
from datetime import datetime
from typing import Iterator, Optional

from demon_lucy.lib.args.parser import Template
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    IgnoreMap,
    System,
)


class Renamer(AbstractModule):
    name: str = "renamer"
    priority: int = 20

    template: Template = [
        ("--rename", str, None, "Rename file. Example: --rename new_name.md.", False),
        (
            "--rename-auto",
            bool,
            False,  # IMPORTANT: for your argparse bool handling, default is a bool, not [False]
            "On create, rename any one-letter scratch filename using --rename-auto-format.",
            False,
        ),
        (
            "--rename-auto-format",
            str,
            "md",
            "Auto rename extension. Default: md. Examples: txt, md, org.",
            False,
        ),
    ]

    def _is_auto_source_name(self, path: str) -> bool:
        base = os.path.basename(path)
        stem, _ext = os.path.splitext(base)
        name = (stem or base).strip()
        return len(name) == 1 and name.isalpha()

    def _render_auto_name(self, *, config: dict, now: datetime) -> Optional[str]:
        raw_extension = str(config["rename_auto_format"]).strip().lstrip(".")
        if not raw_extension:
            return None
        if "%" in raw_extension:
            return None
        if os.path.basename(raw_extension) != raw_extension:
            return None
        if "\\" in raw_extension:
            return None
        if raw_extension in (".", ".."):
            return None

        return f"{now.strftime('%d-%m')}.{raw_extension}"

    def _with_collision_suffix(self, *, name: str, suffix: str) -> str:
        stem, ext = os.path.splitext(name)
        return f"{stem}-{suffix}{ext}"

    def _auto_name_candidates(self, *, base_name: str, now: datetime) -> Iterator[str]:
        yield base_name
        yield self._with_collision_suffix(name=base_name, suffix=now.strftime("%H%M"))
        yield self._with_collision_suffix(name=base_name, suffix=now.strftime("%H%M%S"))
        yield self._with_collision_suffix(
            name=base_name,
            suffix=now.strftime("%H%M%S-%f"),
        )

        second_prefix = now.strftime("%H%M%S")
        for counter in range(1, 10000):
            yield self._with_collision_suffix(
                name=base_name,
                suffix=f"{second_prefix}-{counter:03d}",
            )

    def _apply_manual(self, *, path: str, config: dict) -> Optional[IgnoreMap]:
        if not config["rename"] or not config["rename"].strip():
            return None

        old_path = path
        if os.path.isdir(old_path):
            return None

        dir_path = os.path.dirname(old_path)
        new_path = os.path.abspath(os.path.join(dir_path, config["rename"].strip()))

        if old_path == new_path:
            return None
        if os.path.exists(new_path):
            return None

        try:
            os.rename(old_path, new_path)
            return {old_path: 1, new_path: 1}
        except (FileNotFoundError, OSError):
            return None

    def _apply_auto_on_create(self, *, path: str, config: dict) -> Optional[IgnoreMap]:
        if not config["rename_auto"]:
            return None

        old_path = path
        if os.path.isdir(old_path):
            return None

        if not self._is_auto_source_name(old_path):
            return None

        now = datetime.now()
        new_name = self._render_auto_name(config=config, now=now)
        if not new_name:
            return None

        dir_path = os.path.dirname(old_path)
        for candidate in self._auto_name_candidates(base_name=new_name, now=now):
            new_path = os.path.abspath(os.path.join(dir_path, candidate))
            if old_path == new_path:
                return None
            if os.path.exists(new_path):
                continue

            try:
                os.rename(old_path, new_path)
                return {old_path: 1, new_path: 1}
            except FileNotFoundError:
                return None
            except OSError:
                continue
        return None

    def created(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        # manual rename has priority
        changed = self._apply_manual(path=ctx.path, config=ctx.config)
        if changed:
            return changed
        return self._apply_auto_on_create(path=ctx.path, config=ctx.config)

    def modified(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply_manual(path=ctx.path, config=ctx.config)

    def moved(self, ctx: Context, system: System) -> Optional[IgnoreMap]:
        return self._apply_manual(path=ctx.path, config=ctx.config)
