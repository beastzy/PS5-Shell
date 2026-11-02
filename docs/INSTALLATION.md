# Installation

## Requirements

- A Linux distribution with a working Wayland session and GDM (the installer registers a GDM Wayland session).
- A compatible Wayland compositor; the provided session starts labwc.
- Python 3, GTK 3, PyGObject, and the compositor utilities listed by the installer.
- Steam for Steam game discovery and launch. Steam login is completed in Steam itself.
- A controller is optional. Controller access requires read permission for `/dev/input` and the installer configures `/dev/uinput` for its optional keyboard helper.

The installer recognizes Arch-based, Debian/Ubuntu-based, Fedora/RHEL-based, and openSUSE/SUSE package-manager families. Package names and repository availability vary by release. Steam may require enabling the distribution's non-free, multiverse, or third-party repository.

## Install from a clone

```sh
git clone https://github.com/beastzy/PS5-Shell.git
cd PS5-Shell
sh install.sh
```

Run as your regular account. The script calls `sudo` for system changes and asks for a password when needed. It detects the distribution from `/etc/os-release`, attempts to install available packages, copies the default configuration and assets, registers `/usr/share/wayland-sessions/console-de.desktop`, and configures input-device access.

If one package is unavailable, the script reports the failure and continues with the remaining setup. Review its final output and run `console-de-doctor` after signing in.

## Start the session

1. Log out of the current desktop.
2. At the GDM login screen, choose your user.
3. Open the session menu (gear icon) and choose **Console DE**.
4. Sign in. The session appears as **Console DE** in the menu; the project name is PS5 Shell.

The first startup shows the PS5 Shell introduction before the Games and Apps interface appears.

## Update an existing installation

Extract or clone the newer repository and run `sh install.sh` again. The installer refreshes its tracked config files when their contents differ and merges bundled icons and sounds. Back up any personal edits to files under `~/.config/labwc` or `~/.config/console-de` first; changed defaults can replace those files.

Log out and back in to load the refreshed shell and session configuration.

## Optional features

- **Sound effects and menu music:** require `ffplay` and `ffmpeg`. The Ubuntu install path installs the `ffmpeg` package. On other distributions, install the matching FFmpeg package if the sound cues do not play.
- **Screen recording:** requires `wf-recorder`; it is not included in every distribution's automatic dependency list. Install it from your distribution's repository when available.
- **Region screenshots:** require both `grim` and `slurp`.
- **Gamepad translation for other apps:** `config/console-de/gamepad.py` can translate a controller into keyboard events. It is not launched automatically because Console DE reads the controller directly; running both readers can compete for controller input.

## Uninstall

Clone or download the repository and run the uninstaller as your regular user:

```sh
sh uninstall.sh
```

Or run it directly from GitHub:

```sh
curl -fsSL https://raw.githubusercontent.com/beastzy/PS5-Shell/main/uninstall.sh | sh
```

It asks for confirmation, then removes the Console DE session entry, installed launchers, udev rule, and Console DE configuration/assets. Steam and system packages remain installed because the installer does not track which packages were already present. Log out or restart the display manager after removal.
