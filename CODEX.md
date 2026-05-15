# Module Authoring Rules (Lucy Notes Daemon)

These rules are mandatory for every module in `lucy_notes_manager/modules`.

## 1) Module contract
- Inherit from `AbstractModule`.
- Define:
  - `name: str` (unique),
  - `priority: int` (lower runs earlier),
  - `template: Template`.
- Implement only event handlers you actually need:
  - `on_created`, `on_modified`, `on_moved`, `on_deleted`, `on_opened`.

## 2) Template is the only source of defaults and types
- `template` defines both type and default.
- Do not duplicate defaults in runtime code.
- Do not add fallback defaults in module code.
- Read values directly from config by key:
  - `ctx.config["key"]` / `config["key"]`
- Do not use:
  - `config.get("key", default)`
  - casts over config values (`int(...)`, `float(...)`, `bool(...)`, `str(...)`)

## 3) Argument shape
- Use scalar defaults for scalar args (`str`, `int`, `float`, `bool`) when one value is expected.
- Use `[]` default only for truly multi-value args.
- Keep runtime code aligned with template shape (scalar stays scalar, list stays list).

## 4) Return value and watcher-loop safety
- Return `None` if nothing changed.
- Return `IgnoreMap` (`{abs_path: write_count}`) if files changed.
- Include every changed file in `IgnoreMap` to avoid event feedback loops.

## 5) File I/O rules
- Use UTF-8 for text I/O unless a module has a strict reason not to.
- Do not rewrite unrelated content.
- Prefer deterministic and idempotent transforms.

## 6) External command safety
- If running subprocesses:
  - `shell=False`
  - explicit timeout
  - graceful error handling (do not crash daemon loop)

## 7) Reuse and duplication
- Do not create thin wrappers/proxies without clear value.
- Move logic to `/lib` only when it is truly shared across modules.

## 8) Tests
- Add or update tests in `tests/modules/test_<module>.py`.
- Cover:
  - happy path,
  - no-op path,
  - edge/error path,
  - correct `IgnoreMap` behavior.
