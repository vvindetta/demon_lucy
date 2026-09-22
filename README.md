![Demon Lucy](media/lucy.gif)

# D(a)emon Lucy — a modular file modification program

Lucy works at the filesystem level. Use any editor you like. You don't need any plugins.

Lucy monitors your directory via `inotify` and runs the modules whenever a file event occurs.

![Lucy demo](media/lucy_demo.gif)

#### Usage

Write a `--arg` command in your note and save it (`Ctrl+S`).

Lucy runs the modules and writes the output directly into the note.

### Modules

**Basic modules (loaded by default):**

- `sys`: writes runtime debug information and manual help text.<br>
  ![Sys module: ping, help, loaded modules, and flag manual](media/modules/sys_demo.gif)
- `linker`: keeps Markdown links in sync and pins active notes.<br>
  ![Linker module: renaming a file updates its link; editing the link moves the file](media/modules/linker_demo.gif)
- `archive`: moves idle text to an archive note, keeping the working note clear.<br>
  ![Archive: after 12 idle hours, on demand, or into a dated file](media/modules/archive_demo.gif)
- `dropdir`: runs actions when you drop notes into configured folders.<br>
  ![Dropdir: drop a note to make a checklist, then archive it; the file returns to its original folder](media/modules/dropdir_demo.gif)
- `renamer`: renames notes and automatically names scratch files by date.<br>
  ![Renamer: add an extension, name a note by date, and rename it](media/modules/renamer_demo.gif)
- `banner`: inserts ASCII banners with text or dates.<br>
  ![Banner module: text crimes and today's date](media/modules/banner_demo.gif)
- `formatter`: formats notes, converts bullet lists into checklists, and autocompletes Lucy arguments.<br>
  ![Formatter: checkboxes, dates, and flag completion](media/modules/formatter_demo.gif)
- `graph`: creates graph blocks showing word frequencies.<br>
  ![Graph: count coffee mentions across a week of notes](media/modules/graph_demo.gif)
- `include`: renders complete files or matching source paragraphs inside notes.<br>
  ![Include: insert a file and collect matching task paragraphs](media/modules/include_demo.gif)
- `status`: updates filenames with dynamic tokens (time, date, Git status, animations).<br>
  ![Status: configure a clock, scrolling message, and Git sync indicator](media/modules/status_demo.gif)
- `alias`: creates aliases.
- `workspace`: initializes a default notes workspace with Git and systemd files.

**Integration modules:**

- `git`: syncs notes with a remote Git repository.
- `plasma_widget`: syncs Markdown notes with KDE Plasma note widgets ([see video](media/plasma_widget.mp4)).

**WIP modules:**

- `cmd`: runs commands. Not loaded by default for security reasons.<br>
  ![Cmd: read /etc/os-release and draw a folder tree inside the note](media/modules/cmd_demo.gif)
- `kdeconnect_sync`: sends note changes to your phone via KDE Connect.
- `voice`: transcribes speech into notes using offline Vosk or online speech providers.
- `ai`: edits the current note from an inline prompt using a local agent.
- `email`: reads email as notes, sends drafts, and manages messages by moving files.

See [CHEATSHEET.md](CHEATSHEET.md) for all arguments.

## Theory

### Argument system

All `--args` use Unix-style syntax. You can provide them in three places:

1. Inside the note file (local, for per-note behavior)
2. In `config.txt` (global)
3. At startup: `python3 main_daemon.py --arg` (global)

See [CHEATSHEET.md](CHEATSHEET.md) for all `--args`.

### Use `--help`

```text
* `--mods`: list loaded modules and their priorities.
* `--ping`: send a notification and replace the command line with `++pong!`.
* `--config`: show config values that differ from the defaults.
* `--man <name>`: show an argument and its description (for example, `--man mods` or `--man --mods`).
```

Use `--mods` to see loaded modules and their priorities.

```text
* sys (2)
* banner (10)
* renamer (20)
* status (21)
* linker (22)
* formatter (23)
* graph (24)
* archive (25)
```

### Read the manual

`--man <name/module>` shows help for an argument or module.

## Lucy on Android

You can run Lucy in [Termux](https://f-droid.org/packages/com.termux/). See the [setup guide](#termux-setup).

## Install

Warnings:

- **Turn on automatic file reloading in your text editor!**
- The project has only been tested on GNU/Linux-based distributions. Sorry :<
- macOS and Windows do not support daemon `opened` events (for example, the Git module uses opened events to sync your repo).

1. Clone the repository:

```sh
git clone --depth 1 https://codeberg.org/vvindetta/demon_lucy && cd demon_lucy
```

2. Install Python dependencies:

```
python3 -m pip install -r requirements.txt
```

3. Edit the config file. `config.txt` is a template.
Uncomment and edit the lines you need.

### Manual run

Run Lucy in daemon mode:

```text
python3 main_daemon.py --sys-config-path "/home/user/Notes/.lucy/config.txt"
```

Run Lucy in oneshot mode (useful for scripts and scheduled runs):

```text
python3 main_oneshot.py \
  --oneshot-event opened \
  --oneshot-paths "/home/user/Notes/file.md" \
  --sys-modules git
```

### Systemd setup

The repo includes four units in `setup-systemd/`:

- [lucy-daemon.service](setup-systemd/lucy-daemon.service): watches note events in real time.
- [lucy-daemon.timer](setup-systemd/lucy-daemon.timer): starts the daemon 30 seconds after the user service manager starts.
- [lucy-oneshot.service](setup-systemd/lucy-oneshot.service): runs once and exits, for periodic or manual tasks.
- [lucy-oneshot.timer](setup-systemd/lucy-oneshot.timer): starts `lucy-oneshot.service` on a schedule.

Edit the service files and set the paths to your repository, notes, and config.
Alternatively, use the files generated by the `workspace` module, but verify
all paths before enabling the services.

Default service paths:

- repo: `$HOME/demon_lucy`
- notes: `$HOME/Notes`
- config: `$HOME/Notes/.lucy/config.txt`

Move the units:

```bash
mkdir -p ~/.config/systemd/user; \
mv setup-systemd/lucy-daemon.service ~/.config/systemd/user/; \
mv setup-systemd/lucy-daemon.timer ~/.config/systemd/user/; \
mv setup-systemd/lucy-oneshot.service ~/.config/systemd/user/; \
mv setup-systemd/lucy-oneshot.timer ~/.config/systemd/user/
```

Reload and enable:

```bash
systemctl --user daemon-reload; \
systemctl --user enable --now lucy-daemon.timer; \
systemctl --user enable --now lucy-oneshot.timer
```

Useful checks:

```text
systemctl --user status lucy-daemon.service
systemctl --user status lucy-daemon.timer
systemctl --user status lucy-oneshot.timer
systemctl --user start lucy-oneshot.service
journalctl --user -u lucy-daemon.service -f
```

## Termux setup

Install these apps from F-Droid:

- [Termux](https://f-droid.org/packages/com.termux/): base shell/runtime.

Optional add-ons:

- [Termux:Boot](https://f-droid.org/packages/com.termux.boot/): place a startup script in `~/.termux/boot/` to run it automatically after boot.
- [Termux:Widget](https://f-droid.org/packages/com.termux.widget/): place a script in `~/.shortcuts/` to run it from the widget.
- [Termux:Tasker](https://f-droid.org/packages/com.termux.tasker/): place a script in `~/.termux/tasker/` to run it from Tasker automations.
- [Termux:API](https://f-droid.org/packages/com.termux.api/): enables `termux-job-scheduler`, `termux-wake-lock` and notifications.

I recommend [Markor](https://github.com/gsantner/markor) as a text editor.

Ready-to-use Termux scripts are in `setup-termux`:

- `lucy-daemon.sh`
- `lucy-oneshot.sh`
- `lucy-job-scheduler.sh`

Default script paths:

- repo: `$HOME/demon_lucy`
- notes: `$HOME/storage/shared/Notes`
- config: `$HOME/storage/shared/Notes/.lucy/config.txt`
- state/logs: `$HOME/.lucy`

Periodic mobile sync can also be registered through Android JobScheduler with [lucy-job-scheduler.sh](setup-termux/lucy-job-scheduler.sh). Use Termux:Boot for this mode only to register the persisted job after reboot.
