# Project Map

Demon Lucy is a modular notes manager. Notes are
plain files; Lucy reads Unix-style flags from notes/config/CLI and reacts to
watchdog file events.

## Runtime Flow

- `main_daemon.py`: long-running watcher. Parses startup config, selects modules,
  creates `ModuleManager`, and schedules `FileHandler` for `--sys-watch-paths`.
- `main_oneshot.py`: synthetic single-run entry point for created/modified/moved/
  deleted/opened events. Useful for scripts, systemd timers, and Termux.
- `demon_lucy/runtime.py`: startup template, logging, module selection, module
  include/exclude validation, startup log line.
- `demon_lucy/file_handler.py`: watchdog event filtering, `.git`/dotfile skips,
  opened-event cooldown, ignore-count loop prevention after module writes.
- `demon_lucy/module_manager.py`: builds the global args template from all loaded
  modules, merges defaults/system config/file flags, sorts by priority, and runs
  event handlers.
- `demon_lucy/modules/abstract_module.py`: module contract (`created`,
  `modified`, `moved`, `deleted`, `opened`) plus `Context` and `System`.

## Core Helpers

- `demon_lucy/lib/args.py`: typed flag templates, argparse setup, config-file
  parsing, per-note flag parsing, and flag deletion from note lines.
- `demon_lucy/lib/path.py`: path normalization, parent marker lookup, Git repo
  discovery, and `.git` file/directory support.
- `demon_lucy/lib/__init__.py`: notifications (`safe_notify`, `notify`) and
  throttled/rare-mode error notification state.

## Modules

- `modules/sys.py`: in-note debug/manual commands such as `--mods`, `--config`,
  `--man`, `--event`, `--ping`, `--help`.
- `modules/banner.py`: inserts pyfiglet banners or date banners at flag lines.
- `modules/renamer.py`: manual `--rename` and create-time `--rename-auto`.
- `modules/formatter.py`: TODO checkbox formatting and top/bottom blank padding.
- `modules/archive.py`: moves stale or forced source note content into archive
  destination notes with date headers.
- `modules/linker.py`: root symlink creation/cleanup and markdown link updates on
  move/rename.
- `modules/dropdir.py`: moved-file drop-directory workflow that can trigger
  archive cleanup through the `Archive` module.
- `modules/status/`: filename status tokens, banners, animations, ticker thread,
  and Git sync age/status rendering.
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
- `setup-systemd/`: user service/timer examples for daemon and oneshot runs.
- `setup-termux/`: Android/Termux daemon and oneshot scripts.
- `agent/`: planning notes, currently Git refactor and KDE Connect sync design.
- `media/`: README/demo assets only.

## Fast Search Targets

- Args/templates: `rg "template:|TEMPLATE|DEMON_LUCY_STARTUP_TEMPLATE"`
- Config reads: `rg "config\\[" demon_lucy`
- Event handlers: `rg "def (created|modified|moved|deleted|opened)" demon_lucy`
- Notifications: `rg "safe_notify" demon_lucy`
- Git sync flow: start in `demon_lucy/modules/git/__init__.py`, then
  `worker.py`, then `operations.py`/`ops/`.
- Plasma sync flow: start in `demon_lucy/modules/plasma_widget/__init__.py`,
  then `engine.py`.
- KDE Connect sync flow: start in `demon_lucy/modules/kdeconnect_sync/__init__.py`,
  then `queue.py` and `transport.py`.

# Args

## Hard Rules

- Do not add subcommands like `--git pull hours 2`.
- One flag must have one concrete type: `bool`, `str`, `int`, `float`, or a real
  `str[]`.
- Do not shorten words just to save a few letters: write `hours`.
- `src` and `dest` may be kept for move/source/destination paths.
- `bool` currently works as `store_true`; `true`/`false` values after the flag are not supported.


## Style

Use one consistent format:

`--module-action-detail value`

Frequently used note flags should be short and readable:

- `--rename`
- `--fmt-todo`
- `--fmt-blank`
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


# Notifications

- For failures, call `safe_notify(..., use_rare_mode=True)` (default mode).
- Use one stable root-cause key per incident scope (for example per repo/path), not separate keys per command step.
- Do not send multiple notifications for cause + symptoms. Send one consolidated error notification with the root cause.
- Key naming should be domain-level, for example `git-network:<repo_root>`,
  instead of command-level keys like `pull-timeout:*` and `push-fail:*` for the same network outage.
- Do not notify on transient internal retry states such as `index.lock`, repo busy/lock contention,
  temporary backoff, or "will retry later" conditions. Log them silently and keep retrying.
- Notify only when the operation reached a final critical failure state or requires user action,
  not when Demon Lucy is still able to recover on its own.
