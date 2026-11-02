# Troubleshooting

## Collect a diagnostic report

From a terminal in any session, run:

```sh
console-de-doctor
```

The report is written to `~/.cache/console-de-doctor.txt`. It includes distribution and session information, required command availability, audio status, controller access, shell logs, and recent user-journal messages. Review it before sharing because system logs can contain machine-specific details.

Useful logs:

| File | Contents |
| --- | --- |
| `~/.cache/console-de-autostart.log` | labwc startup and shell launch output |
| `~/.cache/console-de-shell.log` | UI, focus, Steam, controller, and game lifecycle events |
| `~/.cache/console-de-gamepad.log` | optional gamepad-to-keyboard helper events |
| `~/.cache/console-de-wf-recorder.log` | screen-recording startup and errors |

## Console DE returns to GDM

Confirm that `/usr/share/wayland-sessions/console-de.desktop` points to `/usr/local/bin/console-de-session`, then check the autostart log and the GDM/user journal. Re-run the installer after downloading a corrected session entry.

The session launcher needs a working `labwc` binary. A failed package transaction can leave the session entry installed while the compositor is missing; `console-de-doctor` reports command availability.

## Stutter or blurry graphics in a VM

In GNOME Boxes, shut down the VM and open **Preferences → Resources**. Enable **3D Acceleration** if available, then restart the VM. A guest-reported **Red Hat VirtIO GPU** is a virtual adapter; it does not confirm that the host GPU is passed directly into the VM. If the entire desktop remains slow, collect `console-de-doctor` output and include the guest resolution and Boxes 3D setting.

The shell reduces the startup animation's redraw workload when it detects a VM. This does not accelerate the host compositor, the VM's 2D display backend, or games.

## Sound effects or menu music are silent

The shell uses `ffplay` to play the bundled MP3 cues. Check that both `ffplay` and `ffmpeg` are available in `console-de-doctor`. On Ubuntu, install FFmpeg with:

```sh
sudo apt install ffmpeg
```

Also check that the PipeWire/PulseAudio output is connected and not muted. The sound pipeline uses the current user's audio session.

## Black cover remains after a game exits

The current shell removes its launch cover when it confirms that the game process has exited. Check `console-de-shell.log` for `Launched game exited` and `BLACK SCREEN: hidden`. If those events are absent, include the surrounding shell log lines and the game AppID in a report.

## Controller is not detected

- Sign out and back in after installation so the new `input` group membership applies.
- Confirm that the controller is visible under `/dev/input` and that `console-de-doctor` lists its event device.
- Check that `/dev/uinput` exists and is writable only if using the optional keyboard helper.
- Avoid starting `gamepad.py` alongside the shell's direct controller reader.

## A feature is missing

Some helpers are optional. Screen recording requires `wf-recorder`; region screenshots require `slurp`. Check the diagnostic report for missing binaries before filing an issue.
