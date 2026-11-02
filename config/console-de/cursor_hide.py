#!/usr/bin/env python3
"""Hide the labwc pointer at startup and one second after touchpad motion."""
import glob
import os
import select
import sys
import time

try:
    from evdev import InputDevice, UInput, ecodes
except ImportError as exc:
    sys.exit(f"python-evdev is required for cursor auto-hide: {exc}")

HIDE_AFTER = 1.0
LOG_PATH = os.path.expanduser("~/.cache/console-de-cursor.log")
MOTION_CODES = {
    ecodes.ABS_X, ecodes.ABS_Y,
    getattr(ecodes, "ABS_MT_POSITION_X", -1),
    getattr(ecodes, "ABS_MT_POSITION_Y", -1),
}


def log(message):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as stream:
            stream.write(f"{time.strftime('%F %T')} {message}\n")
    except OSError:
        pass


def is_touchpad(device):
    name = device.name.lower()
    if "touchpad" not in name and "trackpad" not in name:
        return False
    caps = device.capabilities(absinfo=False)
    abs_codes = set(caps.get(ecodes.EV_ABS, []))
    keys = set(caps.get(ecodes.EV_KEY, []))
    has_position = ecodes.ABS_X in abs_codes or ecodes.ABS_MT_POSITION_X in abs_codes
    has_touch = bool({ecodes.BTN_TOUCH, ecodes.BTN_TOOL_FINGER} & keys)
    return has_position and has_touch


def main():
    devices = []
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            device = InputDevice(path)
            if is_touchpad(device):
                devices.append(device)
                log(f"Watching touchpad: {device.name} ({path})")
            else:
                device.close()
        except (OSError, PermissionError):
            continue

    if not devices:
        log("No touchpad event device found; cursor timeout is inactive")
        return 1

    try:
        keyboard = UInput(
            name="console-de-cursor-control",
            bustype=ecodes.BUS_USB,
            cap={ecodes.EV_KEY: [ecodes.KEY_F13]},
        )
    except OSError as exc:
        for device in devices:
            device.close()
        log(f"Could not create cursor control key: {exc}")
        return 1

    def hide_cursor():
        keyboard.write(ecodes.EV_KEY, ecodes.KEY_F13, 1)
        keyboard.syn()
        keyboard.write(ecodes.EV_KEY, ecodes.KEY_F13, 0)
        keyboard.syn()

    try:
        hide_cursor()
        hidden = True
        hide_deadline = None
        log("Cursor hidden at startup")
        while True:
            timeout = None
            if hide_deadline is not None:
                timeout = max(0.0, hide_deadline - time.monotonic())
            ready, _, _ = select.select([device.fd for device in devices], [], [], timeout)
            if not ready and hide_deadline is not None:
                if not hidden:
                    hide_cursor()
                    hidden = True
                    log("Cursor hidden after one second without trackpad movement")
                hide_deadline = None
                continue

            for device in devices:
                if device.fd not in ready:
                    continue
                try:
                    events = device.read()
                except BlockingIOError:
                    continue
                for event in events:
                    if event.type == ecodes.EV_ABS and event.code in MOTION_CODES:
                        hidden = False
                        hide_deadline = time.monotonic() + HIDE_AFTER
    except KeyboardInterrupt:
        pass
    finally:
        keyboard.close()
        for device in devices:
            device.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
