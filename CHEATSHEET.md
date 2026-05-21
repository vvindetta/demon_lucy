# Lucy Args Cheatsheet

Short reference for the current argument names. Booleans are `store_true`: write
the flag to enable it, do not pass `true` or `false`.

## Style

Use:

```text
--module-action-detail value
```

## Daemon Args

Used by `main_daemon.py` and config files.

| Arg | Type | Meaning |
|---|---:|---|
| `--sys-config-path` | `str` | Path to the config file. Default: `config.txt`. |
| `--sys-log-level` | `str` | Logging level: `debug`, `info`, `warning`, `error`, `critical`. |
| `--sys-log-format` | `str` | Python logging format string. |
| `--sys-watch-paths` | `str[]` | Directories watched recursively by the daemon. |
| `--sys-opened-event-cooldown-seconds` | `int` | Per-file cooldown for `opened` filesystem events. |
| `--sys-disable-opened-events` | `bool` | Ignore `opened` events completely. Useful on Termux. |
| `--sys-notification-provider` | `str` | Notification backend: `auto`, `termuxapi`, `desktop`, or `disable`. |
| `--sys-notification-min-interval-seconds` | `float` | Minimum interval before repeating the same notification. |
| `--sys-notification-error-backoff-base-seconds` | `float` | Base interval for exponential backoff of error notifications. |
| `--sys-notification-error-backoff-max-seconds` | `float` | Maximum interval cap for exponential backoff of error notifications. |
| `--sys-notification-error-burst-limit` | `int` | Maximum number of error notifications inside one burst window. |
| `--sys-notification-error-burst-window-seconds` | `float` | Burst window length used for global error notification limiting. |
| `--sys-ignore-paths` | `str[]` | Paths where modules should not run. |

Example:

```text
python3 main_daemon.py --sys-watch-paths ~/Notes --sys-disable-opened-events
```

## One-shot Args

Used by `main_oneshot.py`.

| Arg | Type | Meaning |
|---|---:|---|
| `--oneshot-event` | `str` | Synthetic event to run once: `created`, `modified`, `moved`, `deleted`, `opened`. |
| `--oneshot-paths` | `str[]` | File or directory paths to process. Required unless event is `moved`. |
| `--oneshot-move-src-path` | `str` | Source path for a synthetic `moved` event. |
| `--oneshot-move-dest-path` | `str` | Destination path for a synthetic `moved` event. |
| `--oneshot-modules` | `str[]` | Run only these module names. |

Examples:

```text
python3 main_oneshot.py --oneshot-event modified --oneshot-paths ~/Notes
python3 main_oneshot.py --oneshot-event modified --oneshot-paths ~/Notes --oneshot-modules git
python3 main_oneshot.py --oneshot-event moved --oneshot-move-src-path old.md --oneshot-move-dest-path new.md
```

## Module Manager Args

These can be set globally or inside notes.

| Arg | Type | Meaning |
|---|---:|---|
| `--modules-force-enable` | `str[]` | Run named modules even if they are disabled. |
| `--modules-disable` | `str[]` | Skip named modules. Can be overridden by `--modules-force-enable`. |
| `--modules-priority` | `str[]` | Override module order. Format: `name=int`. Lower number runs earlier. |
| `--sys-parse-note-first-line-only` | `bool` | Parse flags only from the first line of a note. Faster, but ignores lower lines. |
| `--sys-notification-provider` | `str` | Override notification backend for modules (`auto`, `termuxapi`, `desktop`, `disable`). |
| `--sys-notification-min-interval-seconds` | `float` | Override module notification throttle interval. |
| `--sys-notification-error-backoff-base-seconds` | `float` | Override base interval for exponential backoff of error notifications. |
| `--sys-notification-error-backoff-max-seconds` | `float` | Override max interval cap for exponential backoff of error notifications. |
| `--sys-notification-error-burst-limit` | `int` | Override max number of error notifications in one burst window. |
| `--sys-notification-error-burst-window-seconds` | `float` | Override burst window length for global error notification limiting. |
| `--sys-ignore-paths` | `str[]` | Override ignored paths for module execution. |

Example:

```text
--modules-disable git status
--modules-force-enable git
--modules-priority banner=5 renamer=20 git=50
```


## Sys Commands

| Arg | Type | Meaning |
|---|---:|---|
| `--mods` | `bool` | Print loaded modules and priorities. |
| `--ping` | `bool` | Rewrite command line to `++pong!`. |
| `--config` | `bool` | Print config values that differ from defaults. |
| `--man` | `str[]` | Print manual text for a module or flag. |
| `--help` | `bool` | Print Sys module command help. |
| `--event` | `bool` | Print current filesystem event details. |

Examples:

```text
--man git
--man --git-sync-on-opened-disable
```


## Banner

| Arg | Type | Meaning |
|---|---:|---|
| `--banner` | `str` | Insert ASCII banner text. Value `date` inserts today's date. |
| `--banner-separator` | `str` | Separator line used before a banner inserted at file start. |

## Renamer

| Arg | Type | Meaning |
|---|---:|---|
| `--rename` | `str` | Rename the current file to the given name. |
| `--rename-auto` | `bool` | On create, rename `t`/`txt` to `DD-MM.txt` and `m`/`md` to `DD-MM.md`. |

## Formatter

| Arg | Type | Meaning |
|---|---:|---|
| `--fmt-todo` | `bool` | Convert `- task` list items into `- [ ] task`. |
| `--fmt-blank` | `str[]` | Add blank lines at top/bottom. Values: `up`, `down`, `both`, optional count. |

Examples:

```text
--fmt-todo
--fmt-blank up
--fmt-blank down 20
--fmt-blank both 12
```

## Status

| Arg | Type | Meaning |
|---|---:|---|
| `--status` | `str[]` | Add filename status tokens: `date`, `time`, `time-with-seconds`, `git`, `git update`. |
| `--status-banner` | `str` | Animated text inserted into the filename status. |
| `--status-banner-speed-milliseconds` | `int` | Animation step duration for `--status-banner`. |
| `--status-banner-max-characters` | `int` | Visible banner width. `0` means unlimited. |
| `--status-prefix` | `str` | Prefix inserted before the first filename status token. |

Examples:

```text
--status date time
--status git update
--status-banner "Working"
```

## Linker

| Arg | Type | Meaning |
|---|---:|---|
| `--linker-root` | `bool` | Create a symlink in the repo root with the current note filename. |
| `--linker-clean-root-symlinks` | `bool` | If `--linker-root` is not set, remove symlinks from the repo root. |
| `--linker-ignore` | `str[]` | Ignore files/links for linker actions (basename or absolute/repo-relative path). |
| `--linker-update-references-on-move` | `bool` | On move/rename, scan markdown files in repo and update links to moved note. |

## Dropdir

| Arg | Type | Meaning |
|---|---:|---|
| `--dropdir-today-clean-paths` | `str[]` | Directories where moved `today-now` files are immediately archived. |
| `--dropdir-today-clean-delay-milliseconds` | `int` | Delay before triggering today cleanup after move-back. |

## Today

| Arg | Type | Meaning |
|---|---:|---|
| `--today-now-path` | `str` | Active note file to archive when stale. Default: `now.md`. |
| `--today-past-path` | `str` | Archive file path. Default: `past.md`. |
| `--today-idle-hours` | `float` | Archive `today-now` when its age is at least this many hours. |
| `--today-past` | `bool` | Force move `today-now` to `today-past` on this event. |
| `--today-force-filesystem-mtime` | `bool` | Use filesystem mtime even inside Git repositories. |


## Cmd Module

The `cmd` module exists but is not enabled in the default module list.

| Arg | Type | Meaning |
|---|---:|---|
| `--cmd` | `str[]` | Command tokens to execute with `shell=False`. |
| `--cmd-timeout-seconds` | `int` | Timeout for each command run. |
| `--cmd-output-max-bytes` | `int` | Max stdout/stderr bytes written back into the note. |
| `--cmd-stream` | `str` | Output policy: `both`, `stdout`, `stderr`, or `none`. |

Example:

```text
--cmd echo hello
--cmd-stream stdout
```

## Git

| Arg | Type | Meaning |
|---|---:|---|
| `--git-commit-message` | `str` | Base commit message. |
| `--git-commit-message-timestamp` | `bool` | Append timestamp to commit message. |
| `--git-commit-message-timestamp-format` | `str` | Python `strftime` format for commit message timestamp. |
| `--git-sync-on-opened-disable` | `bool` | Disable git sync reaction when a repository receives an `opened` event. |
| `--git-push-auto-merge` | `bool` | If push is rejected because remote is ahead, pull/merge and retry push. |
| `--git-upstream-auto-set` | `bool` | Try to set upstream automatically when branch has none. |
| `--git-merge-autoresolve` | `str` | Conflict strategy: `none`, `ours`, `theirs`, `union`, `markers` (commit with conflict markers). |
| `--git-command-timeout-seconds` | `float` | Timeout for git add/status/commit and similar operations. |
| `--git-pull-timeout-seconds` | `float` | Timeout for git pull/merge operations. |
| `--git-network-probe-timeout-seconds` | `float` | Timeout for remote network reachability probe before pull. |
| `--git-pull-offline-error-markers` | `str[]` | Error text markers treated as offline/network pull failures. |
| `--git-push-timeout-seconds` | `float` | Timeout for git push. |
| `--git-sync-retry-window-seconds` | `float` | Total retry window for background git sync retries. `0` disables retries. |
| `--git-sync-retry-backoff-start-seconds` | `float` | Initial retry delay for background git sync retries. |
| `--git-sync-retry-backoff-max-seconds` | `float` | Maximum retry delay cap for background git sync retries. |

Common examples:

```text
--git-merge-autoresolve union
```

## Plasma Sync

| Arg | Type | Meaning |
|---|---:|---|
| `--plasma-widget-path` | `str` | Main Plasma note HTML widget path. Required for plasma sync. |
| `--plasma-bold-widget-path` | `str` | Optional bold-only mirror widget path. |
| `--plasma-markdown-note-path` | `str` | Markdown note path synced with Plasma widget content. Required. |
| `--plasma-css-style` | `bool` | Render with CSS checkbox markers and real `ul/li` output. |
