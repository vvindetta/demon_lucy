# Project Map

Demon Lucy is a modular notes manager. Notes are
plain files; Lucy reads Unix-style flags from notes/config/CLI and reacts to
watchdog file events.

## Runtime Flow

- `main_daemon.py`: long-running watcher. Parses startup config, selects modules,
  creates `ModuleManager`, and schedules `FileHandler` for `--sys-watch-paths`.
- `main_oneshot.py`: synthetic single-run entry point for created/modified/moved/
  deleted/opened events. Useful for scripts, systemd timers, and Termux.
- `demon_lucy/runtime.py`: startup template, config migration hook, logging,
  module selection, module include/exclude validation, startup log line.
- `demon_lucy/file_handler.py`: watchdog event filtering, `.git`/dotfile skips,
  opened-event cooldown, generic internal move skips via
  `--sys-ignore-move-paths`, and ignore-count loop prevention after module
  writes.
- `demon_lucy/module_manager.py`: builds the global args template from all loaded
  modules, merges defaults/system config/file flags, sorts by priority, runs
  event handlers, and refreshes registered dynamic blocks afterward.
- `demon_lucy/modules/abstract_module.py`: module contract (`created`,
  `modified`, `moved`, `deleted`, `opened`) plus `Context` and `System`.

## Core Helpers

- `demon_lucy/lib/args/parser.py`: recursive `ArgTemplate` definitions,
  argparse setup, nested parameter normalization, config-file parsing,
  per-note flag parsing, and config/CLI merge helpers.
- `demon_lucy/lib/args/line_edit.py`: reusable arg-segment helpers and flag
  deletion from note lines.
- `demon_lucy/lib/path.py`: path normalization, safe `path_is_inside`
  containment checks, parent marker lookup, Git repo discovery, and `.git`
  file/directory support.
- `demon_lucy/lib/logfmt.py`: structured one-line log records, event ids, event
  path rendering, and ignore-count summaries.
- `demon_lucy/lib/notifications.py`: notifications (`safe_notify`, `notify`)
  and throttled/rare-mode error notification state.
- `demon_lucy/lib/date_sections.py`: archive date values/headers, dated ranges,
  and safe completion of consecutive short date headers.
- `demon_lucy/lib/file_time.py`: shared Git/mtime content timestamps, local
  timestamp formatting, and relative age text.
- `demon_lucy/lib/text_file.py`: newline detection/normalization and atomic
  UTF-8 text replacement with mode preservation.
- `demon_lucy/lib/dynamic_blocks/`: parsing, serialization, and pure-text
  refresh of `--- <arg> begin ---` generated blocks; parameter schemas come
  from the owning module's main `ArgTemplate`.
- `demon_lucy/migrations/`: class-based config migration modules. `__init__.py`
  owns the sync `Migration` interface, config-file migration methods using
  `lib.args.line_edit` arg-segment helpers, and the `MIGRATIONS` registry.
  `runtime.py` checks `is_migration_needed()` and runs needed migrations before
  normal config parsing in daemon and oneshot.

## Library Usage

- Prefer existing helpers from `demon_lucy/lib/` before adding local module
  utilities or ad-hoc parsing/path/arg/notification logic.
- If a needed helper does not exist and the behavior is reusable outside one
  narrow module, add it to the appropriate `demon_lucy/lib/` module first, then
  use it from feature code.
- Keep module code focused on module behavior; shared mechanics belong in
  `demon_lucy/lib/`.
- Do not add pass-through wrapper functions that only rename or forward to
  another helper. Import and call the helper directly, or refactor the shared
  behavior into a real library function when additional policy/logging/state is
  needed.
- Do not make one feature module import another feature module just to share
  mechanics. Move shared state/helpers into `demon_lucy/lib/` first. For
  example, `status` reads Git sync marker/lock state via
  `demon_lucy.lib.git_state`, not `demon_lucy.modules.git`.
- Use absolute imports inside the project, including intra-package imports. For
  example, use `from demon_lucy.modules.archive import notify`, not
  `from . import notify`.

## Modules

- When a module grows beyond one file, make it a package directory under
  `demon_lucy/modules/<name>/`, not a set of sibling files like
  `modules/<name>_helpers.py`. Keep the public module class import stable from
  `demon_lucy.modules.<name>` by exposing the class in `__init__.py`, and put
  focused internals in files such as `module.py`, `config.py`, `paths.py`,
  `storage.py`, `requests.py`, `render.py`, or domain-specific names.
- `modules/sys.py`: in-note debug/manual commands such as `--mods`, `--config`,
  `--man`, `--event`, `--ping`, `--help`.
- `modules/alias/`: in-note aliases for module args. `__init__.py` owns the
  `Alias` module class, rule error logging/notifications, and event handlers;
  `rules.py` owns alias validation/parsing, and `rewrite.py` owns line expansion.
- `modules/banner.py`: inserts pyfiglet banners or date banners at flag lines.
- `modules/renamer.py`: manual `--rename` and create-time `--rename-auto` for
  one-letter scratch files with configurable output format.
- `modules/formatter.py`: TODO checkbox formatting, archive date completion,
  and top/bottom blank padding; dynamic blocks are left untouched.
- `modules/graph/`: dynamic text/Markdown graph blocks for word/regex frequency.
  `__init__.py` owns command-to-block conversion; `params.py` owns the main
  recursive Graph template and shared command/block normalization;
  `dynamic_block.py` owns source safety and renderer registration; `data.py`
  collects dated/Git data; `render.py` builds output.
- `modules/include/`: renders complete UTF-8 files inside notes as tab-indented
  Dynamic Blocks. `__init__.py` owns command conversion and event handlers;
  `params.py` owns the recursive template; `render.py` owns safe source reads.
- `modules/archive/`: archives stale or forced source note content through
  pair/local/global routes. `module.py` owns orchestration and event handlers,
  `requests.py` builds `ArchiveRequest` objects from already-parsed config/note
  values, `paths.py` owns path safety and
  destination resolution, `storage.py` owns no-follow IO and text/file archive
  writes, `clock.py` applies archive timestamp policy through `lib.file_time`,
  and `notify.py` owns archive error/security notifications.
- `modules/linker/`: root symlink creation/cleanup and markdown link updates on
  move/rename. `__init__.py` owns the `Linker` module class and event handlers,
  `root.py` owns root symlink and ignore-selector logic, and `markdown.py` owns
  Markdown link parsing, rewrites, and edited-link target moves.
- `modules/dropdir.py`: moved-file drop-directory workflow that can trigger
  archive cleanup through the `Archive` module.
- `modules/status/`: standalone filename status tokens, banners, animations,
  ticker thread, and Git sync age/status rendering. Status bootstrap scans only
  root `.status` directories directly under configured `--sys-watch-paths`.
  Daemon `FileHandler` uses generic `--sys-ignore-move-paths` to skip internal
  `moved` events inside generated/internal dirs; default is `.status`.
- `modules/cmd.py`: optional local command execution from notes. Not in the
  default module list for security reasons.
- `modules/git/`: Git sync module. `config.py` owns `--git-*` flags; `worker.py`
  owns event batching, commit/pull/push, locks, retries, notifications, and patch
  packet helpers; `operations.py`/`ops/` own lower-level Git/network/conflict
  operations.
- `modules/kdeconnect_sync/`: KDE Connect patch-queue sync. `config.py` owns
  flags; `queue.py` owns queue paths/excludes; `transport.py` owns SFTP mount and
  transfer helpers.
- `modules/plasma_widget/`: Markdown <-> Plasma note widget sync. `config.py`
  owns required paths; `engine.py` plans sync direction; codec/model/mapper files
  convert Markdown, Plasma HTML, and bold-only mirror content.

## Docs, Tests, Setup

- `README.md`: user-facing overview, install, module list, systemd and Termux
  setup.
- `CHEATSHEET.md`: current argument reference and examples.
- `tests/test_*.py`: core runtime, args, manager, file handler, and entry-point
  tests.
- `tests/modules/test_*.py`: module-specific behavior tests.
- `tests/migrations/test_*.py`: migration toolkit tests and one file per
  concrete config migration.
- `setup-systemd/`: user service/timer examples for daemon and oneshot runs.
- `setup-termux/`: Android/Termux daemon and oneshot scripts.
- `agent/`: planning notes, currently Git refactor and KDE Connect sync design.
- `media/`: README/demo assets only.

## Documentation Style

- Module descriptions in `README.md` must be external-facing and short. Keep
  them at the level of the other module list items: what the module does for a
  user, without implementation details, internal ordering, config mechanics, or
  manual-like explanations.
- `CHEATSHEET.md` is a quick command reference, not full documentation. Keep it
  to current flags, compact meanings, and short examples. Do not turn it into a
  module manual or design document.
- In manuals/help text, do not write obvious transition wording such as
  "Replace this command line with". Show the final command/config/example
  directly.

## Fast Search Targets

- Args/templates: `rg "template:|TEMPLATE|DEMON_LUCY_STARTUP_TEMPLATE"`
- Config migrations: `rg "run_config_migrations|migrate\\(" demon_lucy/migrations demon_lucy/runtime.py`
- Config reads: `rg "config\\[" demon_lucy`
- Event handlers: `rg "def (created|modified|moved|deleted|opened)" demon_lucy`
- Logging: `rg "log_record|event.start|module.start|sync_skip" demon_lucy`
- Notifications: `rg "safe_notify" demon_lucy`
- Git sync flow: start in `demon_lucy/modules/git/__init__.py`, then
  `worker.py`, then `operations.py`/`ops/`.
- Plasma sync flow: start in `demon_lucy/modules/plasma_widget/__init__.py`,
  then `engine.py`.
- KDE Connect sync flow: start in `demon_lucy/modules/kdeconnect_sync/__init__.py`,
  then `queue.py` and `transport.py`.

# Args

`ArgTemplate` is keyword-only. Always initialize it with full field names such
as `name=`, `value_type=`, `default=`, `description=`, and `required=`; never
use positional arguments.

## Hard Rules

- Do not add subcommands like `--git pull hours 2`.
- One flag must have one concrete type: `bool`, `str`, `int`, `float`, a
  `StrEnum`, or a real `str[]`. Use `StrEnum` for fixed string domains and
  carry the Enum member through module logic instead of converting it back to
  an untyped string.
- Do not shorten words just to save a few letters: write `hours`.
- `src` and `dest` may be kept for move/source/destination paths.
- `bool` currently works as `store_true`; `true`/`false` values after the flag are not supported.


## Style

Use one consistent format:

`--module-action-detail value`

Frequently used note flags should be short and readable:

- `--rename`
- `--formatter-todo`
- `--formatter-blank`
- `--cmd`
- `--banner`
- `--status`

Rare config/startup flags should be more explicit so their meaning is clear
without reading the manual:

- `--sys-opened-event-cooldown-seconds`
- `--sys-notification-min-interval-seconds`
- `--git-network-probe-timeout-seconds`
- `--git-pull-interval-hours`


## Config Usage

- Reuse the existing typed `config[...]` values from templates.
- Do not re-parse or re-typecast config values in module logic
- Keep defaults only in templates. Do not add fallback defaults in module logic.
- If a new config key is needed, add it to templates first, then read it via
  `config[...]` in code.


## When Renaming Args

When renaming a flag, update all related places at once:

- module/runtime template;
- every `config[...]` access;
- manual/help text;
- README examples;
- `CHEATSHEET.md`;
- tests;


# Tests

- Do not preserve obsolete legacy tests by bending production code around outdated
  behavior.
- If the intended behavior changed and a test no longer describes the current
  contract, rewrite the test to match the current contract.
- Keep backward compatibility only when it is an explicit product requirement,
  not just because an old test asserts it.


# Logging

- Use `demon_lucy.lib.logfmt.log_record()` for new structured logs instead of
  ad-hoc formatted strings. Keep records one line: `action | key=value | ...`.
- Treat every file event as a block. `FileHandler`/`main_oneshot.py` create the
  event id and log `event.start`/`event.done`; module code should preserve that
  `id` through `System.event_id` when it logs work caused by the same event.
- Keep action names stable and searchable: `module.start`, `module.done`,
  `git.push_failed`, `plasma.sync_applied`, `archive.rule_invalid`. Prefer a
  short `reason=...` key over inventing many near-duplicate action names.
- Include the identifiers needed to debug without re-running: `id`, `event`,
  `module`, `path`, `src`, `dest`, `repo`, `reason`, `status`, `attempt`,
  `changed_paths`, `changed_events`, and short `error` text when relevant.
- Use `info` for Lucy's normal observability: event/module start and completion,
  expected skips, cooldowns, ignored paths, quiet repo/index locks, busy states,
  real successful actions, sync applied, packets sent, commits made, and
  deliberate fallback behavior. A normal Lucy decision should not require
  `--sys-log-level debug` to see.
- Use `debug` only for deliberately low-level diagnostics that are too noisy for
  normal event reading. `--sys-log-level debug` can also expose third-party
  library logs, so do not depend on debug for routine Lucy messages.
- Use `warning` for failed attempts that are still recoverable by retry/backoff,
  offline/wait states that block this cycle, stale lock cleanup, and unusual
  states worth checking.
- Use `error` for invalid user config/rules, security guard blocks, final
  failures where Lucy could not complete the requested work, failed
  writes/rollbacks, unresolved conflicts, or states that can require manual
  action.
- Do not log noisy "will retry later" conditions as errors. If Lucy can recover
  by itself, keep it info/warning and make the log explain the next state.
- Do not duplicate the same fact at several levels. Log the root cause once with
  useful keys, then keep follow-up symptoms quiet unless they add new data.
- Use `logger.exception(...)` only when the traceback is useful for an unexpected
  exception. For expected subprocess/IO failures, log concise `error=...` text.


# Notifications

- For failures, call `safe_notify(..., use_rare_mode=True)` (default mode).
- Use one stable root-cause key per incident scope (for example per repo/path), not separate keys per command step.
- Do not send multiple notifications for cause + symptoms. Send one consolidated error notification with the root cause.
- Key naming should be domain-level, for example `git-sync:<repo_root>` or
  `archive-rule:<flag>:<reason>:<scope>`, instead of command-level keys like
  `pull-timeout:*` and `push-fail:*` for the same root cause.
- Notify for invalid user config/rules, security guard blocks, failed
  writes/rollbacks, unresolved conflicts, and final failures that require manual
  action.
- Do not notify on transient internal retry states such as `index.lock`, repo busy/lock contention,
  temporary backoff, or "will retry later" conditions. Log them without popup and keep retrying.
- Notify only when the operation reached a final critical failure state or requires user action,
  not when Demon Lucy is still able to recover on its own.
- Notification backend delivery failures must be logged as `notification.failed`
  with `provider`, `reason`, and short `error` text when available. Do not let
  desktop/Termux backend failures disappear silently.
