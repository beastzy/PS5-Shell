# PS5 Shell

![Linux](https://img.shields.io/badge/platform-Linux-4c9aff) ![Wayland](https://img.shields.io/badge/session-Wayland-7c4dff) ![Python](https://img.shields.io/badge/interface-Python%20%2B%20GTK3-3776ab)

**A controller-first desktop shell for Linux, built around labwc and Steam.**

PS5 Shell presents installed Steam games and desktop applications in a couch-friendly Linux interface. It is designed for keyboard and gamepad navigation, with a Control Center, settings, screenshots, and optional screen recording. The installed GDM session keeps the technical name Console DE.

> This is an experimental, console-inspired project. It is unofficial and is not affiliated with or endorsed by Sony, PlayStation, Valve, or GNOME.

## What it includes

- A GTK 3 home shell that discovers local Steam games and installed desktop applications.
- A labwc Wayland session with a GDM session entry.
- D-pad and arrow-key navigation, with Enter/A used to activate items.
- A Control Center that can open over the DE or a foreground game.
- Settings for display scale, audio, game launch behavior, system usage, and other shell options.
- Startup and navigation animations, sound cues, and controller/microphone status cards.
- Screenshots through grim; region capture also uses slurp.
- Optional long-press screen recording through wf-recorder.
- A diagnostic command that gathers session, audio, controller, and shell logs.

PS5 Shell launches Steam games through the signed-in Steam client. It does not request or store Steam credentials.

## Install

See docs/INSTALLATION.md for prerequisites, supported package-manager families, and the GDM setup steps.

```sh
git clone https://github.com/beastzy/PS5-Shell.git
cd PS5-Shell
sh install.sh
```

Run the installer as your normal user, not with sudo. It installs packages and system session files, copies the shell and assets into ~/.config/console-de, registers the Console DE session, and configures gamepad access.

After installation, log out. At GDM, choose Console DE from the session menu and sign in.

## Controls

| Input | Action |
| --- | --- |
| Arrow keys / D-pad | Navigate |
| Enter / A (Cross) | Activate the focused item |
| Escape / B (Circle) | Go back or close a menu |
| Tab / LB / RB | Move between Games and Apps |
| Guide / Home | Open or close Control Center |
| Super + Shift + Space | Open Control Center from the keyboard |
| F7 | Take a full-screen screenshot |
| Super + F7 | Select a screenshot region |
| Dedicated capture button | Take a screenshot; hold to toggle recording if wf-recorder is installed |

The dedicated capture button depends on the controller and Linux input driver. See docs/CONTROLS.md for details.

## Virtual machines

GNOME Boxes and similar QEMU/KVM setups usually expose a virtual VirtIO graphics adapter to Ubuntu. That adapter name does not by itself confirm 3D acceleration. Enable 3D acceleration in the VM settings when the host supports it, then restart the VM. PS5 Shell reduces startup animation cost when it detects a VM, but the compositor and games still depend on the VM graphics configuration.

Tile geometry stays proportional to screen size; text and icons scale from the existing 2880×1800 reference layout. See docs/TROUBLESHOOTING.md for VM graphics guidance.

## Documentation

- Installation and updates: docs/INSTALLATION.md
- Keyboard and controller controls: docs/CONTROLS.md
- Troubleshooting and logs: docs/TROUBLESHOOTING.md
- Project structure and development: docs/DEVELOPMENT.md
- Contributing: CONTRIBUTING.md

## Repository contents

- config/: labwc, Waybar, Wofi, shell, controller code, and assets
- scripts/: session launcher, shell launcher, screenshots, diagnostics, and helpers
- session/: GDM Wayland session entry
- docs/: installation, controls, troubleshooting, and development guides
- install.sh: distribution detection, dependency setup, and file installation

## Assets and license

No software license has been selected for this repository yet. Until one is added, the code is not granted for reuse by default.

Some bundled sound and visual assets are console-inspired and their redistribution rights have not been established here. Review or replace those assets before making a public repository. The project is unofficial and does not claim PlayStation branding or ownership.
