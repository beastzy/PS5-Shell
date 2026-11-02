#!/usr/bin/env python3
"""Apply the Console DE scale to labwc's primary display output."""
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

HOME = Path.home()
LOG = HOME / ".cache/console-de-output-scale.log"


def log(message):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as stream:
        stream.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")


def enabled_outputs():
    result = subprocess.run(["wlr-randr"], check=True, capture_output=True,
                            text=True, timeout=8)
    outputs = []
    current = None
    for line in result.stdout.splitlines():
        header = re.match(r'^([^\s]+)\s+"', line)
        if header:
            if current:
                outputs.append(current)
            current = {"name": header.group(1), "enabled": False,
                       "position": None}
            continue
        if current is None:
            continue
        text = line.strip()
        if text == "Enabled: yes":
            current["enabled"] = True
        elif text.startswith("Position:"):
            match = re.search(r"(-?\d+)\s*,\s*(-?\d+)", text)
            if match:
                current["position"] = (int(match.group(1)), int(match.group(2)))
    if current:
        outputs.append(current)
    return [item for item in outputs if item["enabled"]]


def set_xwayland_scale(scale):
    xprop = shutil.which("xprop")
    if not xprop or not os.environ.get("DISPLAY"):
        log("XWayland scaling hint skipped (xprop or DISPLAY unavailable)")
        return
    command = [xprop, "-root", "-f", "_XWAYLAND_GLOBAL_OUTPUT_SCALE", "32c",
               "-set", "_XWAYLAND_GLOBAL_OUTPUT_SCALE", str(scale)]
    result = subprocess.run(command, capture_output=True, text=True,
                            timeout=2, check=False)
    if result.returncode == 0:
        log(f"XWayland scale hint set to {scale}")
        return
    # XWayland may not exist until the first X11 app starts. Retry in the
    # background so the shell can start immediately after the output changes.
    subprocess.Popen(
        [os.sys.executable, str(Path(__file__).resolve()), "xwayland", str(scale)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, start_new_session=True)
    log("XWayland is not ready yet; scheduled a background scale-hint retry")


def main():
    scale_file = HOME / ".config/console-de/scale"
    try:
        scale = int(float(scale_file.read_text(encoding="utf-8").strip()))
    except Exception:
        scale = 3
    scale = min(4, max(1, scale))

    outputs = enabled_outputs()
    if not outputs:
        raise RuntimeError("wlr-randr reported no enabled outputs")
    primary = next((item for item in outputs if item["position"] == (0, 0)), outputs[0])
    subprocess.run(["wlr-randr", "--output", primary["name"], "--scale", str(scale)],
                   check=True, timeout=10)
    log(f"Applied labwc output scale {scale} to primary output {primary['name']}")
    set_xwayland_scale(scale)


def retry_xwayland_scale(scale):
    xprop = shutil.which("xprop")
    if not xprop or not os.environ.get("DISPLAY"):
        return
    command = [xprop, "-root", "-f", "_XWAYLAND_GLOBAL_OUTPUT_SCALE", "32c",
               "-set", "_XWAYLAND_GLOBAL_OUTPUT_SCALE", str(scale)]
    for _ in range(20):
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=3, check=False)
        if result.returncode == 0:
            log(f"XWayland scale hint set to {scale}")
            return
        time.sleep(0.5)
    log("XWayland scale hint unavailable; native Wayland scaling remains active")


if __name__ == "__main__":
    try:
        if len(os.sys.argv) == 3 and os.sys.argv[1] == "xwayland":
            retry_xwayland_scale(int(os.sys.argv[2]))
        else:
            main()
    except Exception as error:
        log(f"Could not apply display scale: {error}")
