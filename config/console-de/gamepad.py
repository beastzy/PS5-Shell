#!/usr/bin/env python3
"""Console DE — optional gamepad → desktop input helper.

Maps a game/controller to keyboard keys so every app is navigable
from the couch:

  D-pad ............ arrows (hold = repeat)
  A / Cross ......... Enter        B / Circle ....... Escape
  X / Square ........ Space        Y / Triangle ..... Tab
  Start ............. home screen  Guide ............ power menu (legacy helper behavior)
  Select ............ unused       LB / RB ......... tab switch
  LT / RT ........... volume

The Console DE shell reads its controller directly and does not start this
helper. Do not run both readers at once: they can compete for the same pad.
This optional helper's Guide mapping differs from the shell's Control Center
mapping.

Cursor hiding is managed separately by cursor_hide.py so controller input
does not interfere with the trackpad's one-second visibility timeout.

Requires read access to /dev/input (user in "input" group) and write
access to /dev/uinput for the virtual keyboard. The device is grabbed
so the compositor doesn't process raw pad events twice.
"""
import glob
import os
import socket
import subprocess
import sys
import time

try:
    import evdev
    from evdev import InputDevice, UInput, ecodes
except ImportError:
    sys.exit("python-evdev is not installed (pacman -S python-evdev / apt install python3-evdev)")

LOGF = os.path.expanduser("~/.cache/console-de-gamepad.log")
REPEAT_DELAY = 0.32    # d-pad: pause before repeat
REPEAT_RATE = 0.085
VOLUME_STEP = "5%"
DEBUG_EVENTS = 25      # log the first N raw events per pad (diagnostics)
CMD_PORT = 47777       # UDP commands to the home shell (direct channel)


def _log(msg):
    try:
        os.makedirs(os.path.dirname(LOGF), exist_ok=True)
        with open(LOGF, "a") as f:
            f.write(f"{time.strftime('%F %T')} {msg}\n")
    except OSError:
        pass


def send_cmd(cmd):
    """Send a navigation command straight to the shell over UDP.

    This is the guaranteed path: it works even if /dev/uinput access
    is broken and doesn't depend on which window has keyboard focus.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(cmd.encode(), ("127.0.0.1", CMD_PORT))
        s.close()
    except OSError:
        pass


# button map: ecodes.BTN_* -> key
BUTTON_MAP = {
    ecodes.BTN_SOUTH: ecodes.KEY_ENTER,     # A / Cross
    ecodes.BTN_EAST: ecodes.KEY_ESC,        # B / Circle
    ecodes.BTN_NORTH: ecodes.KEY_TAB,       # Y / Triangle
    ecodes.BTN_WEST: ecodes.KEY_SPACE,      # X / Square
    ecodes.BTN_START: ecodes.KEY_LEFTMETA,  # Start (handled below too)
    ecodes.BTN_TL: ecodes.KEY_PREVIOUSSONG, # LB -> previous tab
    ecodes.BTN_TR: ecodes.KEY_NEXTSONG,     # RB -> next tab
}
LAUNCHER_CMD = ["console-de-shell"]          # Start = return to the home screen
POWER_CMD = ["/usr/local/bin/console-de-power"]

# actions sent to the shell over UDP (deduped there against key injection)
BTN_CMDS = {
    ecodes.BTN_SOUTH: "enter",    # A / Cross
    ecodes.BTN_EAST: "back",      # B / Circle
    ecodes.BTN_TL: "tabprev",     # LB
    ecodes.BTN_TR: "tabnext",     # RB
}
HAT_CMDS = {
    0: {-1: "left", 1: "right"},  # hat x
    1: {-1: "up", 1: "down"},     # hat y
}


def find_pads():
    pads = []
    for path in sorted(glob.glob("/dev/input/event*")):
        try:
            dev = InputDevice(path)
        except (PermissionError, OSError):
            continue
        caps = dev.capabilities()
        keys = caps.get(ecodes.EV_KEY, [])
        if ecodes.BTN_SOUTH in keys and ecodes.BTN_START in keys:
            pads.append(dev)
        else:
            dev.close()
    return pads


def sh(cmd):
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


class Pad:
    def __init__(self, dev):
        self.dev = dev
        self.ui = UInput(
            name="console-de-virtual-kb",
            bustype=ecodes.BUS_USB,
            cap={ecodes.EV_KEY: list(set(BUTTON_MAP.values()) | {
                ecodes.KEY_UP, ecodes.KEY_DOWN, ecodes.KEY_LEFT, ecodes.KEY_RIGHT,
                ecodes.KEY_VOLUMEUP, ecodes.KEY_VOLUMEDOWN})},
        )
        self.held_keys = set()
        self.hat_state = {0: 0, 1: 0}
        self.hat_held = {0: None, 1: None}   # axis -> key currently down
        self.hat_since = {0: 0.0, 1: 0.0}
        self.trigger_vol = {ecodes.BTN_TL2: -1, ecodes.BTN_TR2: 1}
        self.held_triggers = set()
        self.vol_last = 0.0
        self.debug_left = DEBUG_EVENTS
        dev.grab()
        _log(f"attached: {dev.name} ({dev.path})")

    def key(self, code, down):
        if down and code not in self.held_keys:
            self.held_keys.add(code)
            self.ui.write(ecodes.EV_KEY, code, 1)
            self.ui.syn()
        elif not down and code in self.held_keys:
            self.held_keys.discard(code)
            self.ui.write(ecodes.EV_KEY, code, 0)
            self.ui.syn()

    def release_all(self):
        for code in list(self.held_keys):
            self.key(code, False)
        for ax in self.hat_held:
            self.hat_held[ax] = None

    def tick(self, now):
        # d-pad repeat: direction-aware, and each press/repeat also goes
        # to the shell over UDP so navigation never depends on uinput
        for ax, neg, pos in ((0, ecodes.KEY_LEFT, ecodes.KEY_RIGHT),
                             (1, ecodes.KEY_UP, ecodes.KEY_DOWN)):
            state = self.hat_state[ax]
            want = neg if state < 0 else pos if state > 0 else None
            if self.hat_held[ax] != want:
                if self.hat_held[ax]:
                    self.key(self.hat_held[ax], False)
                self.hat_held[ax] = want
                if want:
                    self.key(want, True)
                    self.hat_since[ax] = now
                    cmd = HAT_CMDS[ax].get(state)
                    if cmd:
                        send_cmd(cmd)
            elif want and now - self.hat_since[ax] >= REPEAT_DELAY:
                if now - self.hat_since[ax] >= REPEAT_RATE:
                    self.hat_since[ax] = now
                    self.key(want, False)
                    self.key(want, True)
                    cmd = HAT_CMDS[ax].get(state)
                    if cmd:
                        send_cmd(cmd)

        # trigger volume (held = repeat)
        if self.held_triggers and now - self.vol_last >= 0.18:
            self.vol_last = now
            for btn in self.held_triggers:
                direction = self.trigger_vol[btn]
                sh(["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@",
                    VOLUME_STEP + ("+" if direction > 0 else "-")])

    def handle(self, ev):
        et, ec, val = ev.type, ev.code, ev.value

        if self.debug_left > 0 and et in (ecodes.EV_KEY, ecodes.EV_ABS):
            self.debug_left -= 1
            _log(f"{self.dev.path}: {evdev.categorize(ev)}")

        if et == ecodes.EV_KEY:
            # triggers as volume (consume, don't forward)
            if ec in self.trigger_vol:
                if val:
                    self.held_triggers.add(ec)
                    self.vol_last = 0.0
                else:
                    self.held_triggers.discard(ec)
                return
            if ec == ecodes.BTN_START and val == 1:
                _log("Start -> home screen")
                sh(LAUNCHER_CMD)
                return
            if ec == ecodes.BTN_MODE and val == 1:
                _log("Guide -> power menu")
                sh(POWER_CMD)
                return
            if ec in BUTTON_MAP:
                # A/B/LB/RB also go straight to the shell over UDP
                if val == 1 and ec in BTN_CMDS:
                    send_cmd(BTN_CMDS[ec])
                self.key(BUTTON_MAP[ec], val)
                return

        elif et == ecodes.EV_ABS:
            if ec == ecodes.ABS_HAT0X:
                self.hat_state[0] = val
            elif ec == ecodes.ABS_HAT0Y:
                self.hat_state[1] = val
            # left stick is intentionally unmapped: the cursor must stay
            # hidden while the pad is in use

    def close(self):
        self.release_all()
        try:
            self.dev.ungrab()
        except OSError:
            pass
        try:
            self.dev.close()
        except OSError:
            pass
        try:
            self.ui.close()
        except OSError:
            pass
        _log(f"detached: {self.dev.path}")


def attach(path, dev):
    """Create a Pad; a failed grab/uinput must not kill the daemon."""
    try:
        return Pad(dev)
    except (OSError, PermissionError) as e:
        _log(f"cannot use {path} ({getattr(dev, 'name', '?')}): {e}\n"
             "  hint: user needs the 'input' group (/dev/input rw) and\n"
             "  write access to /dev/uinput (see installer udev rule)")
        try:
            dev.close()
        except OSError:
            pass
        return None


def main():
    _log(f"--- gamepad daemon starting (pid {os.getpid()}) ---")
    pads = {}
    for dev in find_pads():
        pad = attach(dev.path, dev)
        if pad:
            pads[dev.path] = pad
    if not pads:
        _log("no controllers found yet; will rescan every 2s")

    known = set()
    import selectors
    last_retry = time.monotonic()
    try:
        while True:
            # hotplug: rescan every 2s; retry failed pads every 15s
            # (the shell releases its grab when a game launches, so the
            # daemon can then take the pad over for volume etc.)
            if time.monotonic() - last_retry > 15:
                last_retry = time.monotonic()
                known.clear()
            for path in sorted(glob.glob("/dev/input/event*")):
                if path in known or path in pads:
                    continue
                try:
                    dev = InputDevice(path)
                except (PermissionError, OSError):
                    continue
                keys = dev.capabilities().get(ecodes.EV_KEY, [])
                if ecodes.BTN_SOUTH in keys and ecodes.BTN_START in keys:
                    pad = attach(path, dev)
                    if pad:
                        pads[path] = pad
                    else:
                        known.add(path)
                else:
                    dev.close()
                    known.add(path)

            if not pads:
                time.sleep(2.0)
                known.clear()
                continue

            sel = selectors.DefaultSelector()
            for p in pads.values():
                sel.register(p.dev, selectors.EVENT_READ, p)
            events = sel.select(timeout=0.05)
            for key, _ in events:
                pad = key.data
                try:
                    for ev in pad.dev.read():
                        pad.handle(ev)
                except OSError as e:
                    _log(f"read error on {pad.dev.path}: {e}")
                    pad.close()
                    pads = {k: v for k, v in pads.items() if v is not pad}
            sel.close()

            for pad in list(pads.values()):
                pad.tick(time.monotonic())
    except KeyboardInterrupt:
        pass
    finally:
        for p in pads.values():
            p.close()
        _log("--- gamepad daemon stopped ---")


if __name__ == "__main__":
    main()
