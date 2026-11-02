# Development

## Project map

| Path | Purpose |
| --- | --- |
| `config/console-de/shell.py` | GTK home shell, settings, Steam discovery, Control Center, gamepad input, and lifecycle tracking |
| `config/console-de/gamepad.py` | Optional evdev-to-keyboard helper for applications outside the shell |
| `config/console-de/cursor_hide.py` | Hides the pointer after it has been idle |
| `config/labwc/` | Compositor startup, key bindings, menus, and theme |
| `config/waybar/`, `config/wofi/`, `config/foot/` | Supporting desktop configuration |
| `scripts/` | Session/shell launchers, screenshot helper, diagnostics, reload and power helpers |
| `session/console-de.desktop` | GDM Wayland session declaration |
| `install.sh` | Distribution detection, dependency installation, and config deployment |

The main shell is a GTK 3 application. Cairo draws the carousel and startup animation. Steam game metadata and installed desktop entries are discovered from the logged-in user's Linux session.

## Local iteration

Edit files in the repository tree. To deploy changed defaults to the current account, run `sh install.sh` from the repository root and then restart the Console DE session. Back up local config changes first; the installer refreshes tracked config files that differ from the repository.

To compare installed files with this checkout when using `console-de-doctor`, set the source directory before invoking it:

```sh
CONSOLE_DE_SOURCE="$PWD" console-de-doctor
```

## Runtime behavior

- `config/labwc/autostart` starts the home shell and pointer-hiding helper.
- The shell reads gamepad event devices directly. The optional `gamepad.py` helper is not started automatically to avoid two readers competing for input.
- The screenshot helper uses `grim`; region capture adds `slurp`.
- Screen recording is delegated to `wf-recorder` when installed.
- The installer registers a GDM session and installs shell helpers into `/usr/local/bin`.

## Reporting changes

For bug reports, include the Linux distribution/release, session type, display resolution, controller model (if relevant), steps to reproduce, and the smallest relevant section of `console-de-doctor.txt` or the shell log. Remove tokens, account data, and unrelated private log lines before posting.
