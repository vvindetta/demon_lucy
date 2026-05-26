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
- `ARGS_CHEATSHEET.md`;
- tests;


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
