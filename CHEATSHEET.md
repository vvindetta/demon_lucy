# Demon Lucy Args Cheatsheet

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
| `--sys-modules` | `str[]` | Include only these modules by name for both daemon and one-shot runs. |
| `--sys-modules-exclude` | `str[]` | Exclude modules from the included/default module list. |

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

Examples:

```text
python3 main_oneshot.py --oneshot-event modified --oneshot-paths ~/Notes
python3 main_oneshot.py --oneshot-event modified --oneshot-paths ~/Notes --sys-modules git
python3 main_oneshot.py --oneshot-event moved --oneshot-move-src-path old.md --oneshot-move-dest-path new.md
```

## Module Manager Args

These can be set globally or inside notes.

| Arg | Type | Meaning |
|---|---:|---|
| `--modules-priority` | `str[]` | Override module order. Format: `name=int`. Lower number runs earlier. |
| `--sys-notification-provider` | `str` | Override notification backend for modules (`auto`, `termuxapi`, `desktop`, `disable`). |
| `--sys-notification-min-interval-seconds` | `float` | Override module notification throttle interval. |
| `--sys-notification-error-backoff-base-seconds` | `float` | Override base interval for exponential backoff of error notifications. |
| `--sys-notification-error-backoff-max-seconds` | `float` | Override max interval cap for exponential backoff of error notifications. |
| `--sys-notification-error-burst-limit` | `int` | Override max number of error notifications in one burst window. |
| `--sys-notification-error-burst-window-seconds` | `float` | Override burst window length for global error notification limiting. |
| `--sys-ignore-paths` | `str[]` | Override ignored paths for module execution. |

Example:

```text
--sys-modules git status archive
--sys-modules-exclude status
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
| `--banner` | `str` | Insert ASCII banner text. Value `date` inserts archive's date. |
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
| `--status-animation` | `str[]` | Frame list for pass-based animation in filename status. |
| `--status-animation-speed-milliseconds` | `int` | Minimum delay between frame switches for `--status-animation`. |
| `--status-tick-interval-seconds` | `float` | Base ticker interval for status updates. |
| `--status-git-fast-tick-interval-seconds` | `float` | Fast ticker interval used for recent `git update` status and Sync-prefix loading animation. Default: `0.5`. |
| `--status-git-fast-tick-window-seconds` | `float` | Duration of fast ticker mode after `git update` activity. |
| `--status-opened-events-disable` | `bool` | Disable status module processing for `opened` events. |

Examples:

```text
--status date time
--status git update
--status-banner "Working"
--status-animation "loading" "loading." "loading.."
--status-animation-speed-milliseconds 800
--status-opened-events-disable
```

## Linker

| Arg | Type | Meaning |
|---|---:|---|
| `--linker-root` | `bool` | Create a symlink in the repo root with the current note filename. |
| `--linker-auto-clean-root-links` | `bool` | If `--linker-root` is not set, remove symlinks from the repo root. |
| `--linker-ignore` | `str[]` | Ignore files/links for linker actions (basename or absolute/repo-relative path). |
| `--linker-auto-update-md-links` | `bool` | On move/rename, scan markdown files in repo and update links to moved note. |

## Dropdir

| Arg | Type | Meaning |
|---|---:|---|
| `--dropdir-archive-clean-paths` | `str[]` | Directories where moved archive source files are immediately archived. |
| `--dropdir-archive-clean-delay-milliseconds` | `int` | Delay before triggering archive cleanup after move-back. |

## Archive

| Arg | Type | Meaning |
|---|---:|---|
| `--archive` | `bool` | Force archive move for this event. |
| `--archive-pair` | `str[]` | Archive pair: `<src> <dest> [idle_hours_int]`. |
| `--archive-default-dest-path` | `str` | Fallback destination file when `--archive-pair` is missing. |
| `--archive-idle-hours` | `float` | Archive source when its age is at least this many hours. |
| `--archive-date-prefix` | `str` | Text before archive date in history header. Default: `-- `. |
| `--archive-date-suffix` | `str` | Text after archive date in history header. Default: empty string. |
| `--archive-force-filesystem-mtime` | `bool` | Use filesystem mtime even inside Git repositories. |


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

## KDE Connect Sync

| Arg | Type | Meaning |
|---|---:|---|
| `--kdeconnect-sync` | `bool` | Enable KDE Connect patch sync for edit events (`created`, `modified`, `moved`, `deleted`). |
| `--kdeconnect-device-id` | `str` | KDE Connect device id used for mount/sync operations. |
| `--kdeconnect-remote-root` | `str` | Phone repository root path (for example `/storage/emulated/0/Notes`). |
| `--kdeconnect-patch-queue-dir` | `str` | Project-local patch queue directory. |
| `--kdeconnect-patch-coalesce-milliseconds` | `int` | Coalesce window for rapid edit events before building one patch packet. |
| `--kdeconnect-patch-retry-seconds` | `float` | Retry delay for failed phone transfer attempts. |
| `--kdeconnect-patch-max-retries` | `int` | Maximum transfer retries per packet before giving up. |
| `--kdeconnect-binary-fallback-enabled` | `bool` | Reserved flag for future binary fallback mode. |
| `--kdeconnect-command-timeout-seconds` | `float` | Timeout for `kdeconnect-cli` commands. |
| `--kdeconnect-mount-retry-seconds` | `float` | Delay between mount retries when device is temporarily unavailable. |
| `--kdeconnect-dry-run` | `bool` | Build patch packets without transferring to phone. |

## Plasma Widget

| Arg | Type | Meaning |
|---|---:|---|
| `--plasma-widget-path` | `str` | Main Plasma note HTML widget path. Required for plasma widget sync. |
| `--plasma-bold-widget-path` | `str` | Optional bold-only mirror widget path. |
| `--plasma-markdown-note-path` | `str` | Markdown note path synced with Plasma widget content. Required. |
| `--plasma-css-style` | `bool` | Render with CSS checkbox markers and real `ul/li` output. |
