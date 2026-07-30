import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from demon_lucy.lib.args.models import KnownArg, Template
from demon_lucy.modules.abstract_module import (
    AbstractModule,
    Context,
    ModuleResult,
    System,
)


class Renamer(AbstractModule):
    name: str = "renamer"
    priority: int = 20

    template: Template = [
        KnownArg(
            name="rename",
            value_type=str,
            default=None,
            description="Rename file. Example: --rename new_name.md.",
        ),
        KnownArg(
            name="rename-auto",
            value_type=bool,
            default=False,  # IMPORTANT: for your argparse bool handling, default is a bool, not [False]
            description="On create, add default extension to extensionless files and rename one-letter scratch filenames using --rename-auto-format.",
        ),
        KnownArg(
            name="rename-auto-format",
            value_type=str,
            default="md",
            description="Auto rename extension. Default: md. Examples: txt, md, org.",
        ),
    ]

    def _is_auto_source_name(self, path: str) -> bool:
        base = os.path.basename(path)
        stem, _ext = os.path.splitext(base)
        name = (stem or base).strip()
        return len(name) == 1 and name.isalpha()

    def _auto_extension(self, value: str) -> Optional[str]:
        raw_extension = value.strip().lstrip(".")
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

        return raw_extension

    def _render_auto_name(self, *, extension: str, now: datetime) -> Optional[str]:
        extension = self._auto_extension(extension)
        if extension is None:
            return None
        return f"{now.strftime('%d-%m')}.{extension}"

    def _render_missing_extension_name(
        self,
        *,
        path: str,
        extension: str,
    ) -> str | None:
        extension = self._auto_extension(extension)
        if extension is None:
            return None
        return f"{os.path.basename(path)}.{extension}"

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

    def _apply_manual(
        self,
        *,
        path: str,
        new_name: str | None,
    ) -> tuple[str, dict[str, int]] | None:
        if not new_name:
            return None
        new_name = new_name.strip()
        if not new_name:
            return None

        old_path = path
        if os.path.isdir(old_path):
            return None

        dir_path = os.path.dirname(old_path)
        new_path = os.path.abspath(os.path.join(dir_path, new_name))

        if old_path == new_path:
            return None
        if os.path.exists(new_path):
            return None

        try:
            os.rename(old_path, new_path)
            return new_path, {old_path: 1, new_path: 1}
        except (FileNotFoundError, OSError):
            return None

    def _apply_auto_on_create(
        self,
        *,
        path: str,
        enabled: bool,
        extension: str,
    ) -> tuple[str, dict[str, int]] | None:
        if not enabled:
            return None

        old_path = path
        if os.path.isdir(old_path):
            return None

        now = datetime.now()
        if not Path(old_path).suffix:
            new_name = self._render_missing_extension_name(
                path=old_path,
                extension=extension,
            )
        elif self._is_auto_source_name(old_path):
            new_name = self._render_auto_name(extension=extension, now=now)
        else:
            return None

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
                return new_path, {old_path: 1, new_path: 1}
            except FileNotFoundError:
                return None
            except OSError:
                continue
        return None

    def created(self, ctx: Context, system: System) -> ModuleResult | None:
        renamed = self._apply_manual(
            path=ctx.path,
            new_name=ctx.args.require("rename").value,
        )
        if renamed is None:
            renamed = self._apply_auto_on_create(
                path=ctx.path,
                enabled=ctx.args.require("rename-auto").value,
                extension=ctx.args.require("rename-auto-format").value,
            )
        if renamed is None:
            return None
        new_path, changed = renamed
        return ModuleResult(
            context=replace(ctx, path=new_path),
            changed=changed,
        )

    def modified(self, ctx: Context, system: System) -> ModuleResult | None:
        renamed = self._apply_manual(
            path=ctx.path,
            new_name=ctx.args.require("rename").value,
        )
        if renamed is None:
            return None
        new_path, changed = renamed
        return ModuleResult(
            context=replace(ctx, path=new_path),
            changed=changed,
        )

    def moved(self, ctx: Context, system: System) -> ModuleResult | None:
        renamed = self._apply_manual(
            path=ctx.path,
            new_name=ctx.args.require("rename").value,
        )
        if renamed is None:
            return None
        new_path, changed = renamed
        return ModuleResult(
            context=replace(ctx, path=new_path),
            changed=changed,
        )
