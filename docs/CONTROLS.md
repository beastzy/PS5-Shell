# Controls

PS5 Shell accepts keyboard input and reads supported gamepads through Linux evdev. Button names below use the common Xbox layout; PlayStation labels are shown in parentheses.

## Home interface

| Keyboard | Controller | Action |
| --- | --- | --- |
| Arrow keys | D-pad / left stick | Move focus |
| Enter | A / Cross | Activate the focused item or enter a menu |
| Escape | B / Circle | Back or close the current menu |
| Tab | LB / RB | Switch between Games and Apps |
| Super + Shift + Space | Guide / Home | Open or close Control Center |

The Select button is currently unused. The time display is informational and is not selectable.

## Control Center

Guide/Home opens the Control Center above the current DE view or game. Navigate the available icons with the D-pad/arrows; press A/Enter to activate a selected item. Press Guide/Home again or Escape/B to close it.

The keyboard shortcut is Super + Shift + Space. The Home icon in the Control Center returns to the DE. Power choices are opened from the power icon and then selected with the D-pad/arrows and A/Enter.

## Screenshots and recordings

- Press **F7** for a full-screen screenshot.
- Press **Super + F7** for region selection.
- A recognized dedicated controller capture button takes a screenshot on a short press.
- Hold the capture button to start or stop a recording. The recording feature needs `wf-recorder` and saves output under `~/Videos`.

Controller drivers expose different button codes. If the capture button is not recognized, run `console-de-doctor` and include its report when filing an issue.

## Game input

During gameplay, the game receives regular keyboard/controller input. Guide/Home remains available to open Control Center. When the DE is foregrounded, its D-pad and confirm/back navigation becomes active again.
