# Demon Lucy Args Cheatsheet

Short reference for the current argument names. Booleans are `store_true`: write
the flag to enable it, do not pass `true` or `false`.

## Style

Use:

```text
--module-action-detail value
```

## Runtime/System Args

Used by `main_daemon.py`, `main_oneshot.py`, and config files. Module code reads
these values from the resolved `config[...]`.

| Arg | Type | Meaning |
|---|---:|---|
| `--sys-config-path` | `str` | Path to the config file. Default: `config.txt`. |
| `--sys-log-level` | `str` | Logging level: `debug`, `info`, `warning`, `error`, `critical`. `info` shows normal Lucy event decisions; `debug` is for low-level diagnostics and can include library noise. |
| `--sys-log-format` | `str` | Python logging format string. |
| `--sys-watch-paths` | `str[]` | Directories watched recursively by the daemon. |
| `--sys-opened-event-cooldown-seconds` | `int` | Per-file cooldown for `opened` filesystem events. |
| `--sys-disable-opened-events` | `bool` | Ignore `opened` events completely. Useful on Termux. |
| `--sys-dynamic-block-hide-allowed-values` | `bool` | Hide allowed parameter values in newly created dynamic blocks. |
| `--sys-notification-provider` | `str` | Notification backend: `auto`, `termuxapi`, `desktop`, or `disable`. Failed delivery attempts are logged as `notification.failed`. |
| `--sys-notification-min-interval-seconds` | `float` | Minimum interval before repeating the same notification. |
| `--sys-notification-error-backoff-base-seconds` | `float` | Base interval for exponential backoff of error notifications. |
| `--sys-notification-error-backoff-max-seconds` | `float` | Maximum interval cap for exponential backoff of error notifications. |
| `--sys-notification-error-burst-limit` | `int` | Maximum number of error notifications inside one burst window. |
| `--sys-notification-error-burst-window-seconds` | `float` | Burst window length used for global error notification limiting. |
| `--sys-ignore-paths` | `str[]` | Paths where modules should not run. |
| `--sys-ignore-move-paths` | `str[]` | Directories where internal `moved` events are skipped before module execution. Relative paths are resolved under each watched root. Default: `.status`. |
| `--sys-git-repo-lock-wait-timeout-seconds` | `float` | Maximum wait for Lucy's shared Git repo lock before skipping this cycle. |
| `--sys-git-repo-lock-retry-sleep-seconds` | `float` | Delay between attempts to acquire Lucy's shared Git repo lock. |
| `--sys-git-repo-lock-stale-seconds` | `float` | Age after which Lucy's shared Git repo lock is treated as stale. |
| `--sys-modules` | `str[]` | Include only these modules by name for both daemon and one-shot runs. |
| `--sys-modules-exclude` | `str[]` | Exclude modules from the selected module list. |

Example:

```text
python3 main_daemon.py --sys-watch-paths ~/Notes --sys-disable-opened-events
--sys-notification-provider desktop
--sys-ignore-paths ~/.cache ~/Notes/private
--sys-ignore-move-paths .status
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

These are parsed before per-file module runs, so set them in config/startup args.
Runtime/system args such as notifications and ignored paths are inherited from
the startup config.

| Arg | Type | Meaning |
|---|---:|---|
| `--sys-modules-priority` | `str[]` | Override module order. Format: `name=int`. Lower number runs earlier. |

Example:

```text
--sys-modules git status archive
--sys-modules-exclude status
--sys-modules-priority banner=5 renamer=20 git=50
```


## Alias Module

Defines aliases for module args.
System args (`--sys-*`) and `--cmd` are not aliased.

| Arg | Type | Meaning |
|---|---:|---|
| `--alias` | `str[]` | Alias in `name=expansion` format. Use `{args}` to pass values after the alias into the expansion. |
| `--alias-dry-run` | `bool` | Log alias rewrites without changing files. |

Examples:

```text
--alias "b=--banner {args}" "todo=--formatter-todo" "rn=--rename {args}"
```

Then a note can use:

```text
--b "Daily notes"
--todo
--rn daily.md
```


## Sys Commands

| Arg | Type | Meaning |
|---|---:|---|
| `--neofetch` | `bool` | Print Demon Lucy runtime information. |
| `--mods` | `bool` | Print loaded modules and priorities. |
| `--ping` | `bool` | Rewrite command line to `++pong!`. |
| `--config` | `bool` | Print config values that differ from defaults. |
| `--man` | `str[]` | Print manual text for a module or flag. |
| `--help` | `bool` | Print Sys module command help. |
| `--event` | `bool` | Print current filesystem event details. |

Examples:

```text
--neofetch
--man git
--man sys
--man --git-sync-on-opened-disable
```


## Workspace

| Arg | Type | Meaning |
|---|---:|---|
| `--workspace-init` | `str` | Initialize a Lucy workspace at the given directory path. |

Initializes a default notes workspace.

Example:

```text
--workspace-init ~/Notes
```


## Banner

| Arg | Type | Meaning |
|---|---:|---|
| `--banner` | `str[]` | Insert ASCII banner text. Value `date` inserts today's date. |
| `--banner-separator` | `str` | Separator line used before a banner inserted at file start. |

## Renamer

| Arg | Type | Meaning |
|---|---:|---|
| `--rename` | `str` | Rename the current file to the given name. |
| `--rename-auto` | `bool` | On create, rename any one-letter scratch filename to a dated filename. |
| `--rename-auto-format` | `str` | Auto rename output extension. Default: `md`. 

## Linker

| Arg | Type | Meaning |
|---|---:|---|
| `--linker-root` | `bool` | Create a root link; Windows falls back to a hard link when needed. |
| `--linker-auto-clean-root-links` | `bool` | If `--linker-root` is not set, remove managed root links. |
| `--linker-ignore` | `str[]` | Ignore files/links for linker actions (basename or absolute/repo-relative path). |
| `--linker-auto-update-md-links` | `bool` | On move/rename, scan markdown files in repo and update links to moved note. |

## Graph

| Arg | Type | Meaning |
|---|---:|---|
| `--graph` | `str[]` | Build a text graph for a literal search in a file: `file pattern [week\|month\|year\|all]`. Default: `year`. |
| `--graph-regex` | `str[]` | Build a text graph for a regex search in a file: `file regex [week\|month\|year\|all]`. Default: `year`. |

Example:

```text
--graph past.md sleep week
--graph-regex past.md "\\bsleep\\b|slept|nap" month
```

Commands become dynamic `--- graph begin ---` / `--- graph-regex begin ---`
blocks. Set `view` inside a block to `ascii` (default) or `md` to change its
generated body. Allowed values are shown beside
Enum fields by default; `--sys-dynamic-block-hide-allowed-values` hides them in
newly created blocks.

## Include

| Arg | Type | Meaning |
|---|---:|---|
| `--include` | `str[]` | Render a complete file inside an indented dynamic block: `file`. |
| `--include-find` | `str[]` | Collect paragraphs starting with a keyword from a file or directory: `source keyword`. |
| `--include-depth` | `int` | Maximum nested include render depth. Default: `3`. |

```text
--include-depth 3
--include shared/project.md
--include-find notes "tasks:"
```

## Archive

| Arg | Type | Meaning |
|---|---:|---|
| `--archive` | `bool` | Force archive using the first available route: configured pair, local `.archive/`, then global destination. |
| `--archive-pair` | `str[]` | Force archive through the configured `--archive-auto-pair` rule. Optional value: `text` or `file`. |
| `--archive-local` | `str[]` | Force archive the current file beside itself. Optional value: `text` or `file`. Text mode appends to `.archive/archive.md` when `.archive/` exists, otherwise to `archive.md`; file mode creates `.archive/YYYY-MM-DD---name.md`. |
| `--archive-global` | `str[]` | Force archive the current file into the global destination. Optional value: `text` or `file`. |
| `--archive-auto-pair` | `str[]` | Automatic pair rule: `<src> <dest> [idle_hours] [text\|file]`. In text mode `dest` is an archive file; in file mode `dest` is an archive directory. |
| `--archive-auto-local` | `str[]` | Automatic local rule: `<src> [idle_hours] [text\|file]`. Archives one configured source beside itself. |
| `--archive-auto-global` | `str[]` | Automatic global rule: `<src> [idle_hours] [text\|file]`. Archives one configured source into the global destination. |
| `--archive-default-mode` | `str` | Default archive output mode for rules without explicit mode: `text` or `file`. Default: `text`. |
| `--archive-global-dest-path` | `str` | Global archive destination. In text mode this is a file path; in file mode this is a directory path. If empty, text mode uses `archive.md` at the Git repo root and file mode uses `.archive/` at the Git repo root. |
| `--archive-idle-hours` | `float` | Archive source when its age is at least this many hours. |
| `--archive-date-prefix` | `str` | Text before archive date in history header. Header date uses the latest Git commit date for the source file when available, otherwise today's date. Default: `--- `. |
| `--archive-date-suffix` | `str` | Text after archive date in history header. Default: empty string. |
| `--archive-force-filesystem-mtime` | `bool` | Use filesystem mtime even inside Git repositories. |

Archive paths may be relative to the event/source directory, or absolute inside
the allowed root. `~` and relative `..` are rejected; resolved paths must stay
inside the Git repo root, or inside the current note directory outside Git. In
daemon runs with `--sys-watch-paths`, the canonical event path must still be
inside a watched root. The active config file path is never archived or used as
an archive destination.

## Formatter

| Arg | Type | Meaning |
|---|---:|---|
| `--formatter-todo` | `bool` | Convert `- task` list items into `- [ ] task`. |
| `--formatter-blank` | `str[]` | Add blank lines at top/bottom. Values: `up`, `down`, `both`, optional count. |
| `--formatter-date` | `bool` | Complete the next archive date written as `--- day`. |
| `--formatter-complete-args` | `bool` | Complete arguments to their longest shared prefix. |

Examples:

```text
--formatter-todo
--formatter-date
--formatter-complete-args
--formatter-blank up
--formatter-blank down 20
--formatter-blank both 12
```

## Git

| Arg | Type | Meaning |
|---|---:|---|
| `--git-commit-message` | `str` | Base commit message. |
| `--git-commit-message-timestamp` | `bool` | Append timestamp to commit message. |
| `--git-commit-message-timestamp-format` | `str` | Python `strftime` format for commit message timestamp. |
| `--git-commit-message-style` | `str` | Commit message style: `detailed` adds a body with staged file actions; `compact` uses subject only. |
| `--git-commit-message-max-subject-files` | `int` | Maximum changed files named directly in the subject before switching to counts. |
| `--git-commit-message-max-body-files` | `int` | Maximum changed files listed in a detailed commit body. |
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
--git-commit-message-style detailed
```

## Plasma Widget

| Arg | Type | Meaning |
|---|---:|---|
| `--plasma-widget-path` | `str` | Main Plasma note HTML widget path. Required for plasma widget sync. |
| `--plasma-bold-widget-path` | `str` | Optional bold-only mirror widget path. |
| `--plasma-markdown-note-path` | `str` | Markdown note path synced with Plasma widget content. Required. |
| `--plasma-css-style` | `bool` | Render with CSS checkbox markers and real `ul/li` output. |

## Dropdir

| Arg | Type | Meaning |
|---|---:|---|
| `--dropdir-action` | `str[]` | Run temporary Lucy flags when a file is moved into a matching drop directory. Format: `selector=flags`. |
| `--dropdir-action-delay-milliseconds` | `int` | Delay before running a dropdir action after move-back. |

Examples:

```text
--dropdir-action "cleanup=--archive-pair"
--dropdir-action "todo-drop=--formatter-todo"
--dropdir-action-delay-milliseconds 1200
```

The action is parsed as normal Lucy flags and run against the moved-back source
path. Target modules must be selected in `--sys-modules`. System flags
(`--sys-*`) are rejected inside dropdir actions.

## Voice

| Arg | Type | Meaning |
|---|---:|---|
| `--voice` | `bool` | Replace this line with local Vosk transcription. |
| `--voice-offline-vosk-model-path` | `str` | Local Vosk model directory. |
| `--voice-timeout-seconds` | `int` | Safety limit for one listen. Normal stop is after silence. |
| `--voice-recorder-path` | `str` | Recorder executable that writes raw mono PCM16 audio to stdout. Default: `arecord`. |
| `--voice-sample-rate` | `int` | Recorder and Vosk sample rate. Default: `16000`. |

Example:

```text
--voice
--voice-offline-vosk-model-path ~/.local/share/vosk-model-ru
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
| `--status-git-sync-prefix-cycle-pause-seconds` | `float` | Pause between git-sync prefix animation cycles. Default: `1.0`. |
| `--status-opened-events` | `bool` | Enable status module processing for `opened` events. Disabled by default. |

Examples:

```text
--status date time
--status git update
--status-banner "Working"
--status-animation "loading" "loading." "loading.."
--status-animation-speed-milliseconds 800
--status-git-sync-prefix-cycle-pause-seconds 1.0
--status-opened-events
```

## Cmd Module

The `cmd` module runs local commands. If notes are synced through Git or any
remote source, a changed note can become remote command execution. Enable it
only for fully trusted notes.

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
