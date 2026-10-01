# Demon Lucy Args Cheatsheet

Quick reference for current flags. Use `--man <module-or-argument>` in a note for
help and defaults from loaded modules.

- `bool`: write the flag to enable it; `true` and `false` values are not supported.
- `str[]`: space-separated values. `enum`: one of the listed values.
- `<value>` marks a required value; `[value]` marks an optional value.
- In notes and config files, quote values containing spaces. Backslashes are literal.
- Commands starting with `python3` run in a terminal; other examples belong in notes
  or config files unless stated otherwise.

Run terminal examples from Lucy's project directory unless stated otherwise.

## Runtime and module selection

Set these flags in the config file or at startup.

| Argument | Type | Meaning |
|---|---|---|
| `--sys-config-path` | `str` | Config file path. Default: `config.txt`. |
| `--sys-log-level` | `enum` | `debug`, `info`, `warning`, `error`, `critical`. Use `info` for normal event decisions; `debug` adds low-level and library diagnostics. |
| `--sys-log-format` | `str` | Python logging format string. |
| `--sys-watch-paths` | `str[]` | Directories to watch recursively. |
| `--sys-opened-event-cooldown-seconds` | `int` | Minimum interval in seconds between processed `opened` events for the same file. |
| `--sys-disable-opened-events` | `bool` | Ignore `opened` events. |
| `--sys-dynamic-block-hide-allowed-values` | `bool` | Omit allowed-value hints from new dynamic blocks. |
| `--sys-notification-provider` | `enum` | Notification backend: `auto`, `termuxapi`, `desktop`, `disable`. |
| `--sys-notification-min-interval-seconds` | `float` | Minimum interval in seconds before repeating the same notification. |
| `--sys-notification-error-backoff-base-seconds` | `float` | Initial interval in seconds for error notification backoff. |
| `--sys-notification-error-backoff-max-seconds` | `float` | Maximum interval in seconds for error notification backoff. |
| `--sys-notification-error-burst-limit` | `int` | Maximum error notifications within one burst window. |
| `--sys-notification-error-burst-window-seconds` | `float` | Duration in seconds of the global error notification window. |
| `--sys-ignore-paths` | `str[]` | Skip module processing inside these paths. |
| `--sys-ignore-move-paths` | `str[]` | Skip internal moves within these directories. Relative paths apply under each watched root. Default: `.status`. |
| `--sys-git-repo-lock-wait-timeout-seconds` | `float` | Maximum wait in seconds for Lucy's Git repository lock before skipping the cycle. |
| `--sys-git-repo-lock-retry-sleep-seconds` | `float` | Delay in seconds between Git repository lock attempts. |
| `--sys-git-repo-lock-stale-seconds` | `float` | Age in seconds after which a Git repository lock is considered stale. |
| `--sys-modules` | `str[]` | Load only the named modules, replacing the default selection. |
| `--sys-modules-exclude` | `str[]` | Remove named modules from the selected or default list. |
| `--sys-modules-priority` | `str[]` | Override execution order with `name=integer`. Lower numbers run first. |

```bash
python3 main_daemon.py --sys-watch-paths ~/Notes --sys-disable-opened-events
```

```text
--sys-notification-provider desktop
--sys-ignore-paths ~/.cache ~/Notes/private
--sys-modules git status archive sys
--sys-modules-exclude status
--sys-modules-priority archive=20 git=50
```

## One-shot runs

Run `main_oneshot.py` to process a synthetic event once. Without target paths,
modules that support direct CLI actions use the current directory as their scope.

| Argument | Type | Meaning |
|---|---|---|
| `--oneshot-event` | `enum` | Event: `created`, `modified`, `moved`, `deleted`, `opened`. Default: `modified`. |
| `--oneshot-paths` | `str[]` | Files or directories to process. Omit for direct CLI actions or a `moved` event. |
| `--oneshot-move-src-path` | `str` | Original path for a synthetic `moved` event. |
| `--oneshot-move-dest-path` | `str` | New path for a synthetic `moved` event. |

```bash
python3 main_oneshot.py --oneshot-event modified --oneshot-paths ~/Notes
python3 main_oneshot.py --oneshot-paths ~/Notes/note.md --sys-modules git
python3 main_oneshot.py --oneshot-event moved --oneshot-move-src-path old.md --oneshot-move-dest-path new.md
```

## Sys commands

Write these commands in a note to insert their results.

| Argument | Type | Meaning |
|---|---|---|
| `--neofetch` | `bool` | Show Lucy and system information. |
| `--mods` | `bool` | List loaded modules and their priorities. |
| `--ping` | `bool` | Send a notification and write `++pong!`. |
| `--config` | `bool` | Show settings that differ from defaults and where they were set. |
| `--man` | `str[]` | Show module or argument help; names work with or without `--`. Without a name, show Sys command help. |
| `--help` | `bool` | Show Sys command help. |
| `--event` | `bool` | Show the current filesystem event. |

```text
--neofetch
--man git
--man sys
--man banner-date
--man --banner-date
--man mods banner-date
--man graph/include
--man --mods --man --banner-date
```

## Alias

System flags (`--sys-*`) and `--cmd` cannot be alias targets.

| Argument | Type | Meaning |
|---|---|---|
| `--alias` | `str[]` | Define `name=expansion` rules. `{args}` inserts values passed to the alias. |
| `--alias-dry-run` | `bool` | Log alias expansions without rewriting the note. |

```text
--alias "b=--banner {args}" "todo=--formatter-todo" "rn=--rename {args}"
```

## Workspace

| Argument | Type | Meaning |
|---|---|---|
| `--workspace-init` | `str` | Create a notes workspace at this path, initialize Git when available, and add systemd setup files where supported. |

```bash
python3 main_oneshot.py --workspace-init ~/Notes
```

## Banner

| Argument | Type | Meaning |
|---|---|---|
| `--banner` | `str[]` | Insert the supplied text as an ASCII banner. |
| `--banner-date` | `bool` | Insert today's date as an ASCII banner. |

```text
--banner hello world
--banner-date
```

## Renamer

| Argument | Type | Meaning |
|---|---|---|
| `--rename` | `str` | Rename the current file. |
| `--rename-auto` | `bool` | On creation, add an extension to extensionless files; date one-letter filenames that already have an extension. |
| `--rename-auto-format` | `str` | Extension for automatic renaming. Default: `md`. |

```text
--rename "Project notes.md"
```

## Linker

| Argument | Type | Meaning |
|---|---|---|
| `--linker-root` | `bool` | Create a link to the note in the Git repository root; use a hard link on Windows if needed. |
| `--linker-auto-clean-root-links` | `bool` | Remove managed root links when `--linker-root` is absent. |
| `--linker-ignore` | `str[]` | Exclude files and links by basename, absolute path, or repository-relative path. |
| `--linker-auto-update-md-links` | `bool` | Update Markdown links when files move; move target files when their links are edited. |

```text
--linker-root
--linker-auto-update-md-links
```

## Graph

Graphs become dynamic blocks. Periods: `week`, `month`, `year`, `all`.
Default: `year`.

| Argument | Type | Meaning |
|---|---|---|
| `--graph` | `str[]` | Graph literal matches: `<file> <pattern> [period]`. |
| `--graph-regex` | `str[]` | Graph regular expression matches: `<file> <regex> [period]`. |

```text
--graph past.md sleep week
--graph-regex past.md "\bsleep\b|slept|nap" month
```

## Include

| Argument | Type | Meaning |
|---|---|---|
| `--include` | `str[]` | Show a complete UTF-8 file in an indented dynamic block: `<file>`. |
| `--include-find` | `str[]` | Collect paragraphs whose first line starts with a keyword: `<file-or-directory> <keyword ...>`. |
| `--include-depth` | `int` | Maximum include nesting depth. Default: `3`. |

```text
--include shared/project.md
--include-find notes "tasks:"
```

## Archive

Mode `text` appends content to an archive file; `file` writes dated files into an
archive directory. Rules without a mode or age use the corresponding defaults.

| Argument | Type | Meaning |
|---|---|---|
| `--archive` | `bool` | Archive now using the first available route: configured pair, local `.archive/`, then global destination. |
| `--archive-pair` | `str[]` | Archive now through the configured pair rule: `[text\|file]`. |
| `--archive-local` | `str[]` | Archive the current note beside itself: `[text\|file]`. |
| `--archive-global` | `str[]` | Archive the current note to the global destination: `[text\|file]`. |
| `--archive-auto-pair` | `str[]` | Archive an idle source to a destination: `<src> <dest> [idle_hours] [text\|file]`. |
| `--archive-auto-local` | `str[]` | Archive an idle source beside itself: `<src> [idle_hours] [text\|file]`. |
| `--archive-auto-global` | `str[]` | Archive an idle source to the global destination: `<src> [idle_hours] [text\|file]`. |
| `--archive-ignore-paths` | `str[]` | Exclude events, sources, and destinations. Relative paths match whole components at any depth; absolute paths match a file or subtree. Default: `.lucy`; a supplied list replaces it, no values clears it. |
| `--archive-default-mode` | `enum` | Default output mode: `text` or `file`. Default: `text`. |
| `--archive-global-dest-path` | `str` | Global file or directory destination. Empty: use `archive.md` (`text`) or `.archive/` (`file`) at the Git repository root. |
| `--archive-idle-hours` | `float` | Minimum source age in hours for automatic archiving. Default: `12`. |
| `--archive-date-prefix` | `str` | Text before the date in archive headers. Default: `--- `. |
| `--archive-date-suffix` | `str` | Text after the date in archive headers. Default: empty. |
| `--archive-force-filesystem-mtime` | `bool` | Check source age using filesystem modification time, including inside Git repositories. |

Local text archives use `.archive/archive.md` if `.archive/` exists, otherwise
`archive.md`; local file archives use `.archive/YYYY-MM-DD-name.md`. Header dates
use the source's latest Git commit date when available, otherwise today.

Paths must stay inside the Git repository, or the note's directory outside Git.
Relative paths start at the event/source directory; `~` and relative `..` are
rejected. Daemon events must remain inside a watched root. The active config file
cannot be a source or destination.

```text
--archive-auto-pair now.md past.md 12 text
--archive-local file
```

## Formatter

| Argument | Type | Meaning |
|---|---|---|
| `--formatter-todo` | `bool` | Convert `- task` to `- [ ] task`, then remove the command. |
| `--formatter-blank` | `str[]` | Maintain blank padding: `<up\|down\|both> [count]`. The flag stays in the note. |
| `--formatter-date` | `bool` | Complete consecutive archive date headers written as `--- day`. The flag stays in the note. |
| `--formatter-autocomplete` | `bool` | Complete Lucy argument prefixes. The flag stays in the note. |

```text
--formatter-todo
--formatter-blank both 20
--formatter-date
--formatter-autocomplete
```

## Git

Select `git` with `--sys-modules` to enable automatic commits and remote sync.

| Argument | Type | Meaning |
|---|---|---|
| `--git-commit-message` | `str` | Base text for automatic commit messages. |
| `--git-commit-message-timestamp` | `bool` | Append a timestamp to commit messages. |
| `--git-commit-message-timestamp-format` | `str` | Timestamp format using Python `strftime` notation. |
| `--git-commit-message-style` | `enum` | `detailed`: subject and file actions in the body; `compact`: subject only. |
| `--git-commit-message-max-subject-files` | `int` | Maximum files named in the subject before switching to counts. |
| `--git-commit-message-max-body-files` | `int` | Maximum files listed in a detailed message body. |
| `--git-sync-on-opened-disable` | `bool` | Ignore `opened` events when scheduling Git sync. |
| `--git-push-auto-merge` | `bool` | Merge remote changes and retry a rejected push when the remote is ahead. Enabled by default. |
| `--git-upstream-auto-set` | `bool` | Set an upstream when a matching remote branch exists, preferring `origin`. Enabled by default. |
| `--git-merge-autoresolve` | `enum` | Conflicts: `none` leaves unresolved; `ours` keeps local; `theirs` keeps remote; `union` keeps both without markers; `markers` commits both with conflict markers. |
| `--git-command-timeout-seconds` | `float` | Timeout in seconds for local Git operations. |
| `--git-pull-timeout-seconds` | `float` | Timeout in seconds for pull/merge operations. |
| `--git-network-probe-timeout-seconds` | `float` | Timeout in seconds for checking remote reachability before a pull. |
| `--git-pull-offline-error-markers` | `str[]` | Error text fragments that identify offline or network pull failures. |
| `--git-push-timeout-seconds` | `float` | Timeout in seconds for push operations. |
| `--git-sync-retry-window-seconds` | `float` | Total background retry duration in seconds. `0` disables retries. |
| `--git-sync-retry-backoff-start-seconds` | `float` | Initial delay in seconds between background retries. |
| `--git-sync-retry-backoff-max-seconds` | `float` | Maximum delay in seconds between background retries. |

```text
--git-merge-autoresolve union
--git-commit-message-style detailed
```

## Plasma Widget

Select `plasma_widget` with `--sys-modules`.

| Argument | Type | Meaning |
|---|---|---|
| `--plasma-widget-path` | `str` | Main Plasma note HTML file. Required. |
| `--plasma-bold-widget-path` | `str` | Optional Plasma widget that mirrors only bold text. |
| `--plasma-markdown-note-path` | `str` | Markdown note to sync with the main widget. Required. |
| `--plasma-css-style` | `bool` | Render checkboxes as CSS markers in HTML lists. |

## Dropdir

Dropped files return to their source before temporary actions run. Keep `dropdir`
and the target modules loaded; actions cannot contain `--sys-*` flags.

| Argument | Type | Meaning |
|---|---|---|
| `--dropdir-init` | `str[]` | In a folder's `init.md`, define an action for direct drops: `"<flags>"`. Repeat for more actions. |
| `--dropdir-action` | `str[]` | Define a drop rule: `"<flags>" <directory>`. Match a directory name or absolute path; repeat for more rules. |
| `--dropdir-action-delay-milliseconds` | `int` | Delay in milliseconds between returning the file and running its actions. |

```text
--dropdir-action "--linker-root" "/notes/directory"
--dropdir-action "--formatter-todo" "todo-drop"
--dropdir-action-delay-milliseconds 1200
```

In the drop folder's `init.md`:

```text
--dropdir-init "--linker-root"
```

## Status

| Argument | Type | Meaning |
|---|---|---|
| `--status` | `str[]` | Add filename status tokens: `date`, `time`, `time-with-seconds`, `git`, `git update`. |
| `--status-banner` | `str` | Scroll text through the filename status, with a space between repeats. |
| `--status-banner-speed-milliseconds` | `int` | Delay in milliseconds between scrolling steps. |
| `--status-banner-max-characters` | `int` | Visible banner width in characters. `0` means unlimited. |
| `--status-prefix` | `str` | Text before the first filename status token. |
| `--status-animation` | `str[]` | Frames to cycle through in the filename status. |
| `--status-animation-speed-milliseconds` | `int` | Minimum delay in milliseconds between frames. |
| `--status-tick-interval-seconds` | `float` | Interval in seconds between regular status updates. |
| `--status-git-fast-tick-interval-seconds` | `float` | Interval in seconds for recent `git update` activity and the sync animation. Default: `0.5`. |
| `--status-git-fast-tick-window-seconds` | `float` | Duration in seconds of fast updates after Git activity. |
| `--status-git-sync-prefix-cycle-pause-seconds` | `float` | Pause in seconds between Git sync prefix animation cycles. Default: `1.0`. |
| `--status-opened-events` | `bool` | Update status on `opened` events. Disabled by default. |

```text
--status date time
--status git update
--status-banner "Working"
--status-animation "loading" "loading." "loading.."
--status-animation-speed-milliseconds 800
```

## Cmd

Requires an entry point that loads `cmd`; the standard launchers do not offer it.
Use only with trusted notes: synced edits can execute local commands.

| Argument | Type | Meaning |
|---|---|---|
| `--cmd` | `str[]` | Run a local command without a shell and insert its output. |
| `--cmd-timeout-seconds` | `int` | Timeout in seconds for each command. |
| `--cmd-output-max-bytes` | `int` | Maximum output size in bytes to insert into the note. |
| `--cmd-stream` | `enum` | Output to insert: `both`, `stdout`, `stderr`, `none`. |

```text
--cmd echo hello
--cmd-stream stdout
```
