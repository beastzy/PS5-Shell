#!/usr/bin/env python3
import os
"""Console DE - PS5-style home shell."""

import glob
import csv
import configparser
import json
import math
import os
import re
import select
import shlex
import signal
import shutil
import subprocess
import sys
import threading
import time

HOME = os.path.expanduser("~")


def _is_virtual_machine():
    """Identify VMs so the CPU-drawn intro can use a cheaper frame profile."""
    try:
        with open("/proc/cpuinfo", errors="ignore") as cpuinfo:
            if any("hypervisor" in line.split() for line in cpuinfo
                   if line.startswith("flags") or line.startswith("Features")):
                return True
    except OSError:
        pass
    identity = ""
    for path in ("/sys/class/dmi/id/sys_vendor",
                 "/sys/class/dmi/id/product_name"):
        try:
            with open(path, errors="ignore") as dmi_file:
                identity += " " + dmi_file.read()
        except OSError:
            pass
    identity = identity.lower()
    return any(token in identity for token in (
        "qemu", "kvm", "virtualbox", "vmware", "bochs", "parallels",
        "xen", "bhyve", "virt-manager",
    ))


_LOW_PERF_MODE = _is_virtual_machine()
_ANIMATION_INTERVAL_MS = 33 if _LOW_PERF_MODE else 16

# Set GDK_SCALE BEFORE importing GTK — must happen at module level
# The DE shell keeps a fixed 3x visual design. The setting file controls
# only labwc's output scale and the scale passed to launched applications.
_scale = 3

# Reference resolution from the existing installation; percentage-based
# geometry already adapts, while pixel-sized text/icons need the same ratio.
_REFERENCE_WIDTH = 2880
_REFERENCE_HEIGHT = 1800
_render_scale = float(_scale)

_menu_font_size_file = os.path.expanduser("~/.config/console-de/menu_font_size")
try:
    with open(_menu_font_size_file) as f:
        _menu_font_size = float(f.read().strip())
except Exception:
    _menu_font_size = 1.0

_menu_font_weight_file = os.path.expanduser("~/.config/console-de/menu_font_weight")
try:
    with open(_menu_font_weight_file) as f:
        _menu_font_weight = int(f.read().strip())
except Exception:
    _menu_font_weight = 600

# The shell scales its own layout manually; keep GTK at 1x so its widgets
# are not scaled a second time by the compositor output scale.
_env_scale = os.environ.get("GDK_SCALE", "unset")
os.environ["GDK_SCALE"] = "1"
os.environ["GDK_DPI_SCALE"] = "1"

# Log before GTK import
try:
    with open(os.path.expanduser("~/.cache/console-de-shell.log"), "a") as _f:
        _f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} "
                 f"SCALE: file={_scale} env_was={_env_scale} now={os.environ['GDK_SCALE']}\n")
except Exception:
    pass

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gtk, Gdk, GLib, GdkPixbuf, Gio, Pango

try:
    gi.require_version("GtkLayerShell", "0.1")
    from gi.repository import GtkLayerShell
    HAVE_LAYER_SHELL = True
except (ImportError, ValueError):
    GtkLayerShell = None
    HAVE_LAYER_SHELL = False


def _layer_shell_available():
    """Return whether this Wayland compositor actually advertises layer-shell."""
    if (not HAVE_LAYER_SHELL or
            os.environ.get("XDG_SESSION_TYPE", "").lower() != "wayland"):
        return False
    try:
        return bool(GtkLayerShell.is_supported())
    except Exception as exc:
        _log(f"Layer Shell support check failed; using GTK windows: {exc}")
        return False

try:
    import cairo
    HAVE_CAIRO = True
except ImportError:
    HAVE_CAIRO = False

try:
    import evdev
    from evdev import ecodes
    HAVE_EVDEV = True
except ImportError:
    HAVE_EVDEV = False

try:
    import cairo
    HAVE_CAIRO = True
except ImportError:
    HAVE_CAIRO = False

def _log(msg):
    try:
        with open(os.path.expanduser("~/.cache/console-de-shell.log"), "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


_SETTINGS_GRADIENT_CACHE = {}


def _paint_settings_gradient(cr, w, h):
    """Paint the reference gradient shared by Settings and startup."""
    if w <= 0 or h <= 0:
        return
    key = (int(w), int(h))
    if key not in _SETTINGS_GRADIENT_CACHE:
        image_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "settings_startup_gradient.png")
        try:
            image = GdkPixbuf.Pixbuf.new_from_file(image_path)
            if image.get_width() != w or image.get_height() != h:
                image = image.scale_simple(
                    int(w), int(h), GdkPixbuf.InterpType.BILINEAR)
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(w), int(h))
            surface_cr = cairo.Context(surface)
            Gdk.cairo_set_source_pixbuf(surface_cr, image, 0, 0)
            surface_cr.paint()
            surface.flush()
            _SETTINGS_GRADIENT_CACHE[key] = surface
        except Exception as exc:
            _log(f"Reference gradient load failed: {exc}")
            _SETTINGS_GRADIENT_CACHE[key] = None
    surface = _SETTINGS_GRADIENT_CACHE[key]
    if surface is not None:
        cr.set_source_surface(surface, 0, 0)
        cr.paint()
        return

    # Fall back to a vector gradient if the reference texture is unavailable.
    base = cairo.LinearGradient(0, 0, w, 0)
    base.add_color_stop_rgb(0.0, 0.25, 0.24, 0.25)
    base.add_color_stop_rgb(0.42, 0.12, 0.12, 0.15)
    base.add_color_stop_rgb(1.0, 0.035, 0.045, 0.07)
    cr.set_source(base)
    cr.rectangle(0, 0, w, h)
    cr.fill()

    lx, ly = w * 0.08, -h * 0.02
    glow = cairo.RadialGradient(lx, ly, 1, lx, ly, max(w, h) * 0.92)
    glow.add_color_stop_rgba(0.0, 0.94, 0.80, 0.74, 0.31)
    glow.add_color_stop_rgba(0.24, 0.78, 0.72, 0.70, 0.18)
    glow.add_color_stop_rgba(0.56, 0.53, 0.54, 0.58, 0.06)
    glow.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.0)
    cr.set_source(glow)
    cr.rectangle(0, 0, w, h)
    cr.fill()

    lower_glow = cairo.RadialGradient(
        w * 0.02, h * 0.83, 1, w * 0.02, h * 0.83, max(w, h) * 0.48)
    lower_glow.add_color_stop_rgba(0.0, 0.66, 0.67, 0.69, 0.12)
    lower_glow.add_color_stop_rgba(1.0, 0.40, 0.42, 0.46, 0.0)
    cr.set_source(lower_glow)
    cr.rectangle(0, 0, w, h)
    cr.fill()

def _px(val, m=1.0):
    return int(val * m)


def _cubic_bezier_x(t, x1, x2):
    """Compute x coordinate of cubic-bezier curve at parameter t."""
    return 3 * (1.0 - t) ** 2 * t * x1 + 3 * (1.0 - t) * t ** 2 * x2 + t ** 3


def _cubic_bezier_y(x, x1=0.16, y1=1.0, x2=0.3, y2=1.0):
    """Invert cubic-bezier: given x (progress 0-1), find y (eased progress)."""
    lo, hi = 0.0, 1.0
    for _ in range(20):
        t = (lo + hi) / 2.0
        xt = _cubic_bezier_x(t, x1, x2)
        if xt < x:
            lo = t
        else:
            hi = t
    t = (lo + hi) / 2.0
    return 3 * (1.0 - t) ** 2 * t * y1 + 3 * (1.0 - t) * t ** 2 * y2 + t ** 3


def _font_px(base):
    """Font/pixel size scaled by user preference and reference resolution."""
    return int(base * _render_scale)

def sh(cmd):
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        _log(f"sh error: {e}")


def _gpu_offload_env():
    """Env vars to force dGPU offload for launched games."""
    import os as _os
    env = dict(_os.environ)
    # Launched apps use the same scale as the labwc output. The shell itself
    # stays at GDK_SCALE=1 because it scales its UI manually.
    try:
        with open(_os.path.expanduser("~/.config/console-de/scale")) as scale_file:
            app_scale = str(max(1, int(float(scale_file.read().strip()))))
    except Exception:
        app_scale = "3"
    env["GDK_SCALE"] = app_scale
    env["GDK_DPI_SCALE"] = "1"
    env["QT_SCALE_FACTOR"] = app_scale
    gpu = "nvidia"
    try:
        gpu = open(_os.path.expanduser("~/.config/console-de/gpu")).read().strip()
    except Exception:
        pass
    if gpu == "nvidia":
        env["__NV_PRIME_RENDER_OFFLOAD"] = "1"
        env["__GLX_VENDOR_LIBRARY_NAME"] = "nvidia"
    elif gpu == "amd":
        env["DRI_PRIME"] = "1"
    # gpu == "off": no overrides
    return env

def take_screenshot():
    """Save screenshot to ~/Pictures/Screenshots/ using Console DE's built-in tool."""
    import shutil as _shutil
    outdir = os.path.expanduser("~/Pictures/Screenshots")
    os.makedirs(outdir, exist_ok=True)
    filename = os.path.join(outdir, f"screenshot-{time.strftime('%Y-%m-%d-%H%M%S')}.png")

    # 1. Console DE's built-in screenshot script (grim — installed by install.sh)
    script = _shutil.which("console-de-screenshot")
    if script:
        try:
            result = subprocess.run(
                [script], capture_output=True, text=True, timeout=8)
            out = result.stdout.strip().splitlines()
            saved = out[-1] if out else ""
            if saved and os.path.exists(saved):
                _log(f"screenshot saved: {saved}")
                return saved
            _log(f"console-de-screenshot failed: {result.stderr[:150]}")
        except Exception as e:
            _log(f"console-de-screenshot error: {e}")

    # 2. grim directly (same tool the script uses)
    grim = _shutil.which("grim")
    if grim:
        try:
            subprocess.run([grim, filename], timeout=8, check=True)
            if os.path.exists(filename):
                _log(f"screenshot saved (grim): {filename}")
                return filename
        except Exception as e:
            _log(f"grim failed: {e}")

    _log("no screenshot tool available (grim missing — run install.sh)")
    return None

def _steam_library_paths():
    """Discover all Steam library folders (including external drives)."""
    paths = set()
    data_home = os.environ.get(
        "XDG_DATA_HOME", os.path.expanduser("~/.local/share"))
    candidates = [
        os.environ.get("STEAM_COMPAT_CLIENT_INSTALL_PATH", ""),
        os.path.expanduser("~/.steam/root"),
        os.path.expanduser("~/.steam/steam"),
        os.path.join(data_home, "Steam"),
        os.path.expanduser(
            "~/.var/app/com.valvesoftware.Steam/.local/share/Steam"),
        os.path.expanduser(
            "~/.var/app/com.valvesoftware.Steam/data/Steam"),
        "/usr/share/steam",
        "/usr/share/Steam",
        "/opt/steam",
    ]
    for base in candidates:
        if not base or not os.path.isdir(base):
            continue
        base = os.path.realpath(os.path.expanduser(base))
        vdf = os.path.join(base, "steamapps", "libraryfolders.vdf")
        if os.path.exists(vdf):
            try:
                with open(vdf, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                for m in re.finditer(r'"path"\s+"([^"]+)"', content):
                    p = m.group(1).replace('\\\\', '/')
                    if os.path.exists(p):
                        paths.add(os.path.realpath(p))
            except Exception:
                pass
        # Default steamapps
        sa = os.path.join(base, "steamapps")
        if os.path.exists(sa):
            paths.add(base)
    return sorted(paths)


def _art_candidates(appid, prefer):
    """Yield art paths for an appid, ordered by preference.
    
    prefer: 'tile' (capsule first), 'title' (logo.png first), 'bg' (hero first)
    """
    for lib in _steam_library_paths():
        for base in [
            os.path.join(lib, "appcache", "librarycache"),
            os.path.join(lib, "steamapps", "appcache", "librarycache"),
        ]:
            appdir = os.path.join(base, str(appid))
            if os.path.isdir(appdir):
                # Direct files in appdir
                if prefer == 'tile':
                    suffixes = ("library_capsule.jpg", "library_hero.jpg",
                                "library_600x900.jpg", "header.jpg")
                elif prefer == 'title':
                    suffixes = ("logo.png", "library_capsule.jpg",
                                "library_hero.jpg")
                else:  # bg
                    suffixes = ("library_hero.jpg", "library_600x900.jpg",
                                "header.jpg", "library_capsule.jpg")
                for suffix in suffixes:
                    p = os.path.join(appdir, suffix)
                    if os.path.exists(p):
                        yield p
                # Hash-named subdirs
                try:
                    for entry in os.listdir(appdir):
                        sub = os.path.join(appdir, entry)
                        if not os.path.isdir(sub):
                            continue
                        if prefer == 'tile':
                            sub_suffixes = ("library_capsule.jpg", "library_hero.jpg",
                                            "library_600x900.jpg", "header.jpg")
                        elif prefer == 'title':
                            sub_suffixes = ("logo.png", "library_capsule.jpg",
                                            "library_hero.jpg")
                        else:
                            sub_suffixes = ("library_hero.jpg", "library_600x900.jpg",
                                            "header.jpg", "library_capsule.jpg")
                        for suffix in sub_suffixes:
                            p = os.path.join(sub, suffix)
                            if os.path.exists(p):
                                yield p
                except Exception:
                    pass
            # Flat fallback
            if prefer == 'tile':
                flat_suffixes = ("library_capsule.jpg", "library_hero.jpg",
                                 "library_600x900.jpg", "header.jpg")
            elif prefer == 'title':
                flat_suffixes = ("logo.png", "library_capsule.jpg",
                                 "library_hero.jpg")
            else:
                flat_suffixes = ("library_hero.jpg", "library_600x900.jpg",
                                 "header.jpg", "library_capsule.jpg")
            for suffix in flat_suffixes:
                p = os.path.join(base, f"{appid}_{suffix}")
                if os.path.exists(p):
                    yield p
        # Glob fallback: any image matching appid anywhere under librarycache
        for base in [os.path.join(lib, "appcache", "librarycache"),
                     os.path.join(lib, "steamapps", "appcache", "librarycache")]:
            if not os.path.exists(base):
                continue
            for p in glob.glob(os.path.join(base, "**", f"*{appid}*"), recursive=True):
                if p.lower().endswith((".jpg", ".jpeg", ".png")):
                    yield p


def _find_tile_art(appid):
    """Square box/capsule art for game tiles. Prefers capsule.jpg."""
    if not appid:
        return None
    candidates = list(_art_candidates(appid, 'tile'))
    if not candidates:
        return None
    # Prefer capsule art (square) for tiles
    for p in candidates:
        if 'capsule' in p.lower():
            return p
    return candidates[0]


def _find_title_art(appid):
    """Stylized title logo (logo.png ONLY) for display above Play button.
    Returns None if no logo.png exists — never falls back to tile art."""
    if not appid:
        return None
    candidates = list(_art_candidates(appid, 'title'))
    if not candidates:
        return None
    # Only accept logo.png — never tile/capsule art
    for p in candidates:
        if p.lower().endswith('logo.png'):
            return p
    return None


def _find_bg_art(appid):
    """Wide key art for full-bleed background. Prefers hero.jpg."""
    if not appid:
        return None
    candidates = list(_art_candidates(appid, 'bg'))
    if not candidates:
        return None
    # Prefer hero art for background
    for p in candidates:
        if 'hero' in p.lower():
            return p
    return candidates[0]


def _bg_art_for(game):
    """Find the best 