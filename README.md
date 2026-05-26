![lucy.png](media/lucy.png)

# Lucy d(a)emon — modular notes manager

Your notes are just files. Use any editor you like. No editor plugins. Git is your cloud.

Lucy daemon monitors your note folder. Every time you edit something, it runs modules on that file (formatter, sync, git, etc).
  
![Lucy demo](media/lucy_notes_demo.gif)

Lucy can also read Unix-style flags written inside the note file and pass them to modules.
It could be an execution command or some settings.

## Example of use
If `README.md` is one of your notes, you can write command flags directly inside it:

Rename `README.md` to `DONOTreadme.md`:

```--rename DONOTreadme.md```

Execute the terminal command (output will be written directly to the file):

```--cmd neofetch```


Then press ```CTRL+S``` - Lucy will detect the change and run the modules.

### Use cases  
- Auto-format files, rename and sort it
- Sync notes between formats and programs
- Git auto-commit
- Calendar integration
- Sync your system widgets: [KDE Plasma demo](media/plasma_widget.mp4)
- Write your own module!

### How to sync with mobile?
Use Lucy's Git module together with [GitSync Android app](https://github.com/ViscousPot/GitSync) and [Markor](https://github.com/gsantner/markor) text editor.

## Theory

### Flags system
You can provide flags in three places:

1. Inside the note file (for per-note behavior)
2. In config.txt (global defaults)
3. At startup: ```python3 main_daemon.py --some-flag```

For a compact list of all current arguments, see args [CHEATSHEET.md](CHEATSHEET.md).

### System module

```--help``` for help message: 
```
* --mods: print loaded modules and their priorities
* --config: print config values that differ from defaults
* --man <name>: print one argument with description (example: --man mods or --man --mods)
```


```--mods``` to see loaded modules:
```
* sys (0)
* banner (10)
* todo (10)
* renamer (20)
* plasma_widget (30)
* cmd (50)
```

`--man flag_arg_here` for help with any flag argument.

```--man man``` :

```
* --man: Argument manual. Use: --man <module name> or --man --flag (example: --man sys or --man --mods). (type=str, default=None)
```

## Install

Tested on Fedora GNU+Linux.

1. Clone the repository:
```
git clone https://codeberg.org/Vindetta/lucy_notes_daemon && cd lucy_notes_daemon
```
   
2. Install dependencies:
```
pip install -r requirements.txt
```

3. Create a notes config file and set at least `--sys-watch-paths "/home/user/Notes"`.

**Turn on file auto-update in your text editor.**

### Manual run

Run the daemon:
```text
python3 main_daemon.py
```

Run oneshot tasks manually (useful for scripts, scheduled runs, and Termux/Tasker):
```text
python3 main_oneshot.py \
  --oneshot-event opened \
  --oneshot-paths "/home/user/Notes/file.md" \
  --sys-modules git
```

### Systemd setup

The repo includes three units in `systemd-services/`:
- [lucy-daemon.service](systemd-services/lucy-daemon.service): always-running watcher for real-time note events.
- [lucy-oneshot.service](systemd-services/lucy-oneshot.service): single-run job (runs once and exits), used for periodic/manual tasks.
- [lucy-oneshot.timer](systemd-services/lucy-oneshot.timer): schedule that starts `lucy-oneshot.service`.

Edit the service files and set your real repo path, config path, and oneshot target note.

Link the units:
```text
mkdir -p ~/.config/systemd/user
ln -sf "$PWD/systemd-services/lucy-daemon.service" ~/.config/systemd/user/lucy-daemon.service
ln -sf "$PWD/systemd-services/lucy-oneshot.service" ~/.config/systemd/user/lucy-oneshot.service
ln -sf "$PWD/systemd-services/lucy-oneshot.timer" ~/.config/systemd/user/lucy-oneshot.timer
```

Reload and enable:
```text
systemctl --user daemon-reload
systemctl --user enable --now lucy-daemon.service
systemctl --user enable --now lucy-oneshot.timer
```

Useful checks:
```text
systemctl --user status lucy-daemon.service
systemctl --user status lucy-oneshot.timer
systemctl --user start lucy-oneshot.service
journalctl --user -u lucy-daemon.service -f
```


## Modules

To add new modules, edit `lucy_notes_manager/runtime.py` (`build_lucy_modules`).
Hot reload and module install/uninstall commands are not implemented yet; restart `lucy-daemon.service` after module/runtime changes.

### List of available modules

**Basic:**
- `sys`: writes runtime information, config output, event details, and manual text into notes.
- `formatter`: formats note text, including todo list conversion and blank-space padding.
- `banner`: inserts ASCII banner text or date banners into notes.
- `renamer`: renames notes manually or by simple date-based create rules.

- `linker`: creates or cleans repository-root symlinks for active notes.
- `dropdir`: handles moved files in configured drop directories. Useful for temporary inbox/drop folders where moving a file should immediately trigger a follow-up action.
- `archive`: archives stale active notes into a past/archive note. Useful for keeping one current daily scratch note while automatically moving old content into history.
- `cmd`: runs local commands and writes command output into notes. Disabled by default.

**Integrations:**
- `git`: syncs notes with a remote Git repository.
- `plasma_widget`: syncs Markdown notes with KDE Plasma note widgets ([see video](media/plasma_widget.mp4)).

For a compact list of all current arguments, see args [CHEATSHEET.md](CHEATSHEET.md).
