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
    """Find the best wide key art for a full-bleed background."""
    if not game.get('steam'):
        return None
    appid = game.get('appid')
    return _find_bg_art(appid)


def _cover_pixbuf(path, sw, sh):
    """Load image scaled to COVER the screen (crop, no letterbox)."""
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file(path)
    except Exception:
        return None
    pw, ph = pb.get_width(), pb.get_height()
    if pw <= 0 or ph <= 0:
        return None
    scale = max(sw / pw, sh / ph)
    nw, nh = max(1, int(pw * scale)), max(1, int(ph * scale))
    pb = pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)
    if nw > sw or nh > sh:
        x = max(0, (nw - sw) // 2)
        y = max(0, (nh - sh) // 2)
        pb = pb.new_subpixbuf(x, y, min(sw, nw), min(sh, nh)).copy()
    return pb


class GameTile(Gtk.DrawingArea):
    """Single PS5-style game tile: art clipped to a rounded squircle."""

    def __init__(self, game, tile_w, tile_h, selected=False):
        super().__init__()
        self.set_size_request(tile_w, tile_h)
        self.game = game
        self.selected = selected
        self.tile_w = tile_w
        self.tile_h = tile_h
        self.pb = None
        self.connect("draw", self._on_draw)
        self._load_art()

    def set_morph(self, w, h, ring_alpha):
        """Update animated size + selection ring alpha (morph transition)."""
        self.set_size_request(int(w), int(h))
        self.selected = ring_alpha > 0.5
        self.queue_draw()

    def _load_art(self):
        art = self._get_game_art()
        if art:
            _log(f"Tile art found: {art}")
            try:
                self.pb = _cover_pixbuf(art, self.tile_w, self.tile_h)
                if self.pb:
                    _log(f"Tile art pixbuf created: {self.pb.get_width()}x{self.pb.get_height()}")
                else:
                    _log("Tile art pixbuf is None")
            except Exception as e:
                _log(f"Tile art load failed: {e}")
        else:
            _log(f"No tile art for {self.game.get('name')}")

    def _get_game_art(self):
        if not self.game.get('steam'):
            return None
        appid = self.game.get('appid')
        if not appid:
            return None
        return _find_tile_art(appid)

    def _on_draw(self, widget, cr):
        w = self.tile_w
        h = self.tile_h
        radius = _font_px(14)  # match CSS border-radius (decreased)

        # Draw dark rounded background
        cr.set_source_rgba(0.11, 0.13, 0.16, 1.0)
        cr.new_sub_path()
        cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        cr.arc(w - radius, radius, radius, 3 * math.pi / 2, 2 * math.pi)
        cr.arc(w - radius, h - radius, radius, 0, math.pi / 2)
        cr.arc(radius, h - radius, radius, math.pi / 2, math.pi)
        cr.close_path()
        cr.fill()

        # Clip to the rounded rectangle so art respects corners
        cr.new_sub_path()
        cr.arc(radius, radius, radius, math.pi, 3 * math.pi / 2)
        cr.arc(w - radius, radius, radius, 3 * math.pi / 2, 2 * math.pi)
        cr.arc(w - radius, h - radius, radius, 0, math.pi / 2)
        cr.arc(radius, h - radius, radius, math.pi / 2, math.pi)
        cr.close_path()
        cr.clip()

        # Draw art filling the tile
        if self.pb:
            Gdk.cairo_set_source_pixbuf(cr, self.pb, 0, 0)
            cr.paint()
        else:
            # Fallback: game name text
            cr.set_source_rgba(1, 1, 1, 1)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL,
                                cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(_font_px(14))
            text = self.game['name'][:12]
            xb, yb, w_txt, h_txt, _, _ = cr.text_extents(text)
            cr.move_to((w - w_txt) / 2 - xb, (h - h_txt) / 2 - yb)
            cr.show_text(text)

        # White ring border when selected
        if self.selected:
            cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
            cr.set_line_width(_font_px(3))
            cr.new_sub_path()
            cr.arc(radius, radius, radius - _font_px(1),
                   math.pi, 3 * math.pi / 2)
            cr.arc(w - radius, radius, radius - _font_px(1),
                   3 * math.pi / 2, 2 * math.pi)
            cr.arc(w - radius, h - radius, radius - _font_px(1),
                   0, math.pi / 2)
            cr.arc(radius, h - radius, radius - _font_px(1),
                   math.pi / 2, math.pi)
            cr.close_path()
            cr.stroke()

        return False



class GradientBG(Gtk.DrawingArea):
    """Dark gradient painted over the background art for readability."""

    def __init__(self):
        super().__init__()
        self.connect("draw", self._on_draw)

    def _on_draw(self, widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        if HAVE_CAIRO:
            grad = cairo.LinearGradient(0, 0, w, 0)
            grad.add_color_stop_rgba(0.0, 0.02, 0.03, 0.05, 0.88)
            grad.add_color_stop_rgba(0.55, 0.02, 0.03, 0.05, 0.55)
            grad.add_color_stop_rgba(1.0, 0.02, 0.03, 0.05, 0.35)
            cr.set_source(grad)
        else:
            cr.set_source_rgba(0.02, 0.03, 0.05, 0.6)
        cr.paint()
        return False


def list_steam_games():
    games = []
    seen_appids = set()
    steam_paths = [os.path.join(lib, "steamapps")
                   for lib in _steam_library_paths()]
    skip_names = {'Proton', 'Steamworks Common Redist', 'Steam Linux Runtime',
                  'Steam Linux Runtime - Soldier', 'Steam Linux Runtime - Sniper',
                  'Proton Hotfix', 'Proton Experimental', 'SteamVR'}
    for steamapps in steam_paths:
        if not os.path.exists(steamapps):
            continue
        for manifest_path in glob.glob(os.path.join(steamapps, "appmanifest_*.acf")):
            try:
                with open(manifest_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                appid_match = re.search(r'"appid"\s+"(\d+)"', content)
                name_match = re.search(r'"name"\s+"([^"]+)"', content)
                if appid_match and name_match:
                    appid = appid_match.group(1)
                    name = name_match.group(1)
                    if appid in seen_appids:
                        continue
                    if any(name.startswith(s) for s in skip_names):
                        continue
                    seen_appids.add(appid)
                    games.append({'name': name, 'appid': appid, 'steam': True})
            except Exception:
                pass
    _log(f"Found {len(games)} Steam games")
    return games

def desktop_apps():
    """Return visible launchable desktop apps, honoring XDG precedence."""
    data_home = os.path.expanduser(os.environ.get(
        "XDG_DATA_HOME", os.path.join(HOME, ".local", "share")))
    data_dirs = [data_home, os.path.join(data_home, "flatpak", "exports", "share")]
    data_dirs.extend(os.path.expanduser(path) for path in
                     os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
                     if path)
    data_dirs.extend(("/var/lib/flatpak/exports/share", "/var/lib/snapd/desktop"))
    locale = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG", "")
    locale = locale.split(".", 1)[0].split("@", 1)[0]
    locale_keys = []
    if locale:
        locale_keys.append(f"Name[{locale}]")
        if "_" in locale:
            locale_keys.append(f"Name[{locale.split('_', 1)[0]}]")

    # Desktop file IDs are relative paths with '/' replaced by '-'. The first
    # matching entry wins, including Hidden entries that mask system entries.
    by_id = {}
    for data_dir in filter(None, data_dirs):
        applications_dir = os.path.join(os.path.expanduser(data_dir), "applications")
        if not os.path.isdir(applications_dir):
            continue
        for root, _dirs, filenames in os.walk(applications_dir):
            for filename in filenames:
                if filename.startswith(".") or not filename.endswith(".desktop"):
                    continue
                path = os.path.join(root, filename)
                desktop_id = os.path.relpath(path, applications_dir).replace(os.sep, "-")
                by_id.setdefault(desktop_id, path)

    apps = []
    for desktop_id, path in by_id.items():
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        parser.optionxform = str
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace") as desktop_file:
                parser.read_file(desktop_file)
            entry = parser["Desktop Entry"]
            if entry.get("Type", "Application") != "Application":
                continue
            if entry.getboolean("Hidden", fallback=False) or entry.getboolean("NoDisplay", fallback=False):
                continue
            name = next((entry[key].strip() for key in locale_keys
                         if entry.get(key, "").strip()), entry.get("Name", "").strip())
            command = entry.get("Exec", "").strip()
            if not name or not command:
                continue
            try_exec = entry.get("TryExec", "").strip()
            if try_exec:
                available = (os.access(try_exec, os.X_OK) if os.path.isabs(try_exec)
                             else shutil.which(try_exec) is not None)
                if not available:
                    continue
            command = re.sub(r"%[fFuUdDnNickvm]", "", command).strip()
            if not command:
                continue
            icon = entry.get("Icon", "").strip() or None
            apps.append({"name": name, "exec": command, "icon": icon,
                         "desktop_id": desktop_id, "desktop_file": path})
        except (OSError, KeyError, configparser.Error, ValueError):
            continue
    apps.sort(key=lambda app: (app["name"].casefold(), app["desktop_id"].casefold()))
    _log(f"Found {len(apps)} visible launchable desktop apps across "
         f"{len(by_id)} desktop entries")
    return apps


class ShellWindow(Gtk.ApplicationWindow):
    TABS = ["Games", "Apps"]

    def __init__(self, app):
        super().__init__(application=app, title="Console DE")
        screen = Gdk.Screen.get_default()
        self._primary_monitor_idx = 0
        self._primary_monitor = None
        if screen:
            try:
                self._primary_monitor_idx = screen.get_primary_monitor()
            except Exception:
                pass
            if self._primary_monitor_idx < 0:
                self._primary_monitor_idx = 0
            try:
                display = Gdk.Display.get_default()
                if display:
                    self._primary_monitor = display.get_monitor(
                        self._primary_monitor_idx)
            except Exception:
                pass
        self._shell_layer_shell = _layer_shell_available()
        if self._shell_layer_shell:
            # Keep the DE on a compositor-managed layer so Control Center's
            # Home action can raise it above fullscreen apps on Wayland.
            GtkLayerShell.init_for_window(self)
            GtkLayerShell.set_namespace(self, "console-de-shell")
            GtkLayerShell.set_layer(self, GtkLayerShell.Layer.TOP)
            if self._primary_monitor is not None:
                try:
                    GtkLayerShell.set_monitor(self, self._primary_monitor)
                    _log(f"Shell assigned to primary monitor {self._primary_monitor_idx}")
                except Exception as exc:
                    _log(f"Could not assign primary monitor to shell layer: {exc}")
            GtkLayerShell.set_keyboard_mode(
                self, GtkLayerShell.KeyboardMode.ON_DEMAND)
            GtkLayerShell.set_exclusive_zone(self, -1)
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                         GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(self, edge, True)
        elif (HAVE_LAYER_SHELL and
              os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"):
            _log("Compositor has no Layer Shell protocol; using standard GTK windows")
        # Keep the DE above startup applications such as Steam. Steam may show
        # a first-run/update window even when launched with -silent.
        self.set_keep_above(True)
        self.set_default_size(1280, 720)
        self.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0.05, 0.07, 0.10, 1.0))

        self._m = float(_scale)
        self._launch_black_screen = None
        self._launch_show_time = None
        self._launch_pre_procs = set()
        self._focus_out_time = None
        self._game_handoff_confirmed = False
        self._game_session_active = False
        self._game_handoff_time = None
        self._game_missing_since = None
        self._launch_game_seen_at = None
        self._game_check_timer_id = None

        # Get actual screen dimensions for responsive sizing
        if screen:
            try:
                geometry = screen.get_monitor_geometry(self._primary_monitor_idx)
                self._sw, self._sh = geometry.width, geometry.height
            except Exception:
                self._sw = screen.get_width()
                self._sh = screen.get_height()
        else:
            self._sw, self._sh = 1920, 1080
        if not self._shell_layer_shell:
            if screen and hasattr(self, "fullscreen_on_monitor"):
                self.fullscreen_on_monitor(screen, self._primary_monitor_idx)
            else:
                self.fullscreen()
        resolution_factor = min(
            self._sw / _REFERENCE_WIDTH,
            self._sh / _REFERENCE_HEIGHT)
        self._m = float(_scale) * resolution_factor
        global _render_scale
        _render_scale = self._m
        _log(f"Screen: {self._sw}×{self._sh}, user_scale={_scale}x, "
             f"reference_factor={resolution_factor:.3f}, "
             f"effective_ui_scale={self._m:.3f}x")

        # RGBA visual so overlays can be semi-transparent
        visual = screen.get_rgba_visual()
        if visual:
            self.set_visual(visual)

        # Apply scaled CSS fonts
        self._apply_css()

        self.games = list_steam_games()
        self.apps = desktop_apps()
        self.tab = 0
        self.tab_focus_idx = 0
        self.tab_bar_focused = False
        self._orbit_started_at = time.monotonic()
        self._orbit_timer_id = None
        self.gsel = 0
        self.asel = 0
        # Recently-launched item promoted to front a few seconds after launch
        self._promote_appid = None
        self._promote_name = None
        self._promote_timer_id = None
        # Steam client management (kept warm for fast game launches)
        # Remember game order across restarts (promoted games stay at front)
        self._load_game_order()
        self.settings_sel = 0
        self._update_check_status = "Press A to check"
        self._update_check_running = False
        self._installed_commit = self._read_installed_commit()
        # Cairo carousel state (PS5 tile belt model)
        self._car_tile = self._sw_pct(8)         # base tile size
        self._car_gap = self._sw_pct(1.5)        # gap between tiles
        self._car_sel_size = int(self._car_tile * 1.28)  # selected size
        # At rest, tile 0 starts where tile 1 used to appear in the original
        # layout: one selected-tile width plus a gap after the normal left inset.
        # This gives the belt room to slide left as the user browses forward.
        self._car_left_inset = self._sw_pct(2)
        self._car_first = self._car_left_inset + self._car_sel_size + self._car_gap
        self._car_stride = self._car_tile + self._car_gap
        # The ring's outer edge sits (offset + half the stroke) outside the tile. The selected
        # tile is pushed down by exactly that much so the ring's top lines up with the others.
        self._car_ring_off = self._sw_pct(0.3)
        self._car_ring_w = max(1, self._sw_pct(0.2))
        self._car_ring_margin = self._car_ring_off + self._car_ring_w / 2.0
        self._carousel_pixbufs = {}
        self._car_sel_from = 0       # selected index at anim start
        self._car_offset_from = 0.0  # belt offset (px) at anim start
        self._car_offset_to = 0.0    # belt offset (px) at anim end
        self._car_progress = 0.0     # eased 0..1 progress of the running animation
        self._car_animating = False
        self._car_id = None
        self._car_anim_id = None     # tick-callback id while animating
        self._car_offset_current = 0.0
        self._car_g_from = []        # per-tile 'selected growth' (0..1) when the animation began
        self._startup_hidden = False
        self._carousel_intro_started = False
        self._carousel_intro_active = False
        self._carousel_intro_start = None
        self._carousel_intro_id = None
        self._carousel_intro_duration = 1.7
        self._topbar_fade_active = False
        self._topbar_fade_start = None
        self._topbar_fade_duration = 0.65
        self._status_notices_started = False
        self._status_notice_fade_id = None
        self._status_notice_dismiss_id = None
        self._status_notice_fade_start = None
        self._status_notice_fade_from = 0.0
        self._status_notice_fade_to = 0.0
        self._status_notice_fade_duration = 0.45
        self._controller_battery_percent = None
        self._microphone_enabled = False
        self._microphone_state_initialized = False
        self._microphone_poll_active = False
        self._microphone_monitor_id = None
        self._settings_stats_timer_id = None
        self._settings_stats_last_cpu = time.process_time()
        self._settings_stats_last_wall = time.monotonic()
        self._settings_stats_cpu_percent = 0.0
        self._settings_stats_rss_mb = None
        self._settings_system_cpu_baseline = None
        self._settings_system_cpu_percent = None
        self._settings_system_memory = None
        self._process_log_path = None
        self._process_log_timer_id = None
        self.control_center_open = False
        self.control_center_selected = 10
        self.control_center_items = []
        self.control_center_icon_pixbufs = []
        self.control_center_power_open = False
        self.control_center_power_choice_idx = 0
        self.control_center_power_buttons = []
        self._cursor_hide_timer = None
        self._pointer_cursor_visible = False
        self._info_fade_delay = 0.5
        self._info_fade_start = None
        self._info_fade_duration = 1.5
        self._info_fade_delay_id = None
        self._info_fade_id = None

        # Play button focus state (D-pad navigable)
        self.play_btn_focused = False
        # More button focus state (D-pad navigable, right of Play button)
        self.more_btn_focused = False

        # Gamepad state
        self._pad_dev = None
        self._pad_src = None
        self._gamepad_scan_in_progress = False
        self._steam_game_scan_in_progress = False
        self._pad_devices = []
        self._pad_watch_ids = []
        self._capture_devices = []
        self._capture_watch_ids = []
        self._capture_device_codes = {}
        self._share_press_started = None
        self._raw_hid_capture_active = False
        self._raw_hid_capture_stop = threading.Event()
        self._raw_hid_capture_button_down = False
        self._gamepad_passthrough_active = False
        self._game_input_probe_remaining = 0
        self._guide_control_center_pending = False
        self._raw_hid_capture_stop.set()
        self._raw_hid_capture_active = False
        self._raw_hid_capture_button_down = False
        # Single source of truth for controller routing. The visually active
        # top layer owns navigation; the game owns input only while it is the
        # visible layer and Control Center is closed.
        self._controller_owner = "de"
        self._gamepad_regrab_timer = None
        self._home_foreground_timer = None
        self._background_suspend_timer = None
        self._background_suspend_attempts = 0
        self._background_game_suspended = False
        self._background_game_pids = set()
        self._background_muted_sink_inputs = {}
        self._background_audio_resume_timer = None
        self._background_audio_resume_attempts = 0
        self._focus_restore_timer = None
        self._control_center_return_to_game = False
        self._active_game_appid = None
        self._active_game_name = ""
        self._active_external_app = False
        self._active_external_launch_id = None
        self._opened_applications = []
        self._opened_application_limit = 4
        self._opened_application_sequence = 0
        self._user_requested_de_foreground = False
        self._last_gamepad_scan_log = 0.0
        self._capture_button_codes = set()
        self._hat_state = (0, 0)
        self._share_press_timer = None
        self._share_long_press_fired = False
        self._screen_record_action_busy = False
        self._screen_recording_active = False
        self._screen_record_proc = None
        self._screen_record_path = None
        self._trig_prev = (0, 0)

        _log(f"ShellWindow: {len(self.games)} games, {len(self.apps)} apps")

        # ---- Overlay layout: background art, gradient, content ----
        overlay = Gtk.Overlay()
        self._overlay = overlay
        self.add(overlay)

        self.bg_image = Gtk.Image()
        overlay.add(self.bg_image)

        # NOTE: the readability gradient is baked into the background pixbuf
        # (see _compose_background) instead of being a separate overlay child —
        # a full-screen overlay widget can end up stacking above the content
        # windows and cover UI elements.

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        main_box.set_hexpand(True)
        main_box.set_vexpand(True)
        # Topbar sits in its own overlay child (added below), so reserve its height here
        main_box.set_margin_top(self._sh_pct(8))
        main_box.set_opacity(0.0)  # hidden during startup, revealed by transition
        main_box.set_sensitive(False)
        self.main_box = main_box
        overlay.add_overlay(main_box)

        # Topbar as a separate overlay child: always allocated the full window
        # width, so content overflow (wide pages) can never push the clock
        # off the right edge of the screen.
        self._create_topbar(overlay)
        self._create_status_notices(overlay)
        self._create_content(main_box)

        self.connect("key-press-event", self.on_key)
        self.add_events(Gdk.EventMask.POINTER_MOTION_MASK)
        self.connect("motion-notify-event", self._on_pointer_motion)
        self.connect("destroy", self._on_destroy)

        # Background music (DE menu theme) — paused while a game/app is focused
        self._music_proc = None
        self._music_paused = False
        self.connect("focus-out-event", self._on_focus_out)
        self.connect("focus-in-event", self._on_focus_in)

        self._start_process_logging()

        self._set_tab(0)
        # Startup screen shown while Steam/services get ready in the background
        self._create_startup_screen(overlay)
        # Build the hidden Control Center up front, as in the working iteration.
        # Its current shortcut, startup timing, and toggle/input handling stay
        # unchanged; this only avoids constructing the UI on its first open.
        self._create_control_center(overlay)
        self.show_all()
        self._set_pointer_cursor_visible(False)
        self._orbit_last_frame_time = 0.0
        self._orbit_timer_id = self.add_tick_callback(self._tick_orbit_border)
        # Load carousel art pixbufs after window is shown
        GLib.timeout_add(200, self._load_carousel_pixbufs)

        # Start gamepad after window is shown
        GLib.timeout_add(500, self._init_gamepad)
        self._gamepad_monitor_id = GLib.timeout_add(
            1000, self._monitor_gamepad_connection)
        self._steam_game_refresh_timer_id = GLib.timeout_add_seconds(
            4, self._refresh_steam_game_list)
        # Launch audio pipeline once the window is visible
        GLib.timeout_add(100, self._start_music)
        # Ensure Steam is running in background (not suspended) for fast launches
        GLib.timeout_add(1500, self._ensure_steam_running)
        _log("ShellWindow created")

    def _set_shell_layer(self, layer):
        if not self._shell_layer_shell:
            return
        try:
            GtkLayerShell.set_layer(
                self, getattr(GtkLayerShell.Layer, layer.upper()))
            GtkLayerShell.set_keyboard_mode(
                self, GtkLayerShell.KeyboardMode.NONE if layer == "bottom"
                else (getattr(GtkLayerShell.KeyboardMode, "EXCLUSIVE", None)
                      if layer == "overlay" else
                      GtkLayerShell.KeyboardMode.ON_DEMAND))
        except Exception as exc:
            _log(f"DE layer change failed: {exc}")

    def _sw_pct(self, pct):
        """Width as percentage of screen width."""
        return int(self._sw * pct / 100)

    def _sh_pct(self, pct):
        """Height as percentage of screen height."""
        return int(self._sh * pct / 100)

    def _set_pointer_cursor_visible(self, visible):
        window = self.get_window()
        if window is None:
            return
        try:
            cursor_type = (Gdk.CursorType.ARROW if visible
                           else Gdk.CursorType.BLANK_CURSOR)
            cursor = Gdk.Cursor.new_for_display(self.get_display(), cursor_type)
            window.set_cursor(cursor)
            self._pointer_cursor_visible = visible
        except Exception as exc:
            _log(f"Pointer cursor visibility update failed: {exc}")

    def _on_pointer_motion(self, widget, event):
        self._set_pointer_cursor_visible(True)
        if self._cursor_hide_timer is not None:
            try:
                GLib.source_remove(self._cursor_hide_timer)
            except Exception:
                pass
        self._cursor_hide_timer = GLib.timeout_add(1000, self._hide_pointer_cursor)
        return False

    def _hide_pointer_cursor(self):
        self._cursor_hide_timer = None
        self._set_pointer_cursor_visible(False)
        return False

    def _font_px(self, base):
        """Font size scaled for readability."""
        return _px(base, self._m)

    def _apply_css(self):
        m = self._m
        css = f"""
        window {{
            background-color: #0d1117;
        }}
        label {{
            font-family: 'PS5 Font', 'Inter', 'Cantarell', sans-serif;
            color: white;
            font-size: {self._font_px(16)}px;
        }}
        #tab-label {{
            font-size: {self._font_px(int(10 * _menu_font_size))}px;
            font-weight: 500;
        }}
        #clock-label {{
            color: white;
            font-size: {self._font_px(20)}px;
            font-weight: 600;
        }}
        #game-title {{
            font-size: {self._font_px(34)}px;
            font-weight: 800;
        }}
        #game-subtitle {{
            font-size: {self._font_px(20)}px;
            font-weight: 400;
        }}
        #game-tile {{
            background-color: rgba(17, 19, 22, 1);
            border-radius: {self._font_px(14)}px;
        }}
        #app-tile {{
            background-image: linear-gradient(145deg,
                rgba(40, 50, 68, 0.96), rgba(13, 19, 30, 0.96));
            border: 1px solid rgba(185, 199, 224, 0.18);
            border-radius: {self._font_px(22)}px;
            transition: background-image 180ms ease,
                border-color 180ms ease;
        }}
        #app-tile.selected {{
            background-image: linear-gradient(145deg,
                rgba(57, 76, 108, 0.98), rgba(22, 31, 49, 0.98));
            border-color: rgba(185, 207, 255, 0.72);
        }}
        #app-tile label {{
            font-size: {self._font_px(14)}px;
            font-weight: 500;
        }}
        #play-label {{
            font-size: {self._font_px(16)}px;
            font-weight: 700;
            color: #ffffff;
        }}
        #play-button {{
            background-color: rgba(0.24, 0.24, 0.26, 0.4);
            border-radius: {self._font_px(24)}px;
        }}
        #play-button.highlighted {{
            background-color: rgba(1, 1, 1, 1);
        }}
        #play-button.highlighted #play-label {{
            color: #000000;
        }}
        #more-button {{
            background-color: rgba(0.24, 0.24, 0.26, 0.4);
            border-radius: {self._font_px(24)}px;
        }}
        #more-button.highlighted {{
            background-color: rgba(1, 1, 1, 1);
        }}
        #more-button.highlighted #more-label {{
            color: #000000;
        }}
        #more-label {{
            color: #ffffff;
            font-size: {self._font_px(16)}px;
        }}
        #panel-title {{
            font-size: {self._font_px(14)}px;
            font-weight: 500;
        }}
        #panel-value {{
            font-size: {self._font_px(14)}px;
            font-weight: 700;
        }}
        #system-notice-card {{
            background-color: transparent;
            background-image: none;
        }}
        """.encode()
        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(css)
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception as e:
            _log(f"CSS failed: {e}")

    def _create_topbar(self, parent):
        topbar = Gtk.EventBox()
        topbar.set_size_request(-1, self._sh_pct(8))
        topbar.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0.08, 0.10, 0.13, 0.0))

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        hbox.set_margin_start(self._sw_pct(3))
        hbox.set_margin_top(self._sh_pct(2.5))
        topbar.add(hbox)

        self.tab_labels = []
        self.tab_focus_frames = []
        for name in self.TABS:
            frame = Gtk.EventBox()
            frame.set_name("tab-focus-frame")
            frame.set_visible_window(True)
            frame.override_background_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0))
            frame.connect_after("draw", self._draw_orbit_widget)
            btn = Gtk.Label(label=name)
            btn.set_name("tab-label")
            btn.set_margin_top(self._font_px(8))
            btn.set_margin_bottom(self._font_px(8))
            frame.set_margin_start(self._sw_pct(1.5))
            frame.set_margin_end(self._sw_pct(1.5))
            btn.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.6, 0.6, 0.6, 1.0))
            frame.add(btn)
            hbox.pack_start(frame, False, False, 0)
            self.tab_labels.append(btn)
            self.tab_focus_frames.append(frame)

        hbox.pack_start(Gtk.Box(), True, True, 0)

        # ---- Right side icons: search, settings, profile, time ----
        icon_size = self._font_px(24)
        icon_spacing = self._sw_pct(2)
        self._top_icon_focus_diameter = round(icon_size * 1.65 * 0.8)
        self.top_icon_frames = []

        # Search icon (leftmost of right side)
        self.search_icon = Gtk.Image()
        self.search_icon.set_size_request(self._font_px(28), self._font_px(28))
        self.search_icon.set_valign(Gtk.Align.CENTER)
        self._load_icon(self.search_icon, "search", self._font_px(28))
        self._search_icon_original = self.search_icon.get_pixbuf()
        self._search_icon_selected = self._tint_pixbuf_black(
            self._search_icon_original)
        self._pack_top_icon(
            hbox, self.search_icon, icon_spacing, circular_focus=True)
        self.search_icon_frame = self.top_icon_frames[-1]

        # Settings icon
        self.settings_icon = Gtk.Image()
        self.settings_icon.set_size_request(icon_size, icon_size)
        self.settings_icon.set_valign(Gtk.Align.CENTER)
        self._load_icon(self.settings_icon, "settings", icon_size)
        self._settings_icon_original = self.settings_icon.get_pixbuf()
        self._settings_icon_selected = self._tint_pixbuf_black(
            self._settings_icon_original)
        self._pack_top_icon(
            hbox, self.settings_icon, icon_spacing, circular_focus=True)
        self.settings_icon_frame = self.top_icon_frames[-1]
        self.settings_icon_frame.connect(
            "button-press-event", self._on_settings_icon_press)

        # User profile picture
        self.profile_pic = Gtk.Image()
        self.profile_pic.set_size_request(icon_size, icon_size)
        self.profile_pic.set_valign(Gtk.Align.CENTER)
        self._load_profile_picture()
        self._pack_top_icon(hbox, self.profile_pic, icon_spacing)

        # Time (rightmost)
        self.clock_label = Gtk.Label(label="--:--")
        self.clock_label.set_name("clock-label")
        self.clock_label.set_halign(Gtk.Align.END)
        self.clock_label.set_valign(Gtk.Align.CENTER)
        self.clock_label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        self.clock_label.show()
        hbox.pack_start(self.clock_label, False, False, self._sw_pct(3))
        _log(f"CLOCK created: text={self.clock_label.get_text()} visible={self.clock_label.get_visible()}")

        # Overlay child (not packed into a box): full window width, on top of content
        topbar.set_halign(Gtk.Align.FILL)
        topbar.set_valign(Gtk.Align.START)
        topbar.set_opacity(0.0)  # hidden during startup, revealed by transition
        topbar.set_sensitive(False)
        self.topbar = topbar
        parent.add_overlay(topbar)
        self._update_clock()
        GLib.timeout_add_seconds(60, self._update_clock)

    def _create_status_notices(self, parent):
        """Create the two transient status cards shown at startup/controller connect."""
        notice_width = self._sw_pct(8)
        notice_height = max(1, int(notice_width * 144.0 / 256.0))
        notice_gap = self._sh_pct(0.7)
        self.status_notice_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=notice_gap)
        self.status_notice_box.set_size_request(
            notice_width, notice_height * 2 + notice_gap)
        self.status_notice_box.set_halign(Gtk.Align.END)
        self.status_notice_box.set_valign(Gtk.Align.START)
        self.status_notice_box.set_margin_end(self._sw_pct(1.6))
        self.status_notice_box.set_margin_top(self._sh_pct(11.5))
        self.status_notice_box.set_opacity(0.0)
        self.status_notice_box.set_no_show_all(False)

        self.controller_notice = Gtk.EventBox()
        self.controller_notice.set_name("system-notice-card")
        self.controller_notice.set_size_request(notice_width, notice_height)
        self.controller_notice.set_visible_window(True)
        self._controller_notice_background = self._load_status_notice_background(
            "status_notice_controller_bg.png")
        self._controller_notice_icon = self._load_status_notice_background(
            "status_notice_controller_icon.png")
        self._battery_notice_icon = self._load_status_notice_background(
            "status_notice_battery_outline.png")
        controller_draw = Gtk.DrawingArea()
        controller_draw.connect("draw", self._draw_controller_notice)
        self.controller_notice.add(controller_draw)
        self.status_notice_box.pack_start(self.controller_notice, False, False, 0)

        self.microphone_notice = Gtk.EventBox()
        self.microphone_notice.set_name("system-notice-card")
        self.microphone_notice.set_size_request(notice_width, notice_height)
        self.microphone_notice.set_visible_window(True)
        self._microphone_notice_background = self._load_status_notice_background(
            "status_notice_microphone_bg.png")
        self._microphone_notice_icon = self._load_status_notice_background(
            "status_notice_microphone_icon.png")
        microphone_draw = Gtk.DrawingArea()
        microphone_draw.connect("draw", self._draw_microphone_notice)
        self.microphone_notice.add(microphone_draw)
        self.status_notice_box.pack_start(self.microphone_notice, False, False, 0)

        parent.add_overlay(self.status_notice_box)
        self.status_notice_box.hide()

    def _create_control_center(self, parent):
        """Build the dimmed PS5-style control strip, hidden until Guide is pressed."""
        self.control_center_items.clear()
        self.control_center_power_buttons.clear()
        self.control_center_window = None
        if self._shell_layer_shell:
            # A GTK child overlay cannot cover another fullscreen client. Use
            # Wayland's OVERLAY layer for this surface so it stacks above games.
            self.control_center_window = Gtk.ApplicationWindow(
                application=self.get_application(), title="Console DE Control Center")
            self.control_center_window.set_decorated(False)
            self.control_center_window.set_skip_taskbar_hint(True)
            self.control_center_window.set_skip_pager_hint(True)
            self.control_center_window.set_accept_focus(False)
            self.control_center_window.set_app_paintable(True)
            screen = Gdk.Screen.get_default()
            if screen is not None and screen.get_rgba_visual() is not None:
                self.control_center_window.set_visual(screen.get_rgba_visual())
            self.control_center_window.override_background_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(0.0, 0.0, 0.0, 0.0))
            self.control_center_window.connect("key-press-event", self.on_key)
            GtkLayerShell.init_for_window(self.control_center_window)
            GtkLayerShell.set_namespace(
                self.control_center_window, "console-de-control-center")
            if self._primary_monitor is not None:
                try:
                    GtkLayerShell.set_monitor(
                        self.control_center_window, self._primary_monitor)
                except Exception as exc:
                    _log(f"Could not assign Control Center to primary monitor: {exc}")
            GtkLayerShell.set_layer(
                self.control_center_window, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_exclusive_zone(self.control_center_window, -1)
            GtkLayerShell.set_keyboard_mode(
                self.control_center_window, GtkLayerShell.KeyboardMode.NONE)
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                         GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(self.control_center_window, edge, True)

        self.control_center_overlay = Gtk.EventBox()
        self.control_center_overlay.set_visible_window(False)
        self.control_center_overlay.set_hexpand(True)
        self.control_center_overlay.set_vexpand(True)
        self.control_center_overlay.set_halign(Gtk.Align.FILL)
        self.control_center_overlay.set_valign(Gtk.Align.FILL)
        self.control_center_overlay.set_no_show_all(True)

        layer = Gtk.Overlay()
        layer.set_hexpand(True)
        layer.set_vexpand(True)
        self.control_center_overlay.add(layer)

        dim = Gtk.EventBox()
        dim.set_visible_window(True)
        dim.set_hexpand(True)
        dim.set_vexpand(True)
        dim.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0.006, 0.008, 0.012, 0.78))
        dim.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        dim.connect("button-press-event", self._on_control_center_dim_press)
        layer.add(dim)

        vignette = Gtk.DrawingArea()
        vignette.set_hexpand(True)
        vignette.set_vexpand(True)
        vignette.set_sensitive(False)
        vignette.connect("draw", self._draw_control_center_vignette)
        layer.add_overlay(vignette)

        dock = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        dock.set_hexpand(False)
        dock.set_halign(Gtk.Align.CENTER)
        dock.set_valign(Gtk.Align.END)
        dock.set_size_request(-1, self._sh_pct(8.4))
        dock.set_margin_bottom(self._sh_pct(3.5))
        layer.add_overlay(dock)

        names = (
            "Home", "Storage", "Notifications", "Friends", "Music",
            "Game Alerts", "Sound", "Microphone", "Controller", "Profile",
            "Power",
        )
        icon_names = (
            "home", "storage", "notifications", "friends", "music",
            "alert", "volume", "microphone", "controller", "profile", "power",
        )
        icon_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "icons", "control_center")
        cell_width = self._sw_pct(6.975)
        icon_height = self._sh_pct(4.48)
        for index, (name, icon_name) in enumerate(zip(names, icon_names)):
            cell = Gtk.EventBox()
            cell.set_visible_window(False)
            cell.set_size_request(cell_width, self._sh_pct(8.4))
            cell.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
            cell.connect("button-press-event", self._on_control_center_item_press, index)

            content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            content.set_halign(Gtk.Align.CENTER)
            content.set_valign(Gtk.Align.CENTER)
            image = Gtk.Image()
            pixbuf = None
            icon_ext = "png" if icon_name == "profile" else "svg"
            path = os.path.join(icon_dir, f"cc-{icon_name}.{icon_ext}")
            try:
                if icon_name == "profile":
                    source = self._get_profile_pixbuf()
                    if source is None:
                        source = GdkPixbuf.Pixbuf.new_from_file(path)
                else:
                    source = GdkPixbuf.Pixbuf.new_from_file(path)
                target_w = max(1, round(source.get_width() * icon_height / source.get_height()))
                pixbuf = source.scale_simple(
                    target_w, icon_height, GdkPixbuf.InterpType.BILINEAR)
                image.set_from_pixbuf(pixbuf)
            except Exception as exc:
                _log(f"Control center icon unavailable ({name}): {exc}")
            image.set_halign(Gtk.Align.CENTER)
            image.set_valign(Gtk.Align.CENTER)
            content.pack_start(image, False, False, 0)
            cell.add(content)
            dock.pack_start(cell, False, False, 0)
            self.control_center_items.append({
                "name": name, "widget": cell,
                "image": image, "pixbuf": pixbuf,
            })

        dock.set_size_request(cell_width * len(names), self._sh_pct(8.4))

        # Power actions sit directly above the Power icon, like a small
        # control-center popover instead of a centered system dialog.
        self.control_center_power_card = Gtk.Frame()
        self.control_center_power_card.set_no_show_all(True)
        self.control_center_power_card.set_shadow_type(Gtk.ShadowType.NONE)
        self.control_center_power_card.set_halign(Gtk.Align.START)
        self.control_center_power_card.set_valign(Gtk.Align.END)
        self.control_center_power_card.set_margin_start(
            self._sw_pct((100 - 6.975 * len(names)) / 2)
            + self._sw_pct(6.975) * 10
            + self._sw_pct(6.975 / 2) - self._sw_pct(6.5))
        self.control_center_power_card.set_margin_bottom(self._sh_pct(13.5))
        self.control_center_power_card.set_size_request(self._sw_pct(13), -1)
        self.control_center_power_card.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0.10, 0.12, 0.16, 0.98))
        power_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        for title, response in (("Log Out", 1), ("Shut Down", 2), ("Cancel", 0)):
            button = Gtk.Button(label=title)
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.set_size_request(-1, self._sh_pct(4.8))
            button.set_can_focus(True)
            button.connect("clicked", self._on_control_center_power_choice, response)
            self.control_center_power_buttons.append(button)
            power_box.pack_start(button, False, False, 0)
        self.control_center_power_card.add(power_box)
        layer.add_overlay(self.control_center_power_card)
        self.control_center_power_card.hide()
        self._update_control_center_power_focus()

        if self.control_center_window is not None:
            self.control_center_window.add(self.control_center_overlay)
        else:
            # Compositors without Layer Shell cannot place child content above
            # an unrelated fullscreen game; retain the best in-window fallback.
            parent.add_overlay(self.control_center_overlay)
        self.control_center_overlay.hide()
        if self.control_center_window is not None:
            self.control_center_window.hide()
        self._update_control_center_selection()
        _log("Control Center created as " +
             ("Wayland Layer Shell OVERLAY surface" if self.control_center_window
              else "in-window overlay (compositor lacks Layer Shell)"))

    def _destroy_control_center_surface(self):
        """Destroy the Control Center window/widget tree after it closes."""
        overlay = self.control_center_overlay
        window = self.control_center_window
        if window is not None:
            window.destroy()
        elif overlay is not None:
            try:
                self._overlay.remove_overlay(overlay)
            except Exception:
                pass
            try:
                overlay.destroy()
            except Exception:
                pass
        self.control_center_overlay = None
        self.control_center_window = None
        self.control_center_power_card = None
        self.control_center_power_buttons.clear()
        self.control_center_items.clear()
        _log("Control Center overlay destroyed")

    def _update_control_center_selection(self):
        if not self.control_center_items:
            return
        selected = max(0, min(self.control_center_selected,
                              len(self.control_center_items) - 1))
        for index, item in enumerate(self.control_center_items):
            item["image"].set_opacity(1.0 if index == selected else 0.72)

    def _move_control_center_selection(self, delta):
        self.control_center_selected = (
            self.control_center_selected + delta) % len(self.control_center_items)
        self._update_control_center_selection()
        self._play_sfx("PS5_Change")

    def _toggle_control_center(self):
        """Open or close Control Center above the current foreground surface."""
        if not self._startup_hidden:
            return
        if self._launch_black_screen is not None:
            self._hide_launch_black_screen()
        if self.control_center_open:
            self._close_control_center()
            return

        if not self.control_center_items:
            self._create_control_center(self._overlay)
        self._control_center_return_to_game = bool(
            self._active_external_app or
            (self._active_game_appid and
             not self._user_requested_de_foreground and
             self._is_launched_game_running()))
        self.control_center_open = True
        self.control_center_power_open = False
        self.control_center_selected = 0
        if self._control_center_return_to_game:
            self._set_shell_layer("bottom")
            self.set_accept_focus(False)
            self.set_keep_above(False)
        self._set_controller_owner("control_center")
        if self._control_center_return_to_game:
            self._regrab_gamepad()

        overlay = getattr(self, "control_center_overlay", None)
        window = getattr(self, "control_center_window", None)
        if overlay is not None:
            overlay.set_no_show_all(False)
            overlay.show_all()
        if window is not None:
            GtkLayerShell.set_layer(window, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_keyboard_mode(
                window, GtkLayerShell.KeyboardMode.NONE)
            # Layer Shell anchors already make this a full-screen surface.
            # Calling Gtk.Window.fullscreen() on it can leave only a small
            # corner of the widget tree visible under Labwc.
            window.show_all()
            window.present()
            _log("Control Center layer set to OVERLAY (visible=True)")
        elif overlay is not None:
            overlay.show()
        self._update_control_center_selection()
        self._play_sfx("PS5_Control_Center")
        _log("Control Center opened" +
             (" over foreground app; temporary controller capture active"
              if self._control_center_return_to_game else " over DE"))

    def _close_control_center(self, return_to_game=None, play_sound=True,
                              preserve_underlay=False):
        """Hide Control Center and restore the layer that was underneath it."""
        if not self.control_center_open:
            return False
        if return_to_game is None:
            return_to_game = self._control_center_return_to_game
        self.control_center_open = False
        self.control_center_power_open = False
        self._control_center_return_to_game = False
        if self.control_center_power_card is not None:
            self.control_center_power_card.hide()
            self.control_center_power_card.set_no_show_all(True)
        if self.control_center_window is not None:
            GtkLayerShell.set_layer(
                self.control_center_window, GtkLayerShell.Layer.BOTTOM)
            self.control_center_window.hide()
            _log("Control Center layer set to BOTTOM (visible=False)")
        elif self.control_center_overlay is not None:
            self.control_center_overlay.hide()

        game_is_return_target = bool(
            return_to_game and self._active_game_appid and
            self._is_launched_game_running())
        external_app_is_return_target = bool(
            return_to_game and self._active_external_app)
        if game_is_return_target or external_app_is_return_target:
            self._set_controller_owner("game")
            self._user_requested_de_foreground = False
            self._ungrab_gamepad()
            self.set_accept_focus(False)
            self._set_shell_layer("bottom")
            self.set_keep_above(False)
            _log("Control Center closed; released controller capture to foreground app")
        elif preserve_underlay:
            self._set_controller_owner("de")
            _log("Control Center closed; awaiting Labwc Home activation")
        else:
            self._set_controller_owner("de")
            if not self._user_requested_de_foreground:
                self.set_accept_focus(True)
                self._set_shell_layer("overlay")
                self.set_keep_above(True)
            _log("Control Center closed; DE remains the foreground layer")
        if play_sound:
            self._play_sfx("PS5_Control_Center")
        return False

    def _dispatch_control_center_shortcut(self):
        """Make Guide use the same Labwc shortcut as Super+Shift+Space."""
        if HAVE_EVDEV and self._send_labwc_keybind(
                ecodes.KEY_SPACE, shift=True):
            _log("Guide released; dispatched through Labwc Super+Shift+Space binding")
        else:
            _log("Guide Super+Shift+Space dispatch unavailable; toggling Control Center directly")
            self._toggle_control_center()
        return False

    def _draw_control_center_vignette(self, widget, cr):
        allocation = widget.get_allocation()
        width, height = allocation.width, allocation.height
        if width <= 0 or height <= 0:
            return False
        radius = math.hypot(width * 0.5, height * 0.5)
        gradient = cairo.RadialGradient(
            width * 0.5, height * 0.5, min(width, height) * 0.12,
            width * 0.5, height * 0.5, radius)
        gradient.add_color_stop_rgba(0.0, 0.0, 0.0, 0.0, 0.0)
        gradient.add_color_stop_rgba(0.55, 0.0, 0.0, 0.0, 0.08)
        gradient.add_color_stop_rgba(1.0, 0.0, 0.0, 0.0, 0.55)
        cr.set_source(gradient)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        return False

    def _on_control_center_dim_press(self, widget, event):
        self._toggle_control_center()
        return True

    def _on_control_center_item_press(self, widget, event, index):
        if index != self.control_center_selected:
            self.control_center_selected = index
            self._update_control_center_selection()
            self._play_sfx("PS5_Change")
        self._activate_control_center_item()
        return True

    def _activate_control_center_item(self):
        index = self.control_center_selected
        name = self.control_center_items[index]["name"]
        self._play_sfx("PS5_Select")
        if index == 0:
            _log("Control Center Home selected; dispatching Labwc Super+Tab")
            self._close_control_center(
                return_to_game=False, preserve_underlay=True)
            if HAVE_EVDEV and self._send_labwc_keybind(ecodes.KEY_TAB):
                _log("Control Center Home dispatched through Labwc Super+Tab binding")
            else:
                _log("Control Center Super+Tab dispatch unavailable; using direct Home fallback")
                self._bring_de_home(controller_already_owns_gamepad=True)
                self._set_tab(0)
                self._update_play_button_label()
        elif index == 4:
            if self._music_paused:
                self._resume_music()
            else:
                self._pause_music()
        elif index == 6:
            self._toggle_audio_mute("@DEFAULT_AUDIO_SINK@")
        elif index == 7:
            self._toggle_audio_mute("@DEFAULT_AUDIO_SOURCE@")
        elif index == 8:
            self._refresh_status_notice_data()
            self._show_toast("Controller connected" if self._pad_dev else
                             "No controller connected")
        elif index == 10:
            self._show_control_center_power_menu()
        else:
            self._show_toast(f"{name} control is not available in this DE")

    def _force_home_foreground(self, attempt=0):
        """Re-raise the shell after the Control Center layer has been hidden."""
        self._home_foreground_timer = None
        hold_home = bool(
            self._user_requested_de_foreground and self._active_game_appid and
            self._is_launched_game_running() and not self.control_center_open)
        # Re-map after changing from BACKGROUND to OVERLAY. Mutter can retain
        # the old layer ordering for a mapped layer-shell surface; a fresh
        # map commits the new layer and exclusive keyboard mode together.
        if attempt == 0 and self.get_mapped():
            self.hide()
        self._set_shell_layer("overlay")
        self.set_accept_focus(True)
        self.set_keep_above(True)
        self.deiconify()
        self._fullscreen_primary_monitor()
        self.show()
        self.present()
        self.present_with_time(Gdk.CURRENT_TIME)
        self.grab_focus()
        active = self.is_active()
        if attempt == 0 or (hold_home and attempt % 10 == 0):
            _log(f"Home foreground attempt {attempt + 1}; mapped={self.get_mapped()}; "
                 f"DE active={active}; keyboard mode=exclusive")
        if hold_home:
            self._set_controller_owner("de")
            if active:
                self._resume_music()
            self._home_foreground_timer = GLib.timeout_add(
                500, self._force_home_foreground, attempt + 1)
            return False
        if not active and attempt < 5:
            self._home_foreground_timer = GLib.timeout_add(
                120, self._force_home_foreground, attempt + 1)
        return False

    def _bring_de_home(self, controller_already_owns_gamepad=False):
        """Raise the existing DE and take controller ownership for navigation."""
        self._guide_control_center_pending = False
        if not self._active_game_appid:
            latest_game = self._latest_running_game_record()
            if latest_game is not None:
                self._activate_game_record(latest_game)
                self._game_session_active = True
                self._game_handoff_confirmed = True
                self._game_handoff_time = time.monotonic()
                self._game_missing_since = None
                if self._game_check_timer_id is None:
                    self._game_check_timer_id = GLib.timeout_add(
                        200, self._check_game_launched)
        game_running = bool(self._active_game_appid and
                            self._is_launched_game_running())
        if self.control_center_open:
            self._toggle_control_center()
        self._user_requested_de_foreground = game_running
        self._active_external_app = False
        self._active_external_launch_id = None
        self._set_controller_owner("de")
        if game_running:
            if not controller_already_owns_gamepad:
                self._regrab_gamepad()
            else:
                _log("Control Center controller grab retained by DE for Home")
            self._schedule_background_game_suspend()
            self._update_play_button_label()
        self.set_accept_focus(True)
        self._set_shell_layer("overlay")
        self.set_keep_above(True)
        self.deiconify()
        self._fullscreen_primary_monitor()
        self.show()
        self.present()
        self.present_with_time(Gdk.CURRENT_TIME)
        self.grab_focus()
        self._resume_music()
        self._schedule_home_foreground()
        _log("DE raised through the shared Home action")
        return False

    def _fullscreen_primary_monitor(self):
        """Keep GTK fallback fullscreening scoped to the selected output."""
        if self._shell_layer_shell:
            # The layer surface is already anchored to its primary monitor.
            return
        screen = Gdk.Screen.get_default()
        if screen and hasattr(self, "fullscreen_on_monitor"):
            self.fullscreen_on_monitor(screen, self._primary_monitor_idx)
        else:
            self.fullscreen()

    def _gnome_shell_eval(self, script):
        """Run a small GNOME Shell window-management action when available."""
        gdbus = shutil.which("gdbus")
        if not gdbus:
            _log("GNOME window action unavailable: gdbus is not installed")
            return False
        try:
            result = subprocess.run(
                [gdbus, "call", "--session", "--dest", "org.gnome.Shell",
                 "--object-path", "/org/gnome/Shell", "--method",
                 "org.gnome.Shell.Eval", script],
                capture_output=True, text=True, timeout=2)
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip()
                _log(f"GNOME window action unavailable: {detail[:240]}")
                return False
            output = result.stdout.strip()
            if not output.startswith("(true,"):
                _log(f"GNOME window action rejected: {output[:240]}")
                return None
            _log("GNOME window action completed")
            return output
        except Exception as exc:
            _log(f"GNOME window action failed: {exc}")
            return None

    def _send_labwc_keybind(self, key_code, shift=False):
        """Send a compositor keybind through uinput for labwc window actions."""
        if not HAVE_EVDEV:
            _log("labwc window shortcut unavailable: python-evdev is missing")
            return False
        keyboard = None
        try:
            meta = ecodes.KEY_LEFTMETA
            shift_key = ecodes.KEY_LEFTSHIFT
            keys = [meta]
            if shift:
                keys.append(shift_key)
            keys.append(key_code)
            keyboard = evdev.UInput(
                name="console-de-window-control",
                bustype=ecodes.BUS_USB,
                events={ecodes.EV_KEY: keys})
            keyboard.write(ecodes.EV_KEY, meta, 1)
            keyboard.syn()
            time.sleep(0.03)
            if shift:
                keyboard.write(ecodes.EV_KEY, shift_key, 1)
                keyboard.syn()
                time.sleep(0.03)
            keyboard.write(ecodes.EV_KEY, key_code, 1)
            keyboard.syn()
            time.sleep(0.03)
            keyboard.write(ecodes.EV_KEY, key_code, 0)
            keyboard.syn()
            if shift:
                keyboard.write(ecodes.EV_KEY, shift_key, 0)
                keyboard.syn()
            keyboard.write(ecodes.EV_KEY, meta, 0)
            keyboard.syn()
            return True
        except Exception as exc:
            _log(f"labwc window shortcut failed: {exc}")
            return False
        finally:
            if keyboard is not None:
                try:
                    keyboard.close()
                except Exception:
                    pass

    def _send_labwc_previous_window_immediate(self):
        """Ask labwc to focus the previous client without opening its switcher."""
        if not HAVE_EVDEV:
            _log("Previous-window activation unavailable: python-evdev is missing")
            return False
        keyboard = None
        try:
            meta = ecodes.KEY_LEFTMETA
            ctrl = ecodes.KEY_LEFTCTRL
            tab = ecodes.KEY_TAB
            keyboard = evdev.UInput(
                name="console-de-window-control",
                bustype=ecodes.BUS_USB,
                events={ecodes.EV_KEY: [meta, ctrl, tab]})
            for key in (meta, ctrl, tab):
                keyboard.write(ecodes.EV_KEY, key, 1)
                keyboard.syn()
                time.sleep(0.02)
            keyboard.write(ecodes.EV_KEY, tab, 0)
            keyboard.syn()
            keyboard.write(ecodes.EV_KEY, ctrl, 0)
            keyboard.syn()
            keyboard.write(ecodes.EV_KEY, meta, 0)
            keyboard.syn()
            return True
        except Exception as exc:
            _log(f"Previous-window activation failed: {exc}")
            return False
        finally:
            if keyboard is not None:
                try:
                    keyboard.close()
                except Exception:
                    pass

    def _focus_previous_window_after_resume(self):
        """Explicitly focus the game after the DE has left the foreground."""
        if (self._user_requested_de_foreground or
                not self._active_game_appid or
                not self._is_launched_game_running()):
            return False
        if self._send_labwc_previous_window_immediate():
            _log("Resume sent Labwc PreviousWindowImmediate to focus the game")
        else:
            _log("Resume could not dispatch Labwc PreviousWindowImmediate")
        return False

    def _minimize_focused_game_window(self):
        """Minimize the active game before raising the DE over it."""
        tokens = [token for token in re.findall(
            r"[a-z0-9]+", (self._active_game_name or "").lower())
                  if len(token) > 2]
        if not tokens:
            return False
        # Window titles often omit subtitles and punctuation from Steam's game
        # name. The longest title word is usually distinctive enough to match.
        token_js = json.dumps(max(tokens, key=len))
        script = (
            "(function(){let w=global.display.get_focus_window();"
            "if(!w)return 'no focused window';"
            "let t=w.get_title()||'';let n=t.toLowerCase();let q=" + token_js + ";"
            "if(!n.includes(q))return 'focused window is not the game: '+t;"
            "w.minimize();return 'minimized '+t;})()")
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        result = self._gnome_shell_eval(script) if "gnome" in desktop else None
        if result and "minimized" in result.lower():
            return True
        if "gnome" not in desktop:
            if self._send_labwc_keybind(ecodes.KEY_I if HAVE_EVDEV else 0):
                _log("Requested game minimize through labwc Super+I binding")
                return True
        return False

    def _schedule_background_game_suspend(self):
        if not self._active_game_appid:
            return
        self._background_suspend_attempts = 0
        if self._background_suspend_timer is not None:
            try:
                GLib.source_remove(self._background_suspend_timer)
            except Exception:
                pass
        # Let the compositor commit the Home transition before muting the
        # game stream, so the DE is already the visible top layer.
        self._background_suspend_timer = GLib.timeout_add(
            400, self._suspend_background_game)

    def _suspend_background_game(self):
        """Mute the game's Pulse/PipeWire streams without stopping its processes."""
        self._background_suspend_timer = None
        if (not self._user_requested_de_foreground or
                not self._active_game_appid):
            return False
        game_pids = self._find_launched_game_processes()
        pactl = shutil.which("pactl")
        if not pactl:
            _log("Cannot mute background game audio: pactl is unavailable")
            return False
        try:
            result = subprocess.run([pactl, "list", "sink-inputs"],
                                    capture_output=True, text=True,
                                    timeout=3, check=True)
        except Exception as exc:
            _log(f"Could not inspect background audio streams: {exc}")
            return False

        appid = str(self._active_game_appid or "")
        title_tokens = [token for token in re.findall(
            r"[a-z0-9]+", (self._active_game_name or "").lower())
                        if len(token) > 3]
        blocks = re.split(r"(?=Sink Input #\d+)", result.stdout)
        matched = []
        for block in blocks:
            id_match = re.search(r"Sink Input #(\d+)", block)
            if not id_match:
                continue
            pid_match = any(re.search(
                rf"application\.process\.id\s*=\s*\"?{pid}\"?(?:\s|$)",
                block, re.IGNORECASE) for pid in game_pids)
            title_match = bool(title_tokens and any(
                token in block.lower() for token in title_tokens))
            appid_match = bool(appid and appid in block)
            if pid_match or title_match or appid_match:
                mute_match = re.search(r"^\s*Mute:\s*(yes|no)\s*$", block,
                                       re.IGNORECASE | re.MULTILINE)
                was_muted = (mute_match.group(1).lower() == "yes"
                             if mute_match else False)
                matched.append((id_match.group(1), was_muted))

        if not matched:
            self._background_suspend_attempts += 1
            if self._background_suspend_attempts < 10:
                self._background_suspend_timer = GLib.timeout_add(
                    500, self._suspend_background_game)
            else:
                _log("No game audio stream matched for muting")
            return False

        muted = 0
        for sink_id, was_muted in matched:
            try:
                subprocess.run([pactl, "set-sink-input-mute", sink_id, "1"],
                               capture_output=True, timeout=3, check=True)
                self._background_muted_sink_inputs[sink_id] = was_muted
                muted += 1
            except Exception as exc:
                _log(f"Could not mute game audio stream {sink_id}: {exc}")
        self._background_game_suspended = False
        self._background_game_pids.clear()
        _log(f"Muted {muted} background game audio stream(s); game kept running")
        return False

    def _resume_background_audio(self):
        """Restore audio for the active game, including streams recreated on resume."""
        pactl = shutil.which("pactl")
        if not pactl:
            return
        try:
            result = subprocess.run([pactl, "list", "sink-inputs"],
                                    capture_output=True, text=True,
                                    timeout=3, check=True)
        except Exception as exc:
            _log(f"Could not inspect game audio streams for restore: {exc}")
            return

        game_pids = self._find_launched_game_processes()
        appid = str(self._active_game_appid or "")
        title_tokens = [token for token in re.findall(
            r"[a-z0-9]+", (self._active_game_name or "").lower())
                        if len(token) > 3]
        blocks = re.split(r"(?=Sink Input #\d+)", result.stdout)
        current_ids = set()
        game_ids = []
        for block in blocks:
            id_match = re.search(r"Sink Input #(\d+)", block)
            if not id_match:
                continue
            sink_id = id_match.group(1)
            current_ids.add(sink_id)
            pid_match = any(re.search(
                rf"application\.process\.id\s*=\s*\"?{pid}\"?(?:\s|$)",
                block, re.IGNORECASE) for pid in game_pids)
            title_match = bool(title_tokens and any(
                token in block.lower() for token in title_tokens))
            appid_match = bool(appid and appid in block)
            if pid_match or title_match or appid_match:
                game_ids.append(sink_id)

        # A resumed game can recreate its PipeWire stream with a new ID. Such
        # streams were muted by the DE and have no saved ID, so explicitly
        # unmute them; for IDs we muted ourselves, restore their prior state.
        restored = 0
        for sink_id in game_ids:
            was_muted = self._background_muted_sink_inputs.get(sink_id, False)
            try:
                block = next((item for item in blocks
                              if re.search(rf"Sink Input #{sink_id}(?:\D|$)", item)), "")
                volume_values = [int(value) for value in re.findall(
                    r"\b(\d+)%\s*/\s*-?[\d.]+\s*dB", block,
                    re.IGNORECASE)]
                if volume_values and not any(volume_values):
                    subprocess.run([pactl, "set-sink-input-volume", sink_id,
                                    "100%"], capture_output=True,
                                   timeout=3, check=True)
                    _log(f"Game audio stream {sink_id} was at 0%; restored to 100%")
                subprocess.run([pactl, "set-sink-input-mute", sink_id,
                                "1" if was_muted else "0"],
                               capture_output=True, timeout=3, check=True)
                self._background_muted_sink_inputs.pop(sink_id, None)
                restored += 1
                _log(f"Game audio stream {sink_id} restored (muted={was_muted})")
            except Exception as exc:
                _log(f"Could not restore game audio stream {sink_id}: {exc}")

        # Drop IDs whose streams have disappeared. A short retry window catches
        # the delay between Steam raising the game and its audio stream opening.
        for sink_id in tuple(self._background_muted_sink_inputs):
            if sink_id not in current_ids:
                self._background_muted_sink_inputs.pop(sink_id, None)
        if self._active_game_appid and not game_ids:
            self._background_audio_resume_attempts += 1
            # Some Proton titles do not create their PipeWire stream until
            # the first menu finishes loading; keep checking for 40 seconds.
            if self._background_audio_resume_attempts <= 80:
                if self._background_audio_resume_timer is not None:
                    try:
                        GLib.source_remove(self._background_audio_resume_timer)
                    except Exception:
                        pass
                self._background_audio_resume_timer = GLib.timeout_add(
                    500, self._retry_background_audio_resume)
            else:
                _log("No active game audio stream found after resume retries")
        else:
            self._background_audio_resume_attempts = 0
            if self._background_audio_resume_timer is not None:
                try:
                    GLib.source_remove(self._background_audio_resume_timer)
                except Exception:
                    pass
                self._background_audio_resume_timer = None
            if restored:
                _log(f"Restored {restored} active game audio stream(s)")

    def _retry_background_audio_resume(self):
        self._background_audio_resume_timer = None
        self._resume_background_audio()
        return False

    def _resume_background_game(self):
        """Resume the game's processes after it was suspended behind the DE."""
        if not self._background_game_suspended:
            _log("Background game was not suspended; skipping SIGCONT")
            return False
        pids = set(self._background_game_pids)
        pids.update(self._find_launched_game_processes())
        resumed = 0
        for pid in pids:
            try:
                os.kill(pid, signal.SIGCONT)
                resumed += 1
            except ProcessLookupError:
                pass
            except OSError as exc:
                _log(f"Could not resume game process {pid}: {exc}")
        self._background_game_pids.clear()
        self._background_game_suspended = False
        if resumed:
            _log(f"Background game resumed ({resumed} process(es))")
        return resumed > 0

    def _schedule_home_foreground(self):
        if self._home_foreground_timer is None:
            self._home_foreground_timer = GLib.idle_add(
                self._force_home_foreground)

    def _cancel_home_foreground_enforcement(self):
        if self._home_foreground_timer is not None:
            try:
                GLib.source_remove(self._home_foreground_timer)
            except Exception:
                pass
            self._home_foreground_timer = None

    def _toggle_audio_mute(self, target):
        wpctl = shutil.which("wpctl")
        if wpctl:
            try:
                subprocess.Popen([wpctl, "set-mute", target, "toggle"],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
                return
            except OSError:
                pass
        self._show_toast("Audio control is unavailable")

    def _show_control_center_power_menu(self):
        self.control_center_power_open = True
        self.control_center_power_choice_idx = 0
        self.control_center_power_card.set_no_show_all(False)
        self.control_center_power_card.show_all()
        self.control_center_power_card.set_no_show_all(True)
        self._update_control_center_power_focus()

    def _move_control_center_power_choice(self, delta):
        self.control_center_power_choice_idx = (
            self.control_center_power_choice_idx + delta) % len(self.control_center_power_buttons)
        self._update_control_center_power_focus()
        self._play_sfx("PS5_Change")

    def _update_control_center_power_focus(self):
        for index, button in enumerate(self.control_center_power_buttons):
            if index == self.control_center_power_choice_idx:
                button.override_background_color(
                    Gtk.StateFlags.NORMAL, Gdk.RGBA(0.27, 0.39, 0.62, 1))
                button.override_color(
                    Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            else:
                button.override_background_color(
                    Gtk.StateFlags.NORMAL, Gdk.RGBA(0.10, 0.12, 0.16, 0.98))
                button.override_color(
                    Gtk.StateFlags.NORMAL, Gdk.RGBA(0.78, 0.81, 0.87, 1))

    def _on_control_center_power_choice(self, button, response):
        self.control_center_power_card.hide()
        self.control_center_power_card.set_no_show_all(True)
        self.control_center_power_open = False
        if response == 1:
            self._toggle_control_center()
            self._on_logout()
        elif response == 2:
            self._toggle_control_center()
            self._on_shutdown()

    def _load_status_notice_background(self, filename):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        try:
            return GdkPixbuf.Pixbuf.new_from_file(path)
        except Exception as exc:
            _log(f"Status notice background load failed ({filename}): {exc}")
            return None

    def _paint_status_notice_background(self, cr, width, height, image):
        if image is None:
            return
        scaled = image
        if image.get_width() != width or image.get_height() != height:
            scaled = image.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
        Gdk.cairo_set_source_pixbuf(cr, scaled, 0, 0)
        cr.paint()

    @staticmethod
    def _paint_status_notice_icon(cr, image, x, y, width, height):
        if image is None or width <= 0 or height <= 0:
            return
        cr.save()
        cr.translate(x, y)
        cr.scale(width / image.get_width(), height / image.get_height())
        Gdk.cairo_set_source_pixbuf(cr, image, 0, 0)
        cr.paint()
        cr.restore()

    def _draw_controller_notice(self, widget, cr):
        alloc = widget.get_allocation()
        w, h = float(alloc.width), float(alloc.height)
        if w <= 0 or h <= 0:
            return False
        self._paint_status_notice_background(
            cr, int(w), int(h), self._controller_notice_background)
        has_battery = self._controller_battery_percent is not None
        gw, gh = w * 70.0 / 256.0, h * 46.0 / 144.0
        gx = w * 59.0 / 256.0 if has_battery else (w - gw) / 2.0
        gy = h * 47.0 / 144.0 if has_battery else (h - gh) / 2.0
        self._paint_status_notice_icon(
            cr, self._controller_notice_icon, gx, gy, gw, gh)

        if has_battery:
            bx, by = w * 139.0 / 256.0, h * 65.0 / 144.0
            bw, bh = w * 53.0 / 256.0, h * 34.0 / 144.0
            self._paint_status_notice_icon(
                cr, self._battery_notice_icon, bx, by, bw, bh)
            percent = max(0, min(100, self._controller_battery_percent)) / 100.0
            bars = min(3, int(math.ceil(percent * 3)))
            cr.set_source_rgba(1, 1, 1, 1)
            bar_w, bar_h = w * 7.0 / 256.0, h * 16.0 / 144.0
            for index, source_x in enumerate((153, 164, 175)):
                if index >= bars:
                    break
                cr.rectangle(w * source_x / 256.0, h * 74.0 / 144.0,
                             bar_w, bar_h)
            cr.fill()
        return False

    def _draw_microphone_notice(self, widget, cr):
        alloc = widget.get_allocation()
        w, h = float(alloc.width), float(alloc.height)
        if w <= 0 or h <= 0:
            return False
        self._paint_status_notice_background(
            cr, int(w), int(h), self._microphone_notice_background)
        cx, cy = w * 154.0 / 256.0, h * 72.0 / 144.0
        mic_w, mic_h = w * 44.0 / 256.0, h * 64.0 / 144.0
        self._paint_status_notice_icon(
            cr, self._microphone_notice_icon,
            w * 132.0 / 256.0, h * 40.0 / 144.0, mic_w, mic_h)
        if not self._microphone_enabled:
            # Small red no-entry mark at the lower-right of the microphone.
            nr = min(w, h) * 0.075
            nx, ny = cx + mic_w * 0.78, cy + mic_h * 0.60
            cr.save()
            cr.set_source_rgba(0.95, 0.08, 0.12, 0.98)
            cr.set_line_width(max(1.5, h * 0.028))
            cr.arc(nx, ny, nr, 0, 2 * math.pi)
            cr.stroke()
            cr.move_to(nx - nr * 0.68, ny - nr * 0.68)
            cr.line_to(nx + nr * 0.68, ny + nr * 0.68)
            cr.stroke()
            cr.restore()
        return False

    def _read_controller_battery(self, device_names):
        keywords = ("controller", "gamepad", "xbox", "dualshock", "dualsense")
        for supply in glob.glob("/sys/class/power_supply/*"):
            name = os.path.basename(supply).lower()
            try:
                with open(os.path.join(supply, "type"), encoding="utf-8") as stream:
                    power_type = stream.read().strip().lower()
                if power_type != "battery" or not any(word in name for word in keywords):
                    continue
                with open(os.path.join(supply, "capacity"), encoding="utf-8") as stream:
                    return max(0, min(100, int(stream.read().strip())))
            except (OSError, ValueError):
                continue

        # UPower exposes controller batteries on some Bluetooth/HID drivers.
        upower = shutil.which("upower")
        if upower:
            try:
                devices = subprocess.run([upower, "-e"], capture_output=True,
                                         text=True, timeout=1.5, check=True).stdout.splitlines()
                for path in devices:
                    if not any(word in path.lower() for word in keywords):
                        continue
                    info = subprocess.run([upower, "-i", path], capture_output=True,
                                          text=True, timeout=1.5, check=True).stdout
                    match = re.search(r"^\s*percentage:\s*(\d+)%", info, re.MULTILINE | re.IGNORECASE)
                    if match:
                        return max(0, min(100, int(match.group(1))))
            except (OSError, subprocess.SubprocessError):
                pass
        return None

    def _read_microphone_state(self):
        # PipeWire's wpctl reports mute state in one fast IPC call. Fall back
        # to PulseAudio only when wpctl is absent or cannot query the source.
        wpctl = shutil.which("wpctl")
        if wpctl:
            try:
                result = subprocess.run(
                    [wpctl, "get-volume", "@DEFAULT_AUDIO_SOURCE@"],
                    capture_output=True, text=True, timeout=1.2, check=True)
                return "[muted]" not in result.stdout.lower()
            except (OSError, subprocess.SubprocessError):
                pass
        pactl = shutil.which("pactl")
        if pactl:
            try:
                source = subprocess.run(
                    [pactl, "get-default-source"], capture_output=True,
                    text=True, timeout=1.2, check=True).stdout.strip()
                if source and not source.endswith(".monitor"):
                    muted = subprocess.run(
                        [pactl, "get-source-mute", source], capture_output=True,
                        text=True, timeout=1.2, check=True).stdout.lower()
                    return "mute: no" in muted
            except (OSError, subprocess.SubprocessError):
                pass
        return False

    def _refresh_status_notice_data(self):
        device_names = tuple(dev.name for dev in self._pad_devices)
        def worker():
            battery = self._read_controller_battery(device_names)
            microphone = self._read_microphone_state()
            GLib.idle_add(self._apply_status_notice_data, battery, microphone)
        threading.Thread(target=worker, name="status-notices", daemon=True).start()

    def _apply_status_notice_data(self, battery, microphone):
        self._controller_battery_percent = battery
        self._microphone_enabled = microphone
        self._microphone_state_initialized = True
        self.controller_notice.queue_draw()
        self.microphone_notice.queue_draw()
        return False

    def _show_status_notices(self, show_controller=None, show_microphone=True):
        if self._status_notice_dismiss_id is not None:
            GLib.source_remove(self._status_notice_dismiss_id)
            self._status_notice_dismiss_id = None
        self._refresh_status_notice_data()
        self.status_notice_box.show_all()
        if show_controller is None:
            show_controller = self._pad_dev is not None
        self.controller_notice.set_visible(
            bool(show_controller and self._pad_dev is not None))
        self.microphone_notice.set_visible(bool(show_microphone))
        self._status_notices_started = True
        self.status_notice_box.set_opacity(0.0)
        self._play_sfx("PS5_Peripherals")
        self._animate_status_notices(0.0, 1.0)

    def _start_microphone_state_monitor(self):
        if self._microphone_monitor_id is None:
            self._microphone_monitor_id = GLib.timeout_add(
                3000, self._poll_microphone_state)

    def _poll_microphone_state(self):
        if not self._status_notices_started or self._microphone_poll_active:
            return True
        self._microphone_poll_active = True

        def worker():
            enabled = self._read_microphone_state()
            GLib.idle_add(self._apply_polled_microphone_state, enabled)

        threading.Thread(target=worker, name="microphone-state", daemon=True).start()
        return True

    def _apply_polled_microphone_state(self, enabled):
        self._microphone_poll_active = False
        if not self._microphone_state_initialized:
            self._microphone_enabled = enabled
            self._microphone_state_initialized = True
            self.microphone_notice.queue_draw()
            return False
        if enabled != self._microphone_enabled:
            self._microphone_enabled = enabled
            self.microphone_notice.queue_draw()
            self._show_status_notices(show_controller=False, show_microphone=True)
        return False

    def _animate_status_notices(self, start_opacity, end_opacity):
        if self._status_notice_fade_id is not None:
            GLib.source_remove(self._status_notice_fade_id)
        self._status_notice_fade_from = start_opacity
        self._status_notice_fade_to = end_opacity
        self._status_notice_fade_start = time.monotonic()
        self._status_notice_fade_id = GLib.timeout_add(
            16, self._on_status_notice_fade)

    def _on_status_notice_fade(self):
        elapsed = time.monotonic() - self._status_notice_fade_start
        progress = min(1.0, elapsed / self._status_notice_fade_duration)
        eased = _cubic_bezier_y(progress)
        opacity = (self._status_notice_fade_from +
                   (self._status_notice_fade_to - self._status_notice_fade_from) * eased)
        self.status_notice_box.set_opacity(opacity)
        if progress >= 1.0:
            self._status_notice_fade_id = None
            if self._status_notice_fade_to > 0:
                self._status_notice_dismiss_id = GLib.timeout_add_seconds(
                    4, self._begin_status_notice_fadeout)
            else:
                self.status_notice_box.hide()
            return False
        return True

    def _begin_status_notice_fadeout(self):
        self._status_notice_dismiss_id = None
        self._animate_status_notices(self.status_notice_box.get_opacity(), 0.0)
        return False

    def _on_controller_connected(self):
        if self._status_notices_started:
            self._show_status_notices(show_controller=True, show_microphone=False)

    def _on_controller_disconnected(self):
        if self._pad_dev is None:
            return False
        self._guide_control_center_pending = False
        self._raw_hid_capture_stop.set()
        self._raw_hid_capture_active = False
        for watch_id in self._pad_watch_ids:
            try:
                GLib.source_remove(watch_id)
            except Exception:
                pass
        for watch_id in self._capture_watch_ids:
            try:
                GLib.source_remove(watch_id)
            except Exception:
                pass
        for dev in self._pad_devices:
            try:
                dev.ungrab()
            except Exception:
                pass
            try:
                dev.close()
            except Exception:
                pass
        for dev in self._capture_devices:
            try:
                dev.close()
            except Exception:
                pass
        self._pad_devices = []
        self._pad_watch_ids = []
        self._capture_devices = []
        self._capture_watch_ids = []
        self._capture_device_codes = {}
        self._pad_dev = None
        self._pad_src = None
        self._capture_button_codes.clear()
        self.controller_notice.hide()
        self._controller_battery_percent = None
        return False

    def _monitor_gamepad_connection(self):
        if self._pad_dev is not None and not os.path.exists(self._pad_dev.path):
            _log(f"Controller input node disappeared: {self._pad_dev.path}")
            self._on_controller_disconnected()
        if self._pad_dev is None:
            try:
                self._init_gamepad()
            except Exception as exc:
                # A device can vanish between opening its event node and
                # reading capabilities. Keep the hotplug monitor alive.
                _log(f"Gamepad scan survived an input hotplug race: {exc}")
        return True

    def _pack_top_icon(self, parent, icon, spacing, circular_focus=False):
        """Add an icon inside a focusable frame in the top-right menu."""
        frame = Gtk.EventBox()
        frame.set_name("top-icon-frame")
        frame.set_visible_window(True)
        frame.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 0))
        if circular_focus:
            diameter = self._top_icon_focus_diameter
            frame.set_size_request(diameter, diameter)
            frame.set_halign(Gtk.Align.CENTER)
            frame.set_valign(Gtk.Align.CENTER)
            frame.connect("draw", self._draw_orbit_widget)
        else:
            frame.connect_after("draw", self._draw_orbit_widget)
        frame.set_margin_start(spacing)
        frame.set_margin_end(spacing)
        frame.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        frame.add(icon)
        parent.pack_start(frame, False, False, 0)
        self.top_icon_frames.append(frame)

    def _on_settings_icon_press(self, widget, event):
        # Clicking the gear highlights it; Enter/A opens its menu.
        self.tab_focus_idx = len(self.TABS) + 1
        self.tab_bar_focused = True
        self._update_tab_focus_frame()
        return True

    def _tint_pixbuf_black(self, pixbuf):
        """Create a black version of an icon while preserving its alpha mask."""
        if pixbuf is None or not pixbuf.get_has_alpha():
            return pixbuf
        width, height = pixbuf.get_width(), pixbuf.get_height()
        channels = pixbuf.get_n_channels()
        rowstride = pixbuf.get_rowstride()
        source = pixbuf.get_pixels()
        tinted = bytearray(width * height * 4)
        for y in range(height):
            for x in range(width):
                src = y * rowstride + x * channels
                dst = (y * width + x) * 4
                tinted[dst:dst + 4] = bytes((0, 0, 0, source[src + channels - 1]))
        return GdkPixbuf.Pixbuf.new_from_bytes(
            GLib.Bytes.new(bytes(tinted)), GdkPixbuf.Colorspace.RGB, True, 8,
            width, height, width * 4)

    def _create_content(self, parent):
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(200)
        self.stack.add_named(self._create_games_page(), "games")
        self.stack.add_named(self._create_apps_page(), "apps")
        self.stack.add_named(self._create_settings_page(), "settings")
        parent.pack_start(self.stack, True, True, 0)

    def _create_games_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        page.set_hexpand(True)
        page.set_vexpand(True)

        # Cairo carousel: conveyor belt drawn with Cairo, full control
        self.carousel = Gtk.DrawingArea()
        self.carousel.set_hexpand(True)
        # Keep the belt in a fixed vertical lane. Letting this DrawingArea
        # expand makes its apparent position depend on the selected game's
        # lower information panel and can shift the whole row on edge items.
        self.carousel.set_vexpand(False)
        self.carousel.set_valign(Gtk.Align.START)
        self.carousel.set_margin_top(self._sh_pct(7))
        self.carousel.set_size_request(-1, self._sw_pct(15))
        self.carousel.connect("draw", self._on_carousel_draw)
        page.pack_start(self.carousel, False, False, 0)

        # ---- Selected game info overlay (bottom-left) ----
        self.info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=self._font_px(10))
        self.info_box.set_halign(Gtk.Align.START)
        self.info_box.set_valign(Gtk.Align.END)
        self.info_box.set_margin_start(self._sw_pct(10))
        self._info_box_base_margin_bottom = self._sh_pct(4)
        self._info_box_start_offset = self._sh_pct(1.5)
        self.info_box.set_margin_bottom(
            self._info_box_base_margin_bottom + self._info_box_start_offset)
        self.info_box.set_opacity(0.0)

        # Title art (stylized logo image above the title text)
        self.title_art = Gtk.Image()
        self.title_art.set_valign(Gtk.Align.START)
        self.title_art.set_hexpand(False)
        self.info_box.pack_start(self.title_art, False, False, self._font_px(6))

        self.game_subtitle = Gtk.Label(label="")
        self.game_subtitle.set_halign(Gtk.Align.CENTER)
        self.game_subtitle.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.75, 0.78, 0.84, 1.0))
        self.game_subtitle.set_name("game-subtitle")
        self.info_box.pack_start(self.game_subtitle, False, False, self._font_px(4))

        # Play button (PS5 style: pill-shaped, gray-tint semi-transparent)
        self.play_box = Gtk.EventBox()
        self.play_box.set_size_request(self._sw_pct(20), self._sh_pct(7))
        self.play_box.set_name("play-button")
        self.play_box.set_valign(Gtk.Align.CENTER)
        self.play_box.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0.24, 0.24, 0.26, 0.4))
        self.play_box.set_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                                 Gdk.EventMask.BUTTON_RELEASE_MASK |
                                 Gdk.EventMask.ENTER_NOTIFY_MASK |
                                 Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.play_box.connect("button-press-event", self._on_play_button_press)
        self.play_btn = Gtk.Label(label="Play")
        self.play_btn.set_halign(Gtk.Align.CENTER)
        self.play_btn.set_name("play-label")
        self.play_box.add(self.play_btn)

        # Play + more options container
        self.play_container = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                                      spacing=self._font_px(10))
        self.play_container.set_halign(Gtk.Align.CENTER)
        self.play_container.set_valign(Gtk.Align.CENTER)
        self.play_container.pack_start(self.play_box, False, False, 0)

        # More options button (three dots)
        self.more_box = Gtk.EventBox()
        self.more_box.set_size_request(self._sh_pct(7), self._sh_pct(7))
        self.more_box.set_name("more-button")
        self.more_box.set_valign(Gtk.Align.CENTER)
        self.more_box.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0.24, 0.24, 0.26, 0.4))
        self.more_box.set_events(Gdk.EventMask.BUTTON_PRESS_MASK |
                                 Gdk.EventMask.BUTTON_RELEASE_MASK |
                                 Gdk.EventMask.ENTER_NOTIFY_MASK |
                                 Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.more_box.connect("button-press-event", self._on_more_button_press)
        self.more_btn = Gtk.Label(label="...")
        self.more_btn.set_halign(Gtk.Align.CENTER)
        self.more_btn.set_valign(Gtk.Align.CENTER)
        self.more_btn.set_justify(Gtk.Justification.CENTER)
        self.more_btn.set_alignment(0.5, 0.5)
        self.more_btn.set_name("more-label")
        self.more_box.add(self.more_btn)
        self.play_container.pack_start(self.more_box, False, False, 0)

        self.info_box.pack_start(self.play_container, False, False, self._font_px(14))

        page.pack_end(self.info_box, False, False, 0)

        self._update_game_info()
        self._set_play_button_highlight(False)
        return page

    def _create_apps_page(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_hexpand(True)
        scrolled.set_vexpand(True)

        grid = Gtk.Grid()
        grid.set_column_spacing(self._sw_pct(2.0))
        grid.set_row_spacing(self._sh_pct(2.4))
        grid.set_column_homogeneous(True)
        grid.set_row_homogeneous(False)
        grid.set_margin_start(self._sw_pct(6))
        grid.set_margin_top(self._sh_pct(4))
        grid.set_margin_end(self._sw_pct(6))
        grid.set_margin_bottom(self._sh_pct(5))
        self.app_grid = grid

        usable_width = self._sw * 0.88
        target_tile_width = self._sw * 0.15
        cols = max(3, min(6, int(usable_width / target_tile_width)))
        self._app_cols = cols
        self.app_tiles = []
        self._app_animation_source = None
        self._app_page_build_source = None
        self._app_tile_build_index = 0
        self._apps_loading_label = None
        if self.apps:
            gaps = self._sw_pct(2.0) * (cols - 1)
            self._app_tile_width = max(
                self._sw_pct(12), int((usable_width - gaps) / cols))
            self._apps_loading_label = Gtk.Label(label="Loading applications…")
            self._apps_loading_label.set_margin_top(self._sh_pct(10))
            self._apps_loading_label.set_halign(Gtk.Align.CENTER)
            self._apps_loading_label.override_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            grid.attach(self._apps_loading_label, 0, 0, cols, 1)
        else:
            lbl = Gtk.Label(label="No applications found")
            lbl.set_margin_top(self._sh_pct(10))
            lbl.set_halign(Gtk.Align.CENTER)
            lbl.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            grid.attach(lbl, 0, 0, cols, 1)

        scrolled.add(grid)
        page.pack_start(scrolled, True, True, 0)
        return page

    def _start_app_tile_build(self):
        if (self._app_page_build_source is None and
                self._app_tile_build_index < len(self.apps)):
            self._app_page_build_source = GLib.timeout_add(
                8,
                self._build_next_app_tile)

    def _build_next_app_tile(self):
        """Create one app tile per idle turn so input and rendering stay responsive."""
        if self.tab != 1:
            self._app_page_build_source = None
            return False
        index = self._app_tile_build_index
        if index >= len(self.apps):
            self._app_page_build_source = None
            return False
        app = self.apps[index]
        if self._apps_loading_label is not None:
            self.app_grid.remove(self._apps_loading_label)
            self._apps_loading_label = None
        tile = self._create_app_tile(app, index == self.asel)
        tile.set_size_request(self._app_tile_width, self._app_tile_width)
        tile.set_hexpand(True)
        tile.set_halign(Gtk.Align.FILL)
        tile.connect("button-press-event", self._on_app_tile_press, index)
        self.app_grid.attach(tile, index % self._app_cols,
                             index // self._app_cols, 1, 1)
        tile.show_all()
        self.app_tiles.append(tile)
        self._app_tile_build_index = index + 1
        if self._app_tile_build_index >= len(self.apps):
            self._app_page_build_source = None
            return False
        # Pace construction across frames instead of letting idle callbacks
        # drain the entire app list in one uninterrupted main-loop pass.
        self._app_page_build_source = GLib.timeout_add(
            8, self._build_next_app_tile)
        return False

    def _create_app_tile(self, app, selected):
        tile = Gtk.EventBox()
        tile.set_name("app-tile")
        tile.connect_after("draw", self._draw_orbit_widget)
        tile.set_visible_window(True)
        tile.set_events(Gdk.EventMask.BUTTON_PRESS_MASK)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        spacing=self._font_px(10))
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)
        inner.set_margin_start(self._font_px(10))
        inner.set_margin_end(self._font_px(10))
        inner.set_margin_top(self._font_px(10))
        inner.set_margin_bottom(self._font_px(10))

        # Load themed and file-based icons at display resolution to avoid the
        # soft, upscaled 48px icons that made the previous tiles look blurry.
        icon_name = app.get('icon')
        img = None
        if icon_name:
            try:
                icon_px = min(self._font_px(92), self._sw_pct(5.2))
                if os.path.isfile(icon_name):
                    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        icon_name, icon_px, icon_px, True)
                else:
                    theme = Gtk.IconTheme.get_default()
                    pb = theme.load_icon(
                        icon_name, icon_px, Gtk.IconLookupFlags.FORCE_SIZE)
                img = Gtk.Image.new_from_pixbuf(pb)
            except Exception:
                img = None
        if img is None:
            img = Gtk.Image.new_from_icon_name("application-x-executable",
                                               Gtk.IconSize.DIALOG)
            img.set_pixel_size(self._font_px(76))
        img.set_halign(Gtk.Align.CENTER)
        inner.pack_start(img, False, False, 0)

        lbl = Gtk.Label(label=app['name'])
        lbl.set_halign(Gtk.Align.CENTER)
        lbl.set_justify(Gtk.Justification.CENTER)
        lbl.set_ellipsize(Pango.EllipsizeMode.END)
        lbl.set_max_width_chars(22)
        lbl.set_line_wrap(True)
        lbl.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        inner.pack_start(lbl, False, False, 0)
        tile.add(inner)

        if selected and not self.tab_bar_focused:
            tile.get_style_context().add_class("selected")
        return tile

    def _on_app_tile_press(self, _widget, event, index):
        if event.button != 1:
            return False
        self.asel = index
        self._refresh_app_selection()
        self._play_sfx("PS5_Select")
        self._launch_app()
        return True

    def _animate_apps_in(self):
        """Stagger a short fade and upward settle as the Apps page appears."""
        if self._app_animation_source is not None:
            try:
                self.remove_tick_callback(self._app_animation_source)
            except Exception:
                pass
            self._app_animation_source = None
        tiles = getattr(self, "app_tiles", [])
        if not tiles:
            return
        started = time.monotonic()
        stagger = 0.018
        duration = 0.24
        for tile in tiles:
            tile.set_opacity(0.0)

        def frame(_widget, _frame_clock, _user_data=None):
            elapsed = time.monotonic() - started
            complete = True
            for index, tile in enumerate(tiles):
                progress = max(0.0, min(1.0, (elapsed - index * stagger) / duration))
                eased = 1.0 - (1.0 - progress) ** 3
                tile.set_opacity(eased)
                if progress < 1.0:
                    complete = False
            if complete:
                self._app_animation_source = None
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        self._app_animation_source = self.add_tick_callback(frame)

    def _refresh_app_selection(self):
        """Move the white selection frame to the selected application tile."""
        for i, tile in enumerate(getattr(self, "app_tiles", [])):
            ctx = tile.get_style_context()
            if i == self.asel and not self.tab_bar_focused:
                ctx.add_class("selected")
            else:
                ctx.remove_class("selected")
            tile.queue_draw()

    def _create_game_tile(self, game, selected, position=0):
        # PS5: squircle tile — art clipped to rounded corners via Cairo
        tile_w = self._sw_pct(11 if selected else 8)
        tile_h = tile_w  # square
        tile = GameTile(game, tile_w, tile_h, selected)
        # Left margin: big if first item selected (position 0, gsel==0),
        # small otherwise so tiles sit snug against each other
        if position == 0:
            tile.set_margin_start(self._sw_pct(8) if self.gsel == 0 else self._sw_pct(2))
        else:
            tile.set_margin_start(0)
        return tile

    def _make_bottom_panel(self, title, icons):
        """PS5 bottom info panel: title left, icon+value chips right."""
        panel = Gtk.EventBox()
        panel.set_size_request(-1, self._sh_pct(6))
        panel.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0.11, 0.13, 0.16, 0.9))
        panel.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0.14, 0.17, 0.21, 1.0))

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.set_margin_start(self._sw_pct(2))
        box.set_margin_end(self._sw_pct(2))

        t = Gtk.Label(label=title)
        t.set_halign(Gtk.Align.START)
        t.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.62, 0.64, 0.69, 1.0))
        t.set_name("panel-title")
        box.pack_start(t, True, True, 0)

        chips = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                        spacing=self._font_px(8))
        chips.set_halign(Gtk.Align.END)
        for val, _icon in icons:
            chip = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=self._font_px(4))
            chip.set_halign(Gtk.Align.END)
            v = Gtk.Label(label=val)
            v.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            v.set_name("panel-value")
            chip.pack_start(v, False, False, 0)
            chips.pack_start(chip, False, False, 0)
        box.pack_start(chips, False, False, 0)

        panel.add(box)
        return panel

    def _build_settings_data(self):
        return [
            {"name": "Display", "options": [
                {"label": "Display Scale", "get": self._get_scale_label,
                 "cb": self._on_scale_changed,
                 "cb_backward": self._on_scale_changed_backward},
                {"label": "GPU", "get": self._get_gpu_label,
                 "cb": self._on_gpu_changed,
                 "cb_backward": self._on_gpu_changed_backward},
                {"label": "Menu Font Size", "get": self._get_menu_font_size_label,
                 "cb": self._on_menu_font_size_changed,
                 "cb_backward": self._on_menu_font_size_changed_backward},
                {"label": "Menu Font Weight", "get": self._get_menu_font_weight_label,
                 "cb": self._on_menu_font_weight_changed,
                 "cb_backward": self._on_menu_font_weight_changed_backward},
            ]},
            {"name": "System", "options": [
                {"label": "Log Out", "get": lambda: "",
                 "cb": self._on_logout, "action": True},
                {"label": "Shut Down", "get": lambda: "",
                 "cb": self._on_shutdown, "action": True},
            ]},
            {"name": "About", "options": [
                {"label": "Console DE", "get": lambda: "PS5 Shell 1.0",
                 "cb": None},
                {"label": "Installed Build", "get": self._get_installed_build_label,
                 "cb": None},
                {"label": "Check for Updates", "get": lambda: self._update_check_status,
                 "cb": self._check_for_updates, "action": True},
                {"label": "Resolution",
                 "get": lambda: f"{self._sw}×{self._sh}", "cb": None},
            ]},
            {"name": "Performance", "options": [
                {"label": "System CPU Usage", "get": self._get_system_cpu_label,
                 "cb": None},
                {"label": "System Memory", "get": self._get_system_memory_label,
                 "cb": None},
                {"label": "Process Memory Details", "get": lambda: "Open",
                 "cb": self._show_process_memory_details, "action": True},
                {"label": "DE Process CPU", "get": self._get_shell_cpu_label,
                 "cb": None},
                {"label": "DE Process Memory", "get": self._get_shell_memory_label,
                 "cb": None},
                {"label": "Process ID", "get": lambda: str(os.getpid()),
                 "cb": None},
            ]},
        ]

    def _read_installed_commit(self):
        path = os.path.expanduser("~/.config/console-de/install_commit")
        try:
            with open(path, encoding="ascii") as version_file:
                commit = version_file.read().strip().lower()
            if re.fullmatch(r"[0-9a-f]{40}", commit):
                return commit
        except OSError:
            pass
        return None

    def _get_installed_build_label(self):
        return self._installed_commit[:7] if self._installed_commit else "Unknown"

    def _check_for_updates(self):
        if self._update_check_running:
            return
        if not self._installed_commit:
            self._update_check_status = "Reinstall to record build"
            self._refresh_update_status_row()
            return

        self._update_check_running = True
        self._update_check_status = "Checking…"
        self._refresh_update_status_row()
        installed_commit = self._installed_commit

        def check_worker():
            try:
                from urllib.request import Request, urlopen
                url = ("https://api.github.com/repos/beastzy/PS5-Shell/compare/"
                       f"{installed_commit}...main")
                request = Request(url, headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "PS5-Shell-update-check",
                    "X-GitHub-Api-Version": "2022-11-28",
                })
                with urlopen(request, timeout=8) as response:
                    result = json.loads(response.read().decode("utf-8"))
                relation = result.get("status")
                newer = int(result.get("ahead_by", 0))
                latest = result.get("head", {}).get("sha", "")[:7]
                if relation == "identical":
                    message = f"Up to date · {latest}"
                elif relation == "ahead" and newer:
                    message = f"{newer} newer commit{'s' if newer != 1 else ''} · {latest}"
                elif relation == "behind":
                    message = f"Installed build is newer · {latest}"
                elif relation == "diverged":
                    message = f"Build differs from main · {latest}"
                elif newer:
                    message = f"{newer} newer commit{'s' if newer != 1 else ''} · {latest}"
                else:
                    message = f"Up to date · {latest}"
            except Exception as exc:
                _log(f"Update check failed: {exc}")
                message = "Check failed · try again"
            GLib.idle_add(self._finish_update_check, message)

        threading.Thread(target=check_worker, daemon=True,
                         name="console-de-update-check").start()

    def _finish_update_check(self, message):
        self._update_check_running = False
        self._update_check_status = message
        self._refresh_update_status_row()
        return False

    def _refresh_update_status_row(self):
        for row in getattr(self, "settings_rows", []):
            label = row["label"].get_text()
            if label == "Check for Updates":
                row["value_label"].set_text(self._update_check_status)
            elif label == "Installed Build":
                row["value_label"].set_text(self._get_installed_build_label())

    def _create_settings_page(self):
        self.settings_data = self._build_settings_data()
        self.settings_cat = 0
        self.settings_opt = 0
        self.settings_pane = 0  # 0 = categories, 1 = options

        page = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                       spacing=self._sw_pct(3))
        page.set_margin_start(self._sw_pct(5))
        page.set_margin_top(self._sh_pct(3))
        page.set_margin_end(self._sw_pct(5))

        # Left: category list
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        left.set_size_request(self._sw_pct(22), -1)

        self.cat_rows = []
        for cat in self.settings_data:
            row = Gtk.EventBox()
            row.set_name("settings-item")
            row.connect_after("draw", self._draw_orbit_widget)
            row.set_size_request(-1, self._sh_pct(6))
            lbl = Gtk.Label(label=cat["name"])
            lbl.set_halign(Gtk.Align.START)
            lbl.set_margin_start(self._font_px(16))
            lbl.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            row.add(lbl)
            row.connect("button-press-event", self._on_cat_clicked)
            left.pack_start(row, False, False, self._font_px(2))
            self.cat_rows.append(row)
        page.pack_start(left, False, False, 0)

        sep = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
        page.pack_start(sep, False, False, 0)

        # Right: options for the selected category
        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        spacing=self._font_px(4))
        right.set_hexpand(True)
        self.options_box = right
        page.pack_start(right, True, True, 0)

        self._rebuild_options()
        return page

    def _rebuild_options(self):
        for child in self.options_box.get_children():
            self.options_box.remove(child)
        self.settings_rows = []
        cat = self.settings_data[self.settings_cat]
        for opt in cat["options"]:
            self._add_setting_row(self.options_box, opt["label"],
                                  opt["get"](), opt["cb"],
                                  opt.get("action", False))
        if self.settings_opt >= len(self.settings_rows):
            self.settings_opt = 0
        self._refresh_settings_highlight()
        self.options_box.show_all()
        self._sync_settings_stats_timer()

    def _get_shell_cpu_label(self):
        return f"{self._settings_stats_cpu_percent:.1f}%"

    def _get_shell_memory_label(self):
        if self._settings_stats_rss_mb is None:
            return "-- MB"
        return f"{self._settings_stats_rss_mb:.1f} MB"

    def _get_system_cpu_label(self):
        if self._settings_system_cpu_percent is None:
            return "Measuring..."
        return f"{self._settings_system_cpu_percent:.1f}%"

    def _get_system_memory_label(self):
        if self._settings_system_memory is None:
            return "-- / -- GB"
        used_gb, total_gb, percent = self._settings_system_memory
        return f"{used_gb:.1f}/{total_gb:.1f} GB ({percent:.0f}%)"

    def _read_top_process_memory(self, limit=18):
        """Return the largest resident processes owned by this login user."""
        page_size = os.sysconf("SC_PAGE_SIZE")
        processes = []
        try:
            proc_entries = os.scandir("/proc")
        except OSError:
            return processes
        with proc_entries:
            for entry in proc_entries:
                if not entry.name.isdigit():
                    continue
                pid = int(entry.name)
                proc_path = entry.path
                try:
                    if os.stat(proc_path).st_uid != os.getuid():
                        continue
                    with open(os.path.join(proc_path, "statm"), "r") as statm:
                        fields = statm.read().split()
                    resident_mb = int(fields[1]) * page_size / (1024 * 1024)
                    if resident_mb < 1.0:
                        continue
                    with open(os.path.join(proc_path, "comm"), "r") as comm_file:
                        name = comm_file.read().strip() or "unknown process"
                    processes.append((resident_mb, pid, name))
                except (OSError, ValueError, IndexError):
                    # Processes can exit while /proc is being sampled.
                    continue
        return sorted(processes, reverse=True)[:limit]

    def _start_process_logging(self):
        """Create a visible per-session CSV log of all /proc processes."""
        log_dir = os.path.join(
            HOME, "Documents", "Console DE", "Process Logs")
        try:
            os.makedirs(log_dir, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            self._process_log_path = os.path.join(
                log_dir, f"console-de-processes-{stamp}.csv")
            with open(self._process_log_path, "w", newline="",
                      encoding="utf-8") as log_file:
                writer = csv.writer(log_file)
                writer.writerow([
                    "timestamp", "pid", "parent_pid", "uid", "state",
                    "resident_memory_mb", "process_name"])
            self._process_log_scan_active = False
            self._append_process_log_snapshot()
            self._process_log_timer_id = GLib.timeout_add_seconds(
                30, self._append_process_log_snapshot)
            _log(f"Process snapshots are being logged to {self._process_log_path}")
        except OSError as exc:
            self._process_log_path = None
            _log(f"Could not start process logging: {exc}")

    def _append_process_log_snapshot(self):
        """Collect a complete process snapshot off the GTK thread."""
        if not self._process_log_path:
            return False
        if self._process_log_scan_active:
            return True
        self._process_log_scan_active = True
        log_path = self._process_log_path

        def collect_and_write():
            rows = []
            page_size = os.sysconf("SC_PAGE_SIZE")
            try:
                proc_entries = os.scandir("/proc")
                with proc_entries:
                    for entry in proc_entries:
                        if not entry.name.isdigit():
                            continue
                        try:
                            proc_path = entry.path
                            with open(os.path.join(proc_path, "stat"), "r") as stat_file:
                                stat_text = stat_file.read()
                            close_paren = stat_text.rfind(")")
                            stat_fields = stat_text[close_paren + 2:].split()
                            state = stat_fields[0]
                            parent_pid = int(stat_fields[1])
                            with open(os.path.join(proc_path, "status"), "r") as status_file:
                                uid_line = next(
                                    line for line in status_file
                                    if line.startswith("Uid:"))
                            uid = int(uid_line.split()[1])
                            with open(os.path.join(proc_path, "statm"), "r") as statm_file:
                                resident_pages = int(statm_file.read().split()[1])
                            with open(os.path.join(proc_path, "comm"), "r") as comm_file:
                                name = comm_file.read().strip()
                            rss_mb = resident_pages * page_size / (1024 * 1024)
                            rows.append((entry.name, parent_pid, uid, state,
                                         f"{rss_mb:.2f}", name))
                        except (OSError, ValueError, IndexError, StopIteration):
                            continue
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                with open(log_path, "a", newline="", encoding="utf-8") as log_file:
                    writer = csv.writer(log_file)
                    writer.writerows((timestamp, *row) for row in rows)
            except OSError as exc:
                _log(f"Could not collect process log snapshot: {exc}")
            finally:
                self._process_log_scan_active = False

        threading.Thread(target=collect_and_write, name="process-snapshot",
                         daemon=True).start()
        return True

    def _show_process_memory_details(self):
        """Show an on-screen, refreshable view of this login's memory use."""
        dialog = Gtk.Dialog(
            title="Memory Use",
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)
        dialog.set_default_size(self._sw_pct(58), self._sh_pct(72))
        dialog.set_position(Gtk.WindowPosition.CENTER_ON_PARENT)

        content = dialog.get_content_area()
        content.set_spacing(self._font_px(8))
        content.set_margin_start(self._sw_pct(2))
        content.set_margin_end(self._sw_pct(2))
        content.set_margin_top(self._sh_pct(2))
        content.set_margin_bottom(self._sh_pct(1))

        summary = Gtk.Label()
        summary.set_halign(Gtk.Align.START)
        summary.set_line_wrap(True)
        summary.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        content.pack_start(summary, False, False, 0)

        heading = Gtk.Label(label="Largest processes for this login")
        heading.set_halign(Gtk.Align.START)
        heading.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.75, 0.82, 1, 1))
        content.pack_start(heading, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)
        process_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                              spacing=self._font_px(3))
        scroll.add(process_box)
        content.pack_start(scroll, True, True, 0)

        note = Gtk.Label(label=(
            "This on-screen list shows apps running under your login. The saved "
            "session log also records all readable processes, including system services."))
        note.set_halign(Gtk.Align.START)
        note.set_line_wrap(True)
        note.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.68, 0.7, 0.75, 1))
        content.pack_start(note, False, False, 0)

        if self._process_log_path:
            log_location = Gtk.Label(label=(
                f"Full session process log (all readable processes, every 30 seconds):\n"
                f"{self._process_log_path}"))
            log_location.set_halign(Gtk.Align.START)
            log_location.set_line_wrap(True)
            log_location.override_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(0.68, 0.7, 0.75, 1))
            content.pack_start(log_location, False, False, 0)

        def refresh_process_view():
            self._read_shell_resource_usage()
            if self._settings_system_memory:
                used, total, percent = self._settings_system_memory
                available = max(0.0, total - used)
                summary.set_text(
                    f"System memory in use: {used:.1f} GB of {total:.1f} GB "
                    f"({percent:.0f}%)  •  Available: {available:.1f} GB")
            else:
                summary.set_text("System memory: unavailable")
            for child in process_box.get_children():
                process_box.remove(child)
            processes = self._read_top_process_memory()
            if not processes:
                empty = Gtk.Label(label="No readable user processes found.")
                empty.set_halign(Gtk.Align.START)
                process_box.pack_start(empty, False, False, 0)
            else:
                for rss_mb, pid, name in processes:
                    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
                    process_label = Gtk.Label(label=f"{name}  (PID {pid})")
                    process_label.set_halign(Gtk.Align.START)
                    process_label.set_ellipsize(Pango.EllipsizeMode.END)
                    process_label.set_xalign(0)
                    memory_label = Gtk.Label(label=f"{rss_mb:.0f} MB")
                    memory_label.set_halign(Gtk.Align.END)
                    memory_label.set_xalign(1)
                    row.pack_start(process_label, True, True, 0)
                    row.pack_end(memory_label, False, False, self._sw_pct(1))
                    process_box.pack_start(row, False, False, 0)
            process_box.show_all()
            return True

        refresh_process_view()
        refresh_id = GLib.timeout_add_seconds(5, refresh_process_view)
        content.show_all()
        dialog.run()
        try:
            GLib.source_remove(refresh_id)
        except Exception:
            pass
        dialog.destroy()

    def _sync_settings_stats_timer(self):
        performance_open = bool(
            getattr(self, "tab", None) == 2 and
            getattr(self, "settings_data", None) and
            self.settings_data[self.settings_cat]["name"] == "Performance")
        if performance_open:
            self._read_shell_resource_usage()
            if self._settings_stats_timer_id is None:
                self._settings_stats_timer_id = GLib.timeout_add(
                    1000, self._poll_shell_resource_usage)
        elif self._settings_stats_timer_id is not None:
            try:
                GLib.source_remove(self._settings_stats_timer_id)
            except Exception:
                pass
            self._settings_stats_timer_id = None

    def _read_shell_resource_usage(self):
        """Sample this DE process's CPU share and resident memory from procfs."""
        now_wall = time.monotonic()
        now_cpu = time.process_time()
        elapsed = now_wall - self._settings_stats_last_wall
        if elapsed > 0:
            self._settings_stats_cpu_percent = max(
                0.0, (now_cpu - self._settings_stats_last_cpu) / elapsed * 100.0)
        self._settings_stats_last_wall = now_wall
        self._settings_stats_last_cpu = now_cpu
        try:
            with open("/proc/self/statm", "r") as statm:
                resident_pages = int(statm.read().split()[1])
            page_size = os.sysconf("SC_PAGE_SIZE")
            self._settings_stats_rss_mb = resident_pages * page_size / (1024 * 1024)
        except (OSError, ValueError, IndexError):
            self._settings_stats_rss_mb = None

        # System-wide CPU counters: user, nice, system, idle, iowait, irq,
        # softirq, and steal. Compare samples to report busy time over one core.
        try:
            with open("/proc/stat", "r") as stat_file:
                # Ignore guest/guest_nice columns because Linux already
                # includes their ticks in user/nice.
                cpu_fields = stat_file.readline().split()[1:9]
            counters = [int(value) for value in cpu_fields]
            total = sum(counters)
            idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
            previous = self._settings_system_cpu_baseline
            if previous is not None:
                total_delta = total - previous[0]
                idle_delta = idle - previous[1]
                if total_delta > 0:
                    self._settings_system_cpu_percent = max(
                        0.0, min(100.0, 100.0 *
                                 (total_delta - idle_delta) / total_delta))
            self._settings_system_cpu_baseline = (total, idle)
        except (OSError, ValueError, IndexError):
            self._settings_system_cpu_percent = None

        try:
            meminfo = {}
            with open("/proc/meminfo", "r") as info_file:
                for line in info_file:
                    key, value = line.split(":", 1)
                    meminfo[key] = int(value.strip().split()[0])
            total_kb = meminfo["MemTotal"]
            available_kb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0))
            used_kb = max(0, total_kb - available_kb)
            self._settings_system_memory = (
                used_kb / 1024 / 1024, total_kb / 1024 / 1024,
                100.0 * used_kb / total_kb if total_kb else 0.0)
        except (OSError, ValueError, KeyError, IndexError):
            self._settings_system_memory = None

    def _poll_shell_resource_usage(self):
        self._settings_stats_timer_id = None
        self._read_shell_resource_usage()
        if self.tab == 2 and self.settings_data[
                self.settings_cat]["name"] == "Performance":
            for row in self.settings_rows:
                label = row["label"].get_text()
                if label == "System CPU Usage":
                    row["value_label"].set_text(self._get_system_cpu_label())
                elif label == "System Memory":
                    row["value_label"].set_text(self._get_system_memory_label())
                elif label == "DE Process CPU":
                    row["value_label"].set_text(self._get_shell_cpu_label())
                elif row["label"].get_text() == "DE Process Memory":
                    row["value_label"].set_text(self._get_shell_memory_label())
            self._settings_stats_timer_id = GLib.timeout_add(
                1000, self._poll_shell_resource_usage)
        return False

    def _on_cat_clicked(self, widget, event):
        try:
            idx = self.cat_rows.index(widget)
        except ValueError:
            return True
        if idx != self.settings_cat:
            self.settings_cat = idx
            self.settings_opt = 0
            self.settings_pane = 1
            self._rebuild_options()
        return True

    def _add_setting_row(self, parent, label, value, callback, action=False):
        row = Gtk.EventBox()
        row.connect_after("draw", self._draw_orbit_widget)
        row.set_hexpand(True)
        row.set_size_request(-1, self._sh_pct(6))
        row.override_background_color(
            Gtk.StateFlags.NORMAL, Gdk.RGBA(0.11, 0.13, 0.16, 1.0))

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        box.set_margin_start(self._sw_pct(2))
        box.set_margin_end(self._sw_pct(2))

        lbl = Gtk.Label(label=label)
        lbl.set_halign(Gtk.Align.START)
        lbl.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
        box.pack_start(lbl, False, False, 0)

        val_label = Gtk.Label(label=value)
        val_label.set_halign(Gtk.Align.END)
        val_label.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.6, 0.6, 0.6, 1.0))
        val_label.set_ellipsize(Pango.EllipsizeMode.END)
        val_label.set_width_chars(18)
        box.pack_end(val_label, True, True, 0)

        row.add(box)
        row.connect("button-press-event",
                    lambda w, e: (callback() if callback else None) or True)

        parent.pack_start(row, False, False, self._font_px(4))
        self.settings_rows.append({
            'widget': row, 'label': lbl, 'value_label': val_label,
            'callback': callback, 'action': action,
        })
        return row

    def _refresh_settings_highlight(self):
        # Category pane
        for i, row in enumerate(self.cat_rows):
            on = (i == self.settings_cat and self.settings_pane == 0)
            row.override_background_color(
                Gtk.StateFlags.NORMAL,
                Gdk.RGBA(0.20, 0.25, 0.35, 1.0) if on and not self.tab_bar_focused
                else Gdk.RGBA(0.11, 0.13, 0.16, 0.0))
            row.get_style_context().remove_class("selected")
            if on and not self.tab_bar_focused:
                row.get_style_context().add_class("selected")
            row.queue_draw()
        # Options pane
        for i, row in enumerate(self.settings_rows):
            on = (i == self.settings_opt and self.settings_pane == 1)
            row['widget'].override_background_color(
                Gtk.StateFlags.NORMAL,
                Gdk.RGBA(0.20, 0.25, 0.35, 1.0) if on and not self.tab_bar_focused
                else Gdk.RGBA(0.11, 0.13, 0.16, 1.0))
            row['widget'].set_name("settings-item")
            row['widget'].get_style_context().remove_class("selected")
            if on and not self.tab_bar_focused:
                row['widget'].get_style_context().add_class("selected")
            row['widget'].queue_draw()

    def _get_scale_label(self):
        try:
            with open(os.path.expanduser("~/.config/console-de/scale")) as f:
                return f"{int(f.read().strip())}x"
        except Exception:
            return "2x"

    def _get_gpu_label(self):
        try:
            with open(os.path.expanduser("~/.config/console-de/gpu")) as f:
                return f.read().strip().upper()
        except Exception:
            return "NVIDIA"

    def _get_menu_font_size_label(self):
        try:
            with open(_menu_font_size_file) as f:
                return f"{float(f.read().strip()):.1f}x"
        except Exception:
            return "1.0x"

    def _get_menu_font_weight_label(self):
        try:
            with open(_menu_font_weight_file) as f:
                w = int(f.read().strip())
                return f"{w}"
        except Exception:
            return "600"

    def _apply_display_scale_setting(self, scale):
        """Apply labwc output/app scaling, then restart shell at its fixed 3x UI."""
        helper = os.path.expanduser("~/.config/console-de/labwc-output-scale.py")
        try:
            with open(os.path.expanduser("~/.cache/console-de-output-scale.log"), "a") as log_file:
                subprocess.Popen(
                    [sys.executable, helper], stdout=log_file, stderr=log_file,
                    start_new_session=True)
            _log(f"Requested labwc and app scale update to {scale}x")
        except Exception as exc:
            _log(f"Could not start labwc scale update: {exc}")
        # Recreate the shell after labwc changes the output's logical size.
        GLib.timeout_add(700, self._restart_shell)

    def _on_scale_changed(self):
        try:
            with open(os.path.expanduser("~/.config/console-de/scale")) as f:
                current = int(f.read().strip())
        except Exception:
            current = 2
        next_scale = (current % 3) + 1
        with open(os.path.expanduser("~/.config/console-de/scale"), "w") as f:
            f.write(str(next_scale))
        if self.settings_rows:
            self.settings_rows[0]['value_label'].set_text(f"{next_scale}x")
        _log(f"Display/app scale changed to {next_scale}x")
        self._apply_display_scale_setting(next_scale)

    def _restart_shell(self):
        _log("shell re-exec to apply new scale")
        try:
            os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
        except Exception as e:
            _log(f"restart failed: {e}")
        return False

    def _on_gpu_changed(self):
        options = ["nvidia", "amd", "off"]
        try:
            with open(os.path.expanduser("~/.config/console-de/gpu")) as f:
                current = f.read().strip()
        except Exception:
            current = "nvidia"
        idx = options.index(current) if current in options else 0
        next_gpu = options[(idx + 1) % len(options)]
        with open(os.path.expanduser("~/.config/console-de/gpu"), "w") as f:
            f.write(next_gpu)
        if len(self.settings_rows) > 1:
            self.settings_rows[1]['value_label'].set_text(next_gpu.upper())
        _log(f"GPU changed to {next_gpu}")

    def _on_menu_font_size_changed(self):
        try:
            with open(_menu_font_size_file) as f:
                current = float(f.read().strip())
        except Exception:
            current = 1.0
        sizes = [0.7, 1.0, 1.3, 1.6, 2.0]
        idx = sizes.index(current) if current in sizes else 1
        next_size = sizes[(idx + 1) % len(sizes)]
        with open(_menu_font_size_file, "w") as f:
            f.write(str(next_size))
        # Update CSS without restart
        self._apply_css()
        provider = Gtk.CssProvider()
        # Re-apply by reading the CSS we just generated
        m = self._m
        css = f"""
        #tab-label {{
            font-weight: {_menu_font_weight};
            font-size: {self._font_px(int(10 * next_size))}px;
        }}
        """
        try:
            provider.load_from_data(css.encode())
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception as e:
            _log(f"menu font CSS failed: {e}")
        if len(self.settings_rows) > 2:
            self.settings_rows[2]['value_label'].set_text(f"{next_size:.1f}x")
        _log(f"Menu font size changed to {next_size}x")

    def _on_menu_font_weight_changed(self):
        try:
            with open(_menu_font_weight_file) as f:
                current = int(f.read().strip())
        except Exception:
            current = 600
        weights = [400, 600, 800]
        idx = weights.index(current) if current in weights else 1
        next_weight = weights[(idx + 1) % len(weights)]
        with open(_menu_font_weight_file, "w") as f:
            f.write(str(next_weight))
        # Update CSS without restart
        m = self._m
        css = f"""
        #tab-label {{
            font-weight: {next_weight};
            font-size: {self._font_px(int(10 * _menu_font_size))}px;
        }}
        """
        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(css.encode())
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception as e:
            _log(f"menu font weight CSS failed: {e}")
        if len(self.settings_rows) > 3:
            self.settings_rows[3]['value_label'].set_text(f"{next_weight}")
        _log(f"Menu font weight changed to {next_weight}")

    def _on_scale_changed_backward(self):
        try:
            with open(os.path.expanduser("~/.config/console-de/scale")) as f:
                current = int(f.read().strip())
        except Exception:
            current = 2
        scales = [1, 2, 3]
        idx = scales.index(current) if current in scales else 1
        next_scale = scales[(idx - 1) % len(scales)]
        with open(os.path.expanduser("~/.config/console-de/scale"), "w") as f:
            f.write(str(next_scale))
        if self.settings_rows:
            self.settings_rows[0]['value_label'].set_text(f"{next_scale}x")
        _log(f"Display/app scale changed to {next_scale}x")
        self._apply_display_scale_setting(next_scale)

    def _on_gpu_changed_backward(self):
        options = ["nvidia", "amd", "off"]
        try:
            with open(os.path.expanduser("~/.config/console-de/gpu")) as f:
                current = f.read().strip()
        except Exception:
            current = "nvidia"
        idx = options.index(current) if current in options else 0
        next_gpu = options[(idx - 1) % len(options)]
        with open(os.path.expanduser("~/.config/console-de/gpu"), "w") as f:
            f.write(next_gpu)
        if len(self.settings_rows) > 1:
            self.settings_rows[1]['value_label'].set_text(next_gpu.upper())
        _log(f"GPU changed to {next_gpu}")

    def _on_menu_font_size_changed_backward(self):
        try:
            with open(_menu_font_size_file) as f:
                current = float(f.read().strip())
        except Exception:
            current = 1.0
        sizes = [0.7, 1.0, 1.3, 1.6, 2.0]
        idx = sizes.index(current) if current in sizes else 1
        next_size = sizes[(idx - 1) % len(sizes)]
        with open(_menu_font_size_file, "w") as f:
            f.write(str(next_size))
        m = self._m
        css = f"""
        #tab-label {{
            font-weight: {_menu_font_weight};
            font-size: {self._font_px(int(10 * next_size))}px;
        }}
        """
        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(css.encode())
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception as e:
            _log(f"menu font size backward CSS failed: {e}")
        if len(self.settings_rows) > 2:
            self.settings_rows[2]['value_label'].set_text(f"{next_size:.1f}x")
        _log(f"Menu font size changed to {next_size}x")

    def _on_menu_font_weight_changed_backward(self):
        try:
            with open(_menu_font_weight_file) as f:
                current = int(f.read().strip())
        except Exception:
            current = 600
        weights = [400, 600, 800]
        idx = weights.index(current) if current in weights else 1
        next_weight = weights[(idx - 1) % len(weights)]
        with open(_menu_font_weight_file, "w") as f:
            f.write(str(next_weight))
        m = self._m
        css = f"""
        #tab-label {{
            font-weight: {next_weight};
            font-size: {self._font_px(int(10 * _menu_font_size))}px;
        }}
        """
        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(css.encode())
            Gtk.StyleContext.add_provider_for_screen(
                Gdk.Screen.get_default(), provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        except Exception as e:
            _log(f"menu font weight backward CSS failed: {e}")
        if len(self.settings_rows) > 3:
            self.settings_rows[3]['value_label'].set_text(f"{next_weight}")
        _log(f"Menu font weight changed to {next_weight}")

    def _is_printable_key(self, kv):
        """Check if a keyval is a printable character."""
        return (Gdk.KEY_0 <= kv <= Gdk.KEY_9 or
                Gdk.KEY_a <= kv <= Gdk.KEY_z or
                Gdk.KEY_A <= kv <= Gdk.KEY_Z or
                kv in (Gdk.KEY_space, Gdk.KEY_minus, Gdk.KEY_period,
                       Gdk.KEY_KP_Minus, Gdk.KEY_KP_Decimal,
                       Gdk.KEY_KP_0, Gdk.KEY_KP_1, Gdk.KEY_KP_2,
                       Gdk.KEY_KP_3, Gdk.KEY_KP_4, Gdk.KEY_KP_5,
                       Gdk.KEY_KP_6, Gdk.KEY_KP_7, Gdk.KEY_KP_8,
                       Gdk.KEY_KP_9))

    def _show_text_input_dialog(self):
        """Open a text entry dialog to type a value for the current setting."""
        opt = self.settings_rows[self.settings_opt]
        label = opt['label'].get_text()
        current = opt['value_label'].get_text()

        dialog = Gtk.Dialog(title=f"Enter value for {label}",
                            transient_for=self,
                            flags=Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("OK", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)

        entry = Gtk.Entry()
        entry.set_text(current)
        entry.set_activates_default(True)
        entry.connect("activate", lambda e: dialog.response(Gtk.ResponseType.OK))

        box = dialog.get_content_area()
        box.pack_start(entry, True, True, 0)
        box.show_all()
        entry.grab_focus()

        response = dialog.run()
        result = entry.get_text().strip()
        dialog.destroy()

        if response == Gtk.ResponseType.OK and result:
            self._apply_text_input(result)

    def _apply_text_input(self, text):
        """Apply typed text to the current setting."""
        opt_idx = self.settings_opt
        opt = self.settings_rows[opt_idx]
        label = opt['label'].get_text()

        try:
            if label == "Display Scale":
                val = int(text)
                if val not in (1, 2, 3):
                    _log(f"Invalid scale: {val}, must be 1/2/3")
                    return
                with open(os.path.expanduser("~/.config/console-de/scale"), "w") as f:
                    f.write(str(val))
                opt['value_label'].set_text(f"{val}x")
                _log(f"Scale set to {val}x — restarting shell")
                GLib.timeout_add(500, self._restart_shell)

            elif label == "GPU":
                val = text.lower()
                if val not in ("nvidia", "amd", "off"):
                    _log(f"Invalid GPU: {val}, must be nvidia/amd/off")
                    return
                with open(os.path.expanduser("~/.config/console-de/gpu"), "w") as f:
                    f.write(val)
                opt['value_label'].set_text(val.upper())
                _log(f"GPU set to {val}")

            elif label == "Menu Font Size":
                val = float(text)
                if val <= 0:
                    _log(f"Invalid font size: {val}, must be > 0")
                    return
                global _menu_font_size
                _menu_font_size = val
                with open(_menu_font_size_file, "w") as f:
                    f.write(str(val))
                m = self._m
                css = f"""
                #tab-label {{
                    font-weight: {_menu_font_weight};
                    font-size: {self._font_px(int(10 * val))}px;
                }}
                """
                provider = Gtk.CssProvider()
                provider.load_from_data(css.encode())
                Gtk.StyleContext.add_provider_for_screen(
                    Gdk.Screen.get_default(), provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                opt['value_label'].set_text(f"{val:.1f}x")
                _log(f"Menu font size set to {val}x")

            elif label == "Menu Font Weight":
                val = int(text)
                weights = [400, 600, 800]
                if val not in weights:
                    _log(f"Invalid font weight: {val}, must be one of {weights}")
                    return
                with open(_menu_font_weight_file, "w") as f:
                    f.write(str(val))
                m = self._m
                css = f"""
                #tab-label {{
                    font-weight: {val};
                    font-size: {self._font_px(int(10 * _menu_font_size))}px;
                }}
                """
                provider = Gtk.CssProvider()
                provider.load_from_data(css.encode())
                Gtk.StyleContext.add_provider_for_screen(
                    Gdk.Screen.get_default(), provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
                opt['value_label'].set_text(str(val))
                _log(f"Menu font weight set to {val}")

        except ValueError:
            _log(f"Invalid input: {text}")
        except Exception as e:
            _log(f"Text input error: {e}")

    def _on_logout(self):
        _log("Settings: Log Out")
        self._stop_music()
        sh(["pkill", "-TERM", "-x", "labwc"])
        self.destroy()

    def _on_shutdown(self):
        _log("Settings: Shut Down")
        self._raw_hid_capture_stop.set()
        sh(["systemctl", "poweroff"])
        self.destroy()

    def _set_tab(self, idx):
        if idx not in (0, 1, 2):
            return
        previous_tab = self.tab
        page_name = ("games", "apps", "settings")[idx]
        page_label = ("Games", "Apps", "Settings")[idx]
        _log(f"Page: {page_label}")
        self.tab = idx
        if idx == 1:
            self._start_app_tile_build()
        self._sync_settings_stats_timer()
        if idx in (0, 1):
            self.tab_focus_idx = (0, 1)[idx]
        self._update_tab_focus_frame()
        active_header_tab = idx
        for i, lbl in enumerate(self.tab_labels):
            if idx in (0, 1) and i == active_header_tab:
                lbl.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(1.0, 1.0, 1.0, 1.0))
                # Active tab: bold + slightly larger
                desc = Pango.FontDescription()
                desc.set_family("PS5 Font")
                desc.set_weight(Pango.Weight.BOLD)
                desc.set_size(self._font_px(int(12 * _menu_font_size)) * Pango.SCALE)
                lbl.override_font(desc)
            else:
                lbl.override_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0.54, 0.56, 0.59, 1.0))
                # Inactive tab: normal weight
                desc = Pango.FontDescription()
                desc.set_family("PS5 Font")
                desc.set_weight(Pango.Weight.NORMAL)
                desc.set_size(self._font_px(int(10 * _menu_font_size)) * Pango.SCALE)
                lbl.override_font(desc)
        self.stack.set_visible_child_name(page_name)
        if idx == 1:
            self._refresh_app_selection()
            if (previous_tab != 1 and
                    self._app_tile_build_index >= len(self.apps)):
                self._animate_apps_in()
        elif idx == 2:
            self._refresh_settings_highlight()
            self._sync_settings_stats_timer()
        self._sync_background()
        _log(f"Page: {page_label}")

    def _update_tab_focus_frame(self):
        """Show the white selection frame on the focused header item."""
        for i, frame in enumerate(self.tab_focus_frames):
            ctx = frame.get_style_context()
            if self.tab_bar_focused and self.tab_focus_idx == i:
                ctx.add_class("focused")
            else:
                ctx.remove_class("focused")
            frame.queue_draw()
        icon_idx = self.tab_focus_idx - len(self.TABS)
        for i, frame in enumerate(self.top_icon_frames):
            ctx = frame.get_style_context()
            selected = self.tab_bar_focused and icon_idx == i
            if selected:
                ctx.add_class("focused")
            else:
                ctx.remove_class("focused")
            if frame is getattr(self, "settings_icon_frame", None):
                icon = (self._settings_icon_selected if selected
                        else self._settings_icon_original)
                if icon is not None:
                    self.settings_icon.set_from_pixbuf(icon)
            elif frame is getattr(self, "search_icon_frame", None):
                icon = (self._search_icon_selected if selected
                        else self._search_icon_original)
                if icon is not None:
                    self.search_icon.set_from_pixbuf(icon)
            frame.queue_draw()

    def _enter_tab_bar(self):
        self.tab_focus_idx = (len(self.TABS) + 1 if self.tab == 2 else self.tab)
        self.tab_bar_focused = True
        self._update_tab_focus_frame()
        if hasattr(self, "carousel"):
            self.carousel.queue_draw()
        if self.tab == 1:
            self._refresh_app_selection()
        elif self.tab == 2:
            self._refresh_settings_highlight()
        self._play_sfx("PS5_Change")
        focused_label = (self.TABS[self.tab_focus_idx]
                         if self.tab_focus_idx < len(self.TABS)
                         else ("Search", "Settings icon", "Profile")[
                             self.tab_focus_idx - len(self.TABS)])
        _log(f"Tab menu focused: {focused_label}")

    def _leave_tab_bar(self, commit=False):
        """Leave tab focus, optionally switching to the focused tab."""
        if not self.tab_bar_focused:
            return
        idx = self.tab_focus_idx
        self.tab_bar_focused = False
        self._update_tab_focus_frame()
        if commit and idx < len(self.TABS):
            self._set_tab((0, 1)[idx])
        else:
            self.tab_focus_idx = self.tab
        if hasattr(self, "carousel"):
            self.carousel.queue_draw()
        if self.tab == 1:
            self._refresh_app_selection()
        elif self.tab == 2:
            self._refresh_settings_highlight()
        self._play_sfx("PS5_Change")

    def _move_tab_focus(self, direction):
        item_count = len(self.TABS) + len(self.top_icon_frames)
        idx = max(0, min(self.tab_focus_idx + direction, item_count - 1))
        if idx == self.tab_focus_idx:
            return
        self.tab_focus_idx = idx
        self._update_tab_focus_frame()
        self._play_sfx("PS5_Change")
        if idx < len(self.TABS):
            label = self.TABS[idx]
        else:
            label = ("Search", "Settings icon", "Profile")[idx - len(self.TABS)]
        _log(f"Header selection: {label}")

    def _draw_orbit_widget(self, widget, cr):
        """Draw the animated blue-violet selection frame over a widget."""
        if not HAVE_CAIRO:
            return False
        ctx = widget.get_style_context()
        if not (ctx.has_class("focused") or ctx.has_class("selected")):
            return False
        alloc = widget.get_allocation()
        inset = max(1.0, self._font_px(1) / 2.0)
        if widget in (getattr(self, "settings_icon_frame", None),
                      getattr(self, "search_icon_frame", None)):
            diameter = min(alloc.width, alloc.height) - 2 * inset
            radius = diameter / 2.0
            cx, cy = alloc.width / 2.0, alloc.height / 2.0
            cr.save()
            cr.arc(cx, cy, radius, 0, 2 * math.pi)
            cr.set_source_rgba(0.91, 0.93, 0.97, 0.98)
            cr.fill()
            self._paint_orbit_border(
                cr, cx - radius, cy - radius, diameter, diameter,
                radius, max(2.0, self._font_px(1)))
            cr.restore()
            return False
        else:
            radius = min(self._font_px(9), alloc.width / 4, alloc.height / 4)
        self._paint_orbit_border(
            cr, inset, inset, alloc.width - 2 * inset,
            alloc.height - 2 * inset,
            radius, max(2.0, self._font_px(1)))
        return False

    def _paint_orbit_border(self, cr, x, y, w, h, radius, line_width, alpha=1.0):
        """Paint a rounded indigo frame whose glow flows around the full edge."""
        if not HAVE_CAIRO or w <= 2 or h <= 2 or alpha <= 0:
            return
        alpha *= 0.5
        line_width *= 0.7
        radius = max(1.0, min(radius, w / 2.0, h / 2.0))
        perimeter = 2 * (w + h - 4 * radius) + 2 * math.pi * radius
        if perimeter <= 0:
            return

        def path():
            cr.new_sub_path()
            cr.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
            cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
            cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
            cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
            cr.close_path()

        cr.save()
        cr.set_line_cap(cairo.LINE_CAP_ROUND)

        # Soft, consistent outer glow, then the subdued continuous outline.
        path()
        cr.set_line_width(line_width * 2.2)
        cr.set_source_rgba(0.30, 0.38, 0.96, 0.07 * alpha)
        cr.stroke()

        path()
        grad = cairo.LinearGradient(x, y, x + w, y + h)
        grad.add_color_stop_rgba(0.0, 0.39, 0.42, 0.88, 0.38 * alpha)
        grad.add_color_stop_rgba(0.5, 0.36, 0.55, 0.98, 0.56 * alpha)
        grad.add_color_stop_rgba(1.0, 0.50, 0.42, 0.91, 0.38 * alpha)
        cr.set_source(grad)
        cr.set_line_width(line_width)
        cr.stroke()

        # Split the whole perimeter into short, smoothly colored strokes. A
        # soft brightness wave travels around it without forming a separate dash.
        parts = [
            ("line", (x + radius, y, x + w - radius, y), w - 2 * radius),
            ("arc", (x + w - radius, y + radius, radius, -math.pi / 2, 0), math.pi * radius / 2),
            ("line", (x + w, y + radius, x + w, y + h - radius), h - 2 * radius),
            ("arc", (x + w - radius, y + h - radius, radius, 0, math.pi / 2), math.pi * radius / 2),
            ("line", (x + w - radius, y + h, x + radius, y + h), w - 2 * radius),
            ("arc", (x + radius, y + h - radius, radius, math.pi / 2, math.pi), math.pi * radius / 2),
            ("line", (x, y + h - radius, x, y + radius), h - 2 * radius),
            ("arc", (x + radius, y + radius, radius, math.pi, 3 * math.pi / 2), math.pi * radius / 2),
        ]

        def point_at(distance):
            distance %= perimeter
            for kind, values, length in parts:
                if distance <= length:
                    t = 0.0 if length <= 0 else distance / length
                    if kind == "line":
                        x1, y1, x2, y2 = values
                        return (x1 + (x2 - x1) * t, y1 + (y2 - y1) * t)
                    cx, cy, r, a0, a1 = values
                    angle = a0 + (a1 - a0) * t
                    return (cx + r * math.cos(angle), cy + r * math.sin(angle))
                distance -= length
            return (x + radius, y)

        phase = ((time.monotonic() - self._orbit_started_at) % 9.0) / 9.0
        segment_count = max(96, min(400, int(perimeter / 1.5)))
        segment_len = perimeter / segment_count
        low = (0.28, 0.34, 0.70)
        high = (0.72, 0.85, 1.0)
        for i in range(segment_count):
            start = i * segment_len
            end = (i + 1) * segment_len
            mid = (start + end) / (2.0 * perimeter)
            wave = 0.5 + 0.5 * math.cos(2 * math.pi * (mid - phase))
            blend = wave * wave * (3 - 2 * wave)
            color = tuple(a + (b - a) * blend for a, b in zip(low, high))
            p0 = point_at(start)
            p1 = point_at(end)

            cr.new_path()
            cr.move_to(*p0)
            cr.line_to(*p1)
            cr.set_source_rgba(*color, alpha * (0.08 + 0.14 * wave))
            cr.set_line_width(line_width * 1.4)
            cr.stroke()

            cr.new_path()
            cr.move_to(*p0)
            cr.line_to(*p1)
            cr.set_source_rgba(*color, alpha * (0.36 + 0.34 * wave))
            cr.set_line_width(line_width)
            cr.stroke()
        cr.restore()

    def _tick_orbit_border(self, _widget, frame_clock, _user_data=None):
        """Refresh the selected glow at 30 fps while the home UI is visible."""
        if not self._startup_hidden:
            return True
        frame_time = frame_clock.get_frame_time() / 1_000_000.0
        if frame_time - self._orbit_last_frame_time < 1.0 / 30.0:
            return True
        self._orbit_last_frame_time = frame_time
        if self.tab_bar_focused:
            if self.tab_focus_idx < len(self.TABS):
                self.tab_focus_frames[self.tab_focus_idx].queue_draw()
            else:
                idx = self.tab_focus_idx - len(self.TABS)
                if idx < len(self.top_icon_frames):
                    self.top_icon_frames[idx].queue_draw()
        elif self.tab == 0 and self.games:
            self.carousel.queue_draw()
        elif self.tab == 1 and self.asel < len(self.app_tiles):
            self.app_tiles[self.asel].queue_draw()
        elif self.tab == 2:
            if self.settings_pane == 0 and self.settings_cat < len(self.cat_rows):
                self.cat_rows[self.settings_cat].queue_draw()
            elif self.settings_pane == 1 and self.settings_opt < len(self.settings_rows):
                self.settings_rows[self.settings_opt]["widget"].queue_draw()
        return True

    def _compose_background(self, pb):
        """Bake the page background into the pixbuf.

        Done at the pixbuf level (instead of a separate overlay widget) so the
        gradient can never stack above the content and cover UI elements.
        """
        if not HAVE_CAIRO:
            return pb
        w, h = self._sw, self._sh
        if pb is None and self.tab in (2, 3):
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
            _paint_settings_gradient(cairo.Context(surface), w, h)
            return Gdk.pixbuf_get_from_surface(surface, 0, 0, w, h)
        if pb and pb.get_width() == w and pb.get_height() == h:
            base = pb
        else:
            base = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, w, h)
            base.fill(0x0d1117ff)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surface)
        Gdk.cairo_set_source_pixbuf(cr, base, 0, 0)
        cr.paint()
        grad = cairo.LinearGradient(0, 0, w, 0)
        grad.add_color_stop_rgba(0.0, 0.02, 0.03, 0.05, 0.88)
        grad.add_color_stop_rgba(0.55, 0.02, 0.03, 0.05, 0.55)
        grad.add_color_stop_rgba(1.0, 0.02, 0.03, 0.05, 0.35)
        cr.set_source(grad)
        cr.paint()
        return Gdk.pixbuf_get_from_surface(surface, 0, 0, w, h)

    def _sync_background(self):
        """Show selected game's key art on Games tab; dark gradient elsewhere."""
        try:
            pb = None
            if self.tab == 0 and self.games:
                art = _bg_art_for(self.games[self.gsel])
                if art:
                    _log(f"BG art found: {art}")
                    pb = _cover_pixbuf(art, self._sw, self._sh)
                    if pb:
                        _log("BG art set on window")
                    else:
                        _log("BG art pixbuf failed")
                else:
                    _log(f"No BG art found for appid={self.games[self.gsel].get('appid')}")
            composed = self._compose_background(pb)
            if composed:
                self.bg_image.set_from_pixbuf(composed)
            else:
                self.bg_image.clear()
        except Exception as e:
            _log(f"bg sync failed: {e}")

    def _update_clock(self):
        text = time.strftime("%H:%M")
        self.clock_label.set_text(text)
        return True

    def _load_profile_picture(self):
        """Load user profile picture from GDM/login manager face icon."""
        import getpass
        username = getpass.getuser()
        face_paths = [
            f"/var/lib/AccountsService/icons/{username}",
            os.path.expanduser("~/.face"),
            os.path.expanduser("~/.config/console-de/profile.png"),
        ]
        for path in face_paths:
            if os.path.exists(path):
                try:
                    pb = GdkPixbuf.Pixbuf.new_from_file(path)
                    pw, ph = pb.get_width(), pb.get_height()
                    if pw > 0 and ph > 0:
                        size = self._font_px(22)
                        pb = pb.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
                        self.profile_pic.set_from_pixbuf(pb)
                        _log(f"Profile picture loaded: {path}")
                        return
                except Exception as e:
                    _log(f"Failed to load profile picture {path}: {e}")
        # Fallback: user icon emoji
        self.profile_pic.clear()
        self.profile_pic.set_from_icon_name("system-users", Gtk.IconSize.DIALOG)
        _log("Profile picture: using fallback icon")

    def _get_profile_pixbuf(self):
        """Return the user's account image for the control-center profile icon."""
        import getpass
        username = getpass.getuser()
        face_paths = (
            f"/var/lib/AccountsService/icons/{username}",
            os.path.expanduser("~/.face"),
            os.path.expanduser("~/.config/console-de/profile.png"),
        )
        for path in face_paths:
            if os.path.exists(path):
                try:
                    return GdkPixbuf.Pixbuf.new_from_file(path)
                except Exception as exc:
                    _log(f"Failed to load control-center profile picture {path}: {exc}")
        return None

    def _load_icon(self, image_widget, name, size):
        """Load an icon PNG from ~/.config/console-de/icons/."""
        icon_path = os.path.expanduser(f"~/.config/console-de/icons/{name}.png")
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(icon_path)
            pb = pb.scale_simple(size, size, GdkPixbuf.InterpType.BILINEAR)
            image_widget.set_from_pixbuf(pb)
            _log(f"Icon loaded: {icon_path}")
        except Exception as e:
            _log(f"Failed to load icon {icon_path}: {e}")
            image_widget.clear()

    def _update_game_info(self):
        if self.games and 0 <= self.gsel < len(self.games):
            g = self.games[self.gsel]
            source = "Steam" if g.get('steam') else "App"
            self.game_subtitle.set_text(f"Ready to play  ·  {source}".title())
            # Load title art (logo.png) above the Play button
            title_art = _find_title_art(g.get('appid'))
            if title_art:
                self._set_title_art(title_art)
            else:
                self.title_art.clear()
        else:
            self.game_subtitle.set_text("")
            self.title_art.clear()
        self._sync_background()

    def _set_title_art(self, path):
        """Load title art (logo.png) above the Play button."""
        try:
            pb = GdkPixbuf.Pixbuf.new_from_file(path)
        except Exception:
            self.title_art.clear()
            return
        pw, ph = pb.get_width(), pb.get_height()
        if pw <= 0 or ph <= 0:
            self.title_art.clear()
            return
        tw = self._sw_pct(30)
        th = max(1, int(ph * tw / pw))
        pb = pb.scale_simple(tw, th, GdkPixbuf.InterpType.BILINEAR)
        self.title_art.set_from_pixbuf(pb)

    # ---- Sound effects ----

    def _play_sfx(self, name):
        """Play a short sound effect (non-blocking)."""
        path = os.path.expanduser(f"~/.config/console-de/sfx/{name}.mp3")
        if not os.path.isfile(path):
            _log(f"SFX file not found: {path}")
            return
        if not shutil.which("ffplay"):
            _log("SFX unavailable: ffplay is missing (install ffmpeg)")
            return
        try:
            cmd = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
            if name == "PS5_Change":
                # Change sound on navigation
                cmd += ["-af", "volume=4"]
            else:
                # Select sound on confirm
                cmd += ["-af", "volume=2.1"]
            cmd += [path]
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
        except Exception as e:
            _log(f"SFX play failed ({name}): {e}")

    # ---- Navigation ----

    def _nav_up(self):
        _log("Nav: Up")
        if self.control_center_open:
            if self.control_center_power_open:
                self._move_control_center_power_choice(-1)
            return
        if self.tab_bar_focused:
            return
        elif self.tab == 0:
            if self.games and self.more_btn_focused:
                self.more_btn_focused = False
                self._set_more_button_highlight(False)
                self.play_btn_focused = True
                self._set_play_button_highlight(True)
                self.carousel.queue_draw()
                self._play_sfx("PS5_Change")
                return
            if self.games and self.play_btn_focused:
                self.play_btn_focused = False
                self._set_play_button_highlight(False)
                self.carousel.queue_draw()
                self._play_sfx("PS5_Change")
                return
            self._enter_tab_bar()
        elif self.tab == 1:
            if not self.apps or self.asel < self._app_cols:
                self._enter_tab_bar()
            else:
                self.asel = max(self.asel - self._app_cols, 0)
                self._refresh_app_selection()
                self._play_sfx("PS5_Change")
        elif self.tab == 2:
            if self.settings_pane == 1:
                if self.settings_opt > 0:
                    self.settings_opt -= 1
                    self._refresh_settings_highlight()
                    self._play_sfx("PS5_Change")
                else:
                    self._enter_tab_bar()
            else:
                if self.settings_cat > 0:
                    self.settings_cat -= 1
                    self.settings_opt = 0
                    self._rebuild_options()
                    self._play_sfx("PS5_Change")
                else:
                    self._enter_tab_bar()

    def _nav_down(self):
        _log("Nav: Down")
        if self.control_center_open:
            if self.control_center_power_open:
                self._move_control_center_power_choice(1)
            return
        if self.tab_bar_focused:
            self._leave_tab_bar(commit=False)
            return
        elif self.tab == 0 and self.games:
            if not self.play_btn_focused:
                self.play_btn_focused = True
                self._set_play_button_highlight(True)
                self.carousel.queue_draw()
                self._play_sfx("PS5_Change")
                return
        elif self.tab == 1 and self.apps:
            self.asel = min(self.asel + self._app_cols, len(self.apps) - 1)
            self._refresh_app_selection()
            self._play_sfx("PS5_Change")
        elif self.tab == 2:
            if self.settings_pane == 1:
                if self.settings_opt < len(self.settings_rows) - 1:
                    self.settings_opt += 1
                    self._refresh_settings_highlight()
                    self._play_sfx("PS5_Change")
            else:
                if self.settings_cat < len(self.settings_data) - 1:
                    self.settings_cat += 1
                    self.settings_opt = 0
                    self._rebuild_options()
                    self._play_sfx("PS5_Change")



    def _nav_left(self):
        _log("Nav: Left")
        if self.control_center_open:
            if self.control_center_power_open:
                self._move_control_center_power_choice(-1)
                return
            self._move_control_center_selection(-1)
            return
        if self.tab_bar_focused:
            self._move_tab_focus(-1)
            return
        elif self.tab == 0 and self.games:
            if self.more_btn_focused:
                # Move focus from More button back to Play button
                self.more_btn_focused = False
                self._set_more_button_highlight(False)
                self.play_btn_focused = True
                self._set_play_button_highlight(True)
                self._play_sfx("PS5_Change")
                return
            if self.play_btn_focused:
                # Unfocus buttons, return to game tiles
                self.play_btn_focused = False
                self._set_play_button_highlight(False)
                self.carousel.queue_draw()
                self._play_sfx("PS5_Change")
                return
            prev = self.gsel
            self.gsel = max(self.gsel - 1, 0)
            self._refresh_selection(prev=prev)
            self._play_sfx("PS5_Change")
        elif self.tab == 1 and self.apps:
            self.asel = max(self.asel - 1, 0)
            self._refresh_app_selection()
            self._play_sfx("PS5_Change")
        elif self.tab == 2:
            if self.settings_pane == 1:
                # Cycle current option value backward (Left)
                cb = self.settings_rows[self.settings_opt].get('cb_backward')
                if cb:
                    cb()
                    self._play_sfx("PS5_Change")
                return
            elif self.settings_pane == 0:
                if self.settings_cat > 0:
                    self.settings_cat -= 1
                    self.settings_opt = 0
                    self._rebuild_options()
                    self._play_sfx("PS5_Change")

    def _nav_right(self):
        _log("Nav: Right")
        if self.control_center_open:
            if self.control_center_power_open:
                self._move_control_center_power_choice(1)
                return
            self._move_control_center_selection(1)
            return
        if self.tab_bar_focused:
            self._move_tab_focus(1)
            return
        elif self.tab == 0 and self.games:
            if self.play_btn_focused and not self.more_btn_focused:
                # Move focus from Play button to More button
                self.play_btn_focused = False
                self._set_play_button_highlight(False)
                self.more_btn_focused = True
                self._set_more_button_highlight(True)
                self.carousel.queue_draw()
                self._play_sfx("PS5_Change")
                return
            if self.more_btn_focused:
                # Unfocus buttons, return to game tiles
                self.more_btn_focused = False
                self._set_more_button_highlight(False)
                self.carousel.queue_draw()
                self._play_sfx("PS5_Change")
                return
            prev = self.gsel
            self.gsel = min(self.gsel + 1, len(self.games) - 1)
            self._refresh_selection(prev=prev)
            self._play_sfx("PS5_Change")
        elif self.tab == 1 and self.apps:
            self.asel = min(self.asel + 1, len(self.apps) - 1)
            self._refresh_app_selection()
            self._play_sfx("PS5_Change")
        elif self.tab == 2:
            if self.settings_pane == 1:
                # Cycle current option value forward (Right)
                cb = self.settings_rows[self.settings_opt]['callback']
                if cb:
                    cb()
                    self._play_sfx("PS5_Change")
                return
            elif self.settings_pane == 0:
                if self.settings_cat < len(self.settings_data) - 1:
                    self.settings_cat += 1
                    self.settings_opt = 0
                    self._rebuild_options()
                    self._play_sfx("PS5_Change")

    def _nav_activate(self):
        _log("Nav: Activate (A/Enter)")
        if self.control_center_open:
            if self.control_center_power_open:
                self.control_center_power_buttons[
                    self.control_center_power_choice_idx].clicked()
                return
            self._activate_control_center_item()
            return
        if self.tab_bar_focused:
            focused = self.tab_focus_idx
            if focused < len(self.TABS):
                self._set_tab((0, 1)[focused])
            if focused == len(self.TABS) + 1:
                self._set_tab(2)
            self._leave_tab_bar(commit=False)
            self._play_sfx("PS5_Select")
            return
        if self.tab == 0 and self.games:
            self._play_sfx("PS5_Select")
            self._play_selected()
        elif self.tab == 1 and self.apps:
            self._play_sfx("PS5_Select")
            self._launch_app()
        elif self.tab == 2:
            if self.settings_pane == 0:
                self.settings_pane = 1
                self.settings_opt = 0
                self._refresh_settings_highlight()
                self._sync_settings_stats_timer()
                self._play_sfx("PS5_Select")
            elif self.settings_pane == 1:
                # Enter does NOT change value rows (Left/Right cycles those),
                # but action rows (Log Out, Shut Down) must confirm on Enter/A.
                opt = self.settings_rows[self.settings_opt]
                if opt['action'] and opt['callback']:
                    self._play_sfx("PS5_Select")
                    opt['callback']()

    def _set_play_button_highlight(self, highlighted):
        """Toggle play button highlight: white bg + black text when selected."""
        if highlighted:
            self.play_box.override_background_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            self.play_btn.override_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 1))
            # Remove CSS class since we use Python override
            ctx = self.play_box.get_style_context()
            ctx.remove_class("highlighted")
        else:
            self.play_box.override_background_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(0.24, 0.24, 0.26, 0.4))
            self.play_btn.override_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            ctx = self.play_box.get_style_context()
            ctx.remove_class("highlighted")

    def _set_more_button_highlight(self, highlighted):
        """Toggle more button highlight: white bg + black text when selected."""
        if highlighted:
            self.more_box.override_background_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            self.more_btn.override_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 1))
        else:
            self.more_box.override_background_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(0.24, 0.24, 0.26, 0.4))
            self.more_btn.override_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))

    def _on_play_button_press(self, widget, event):
        """Handle Play button click — launch the selected game."""
        self._play_selected()
        return True

    def _on_more_button_press(self, widget, event):
        """Handle More options button click — placeholder."""
        _log("More options button pressed")
        return True

    def _nav_next_tab(self):
        _log("Nav: Next Tab")
        self._play_sfx("PS5_Change")
        order = (0, 1)
        self._set_tab(order[(order.index(self.tab) + 1) % len(order)] if self.tab in order else 0)

    def _nav_prev_tab(self):
        _log("Nav: Prev Tab")
        self._play_sfx("PS5_Change")
        order = (0, 1)
        self._set_tab(order[(order.index(self.tab) - 1) % len(order)] if self.tab in order else 0)

    def _nav_back(self):
        """Go back one menu level (B / ESC):
        settings options pane -> settings categories pane;
        settings categories pane -> games tab;
        games/apps tab -> no-op (stay)."""
        _log("Nav: Back (B/Esc)")
        self._play_sfx("PS5_Change")
        if self.control_center_open:
            if self.control_center_power_open:
                self.control_center_power_card.hide()
                self.control_center_power_card.set_no_show_all(True)
                self.control_center_power_open = False
            else:
                self._close_control_center()
            return True
        if self.tab_bar_focused:
            self._leave_tab_bar(commit=False)
            return
        if self.tab == 2:
            if self.settings_pane == 1:
                self.settings_pane = 0
                self.settings_opt = 0
                self._refresh_settings_highlight()
                self._sync_settings_stats_timer()
            else:
                self._set_tab(0)

    # ---- Keyboard ----

    def on_key(self, widget, event):
        if not self._startup_hidden:
            return True
        kv = event.keyval
        if kv == Gdk.KEY_Home:
            self._toggle_control_center()
            return True
        if self._controller_owner == "game" and not self.control_center_open:
            # Leave ordinary keyboard input to the foreground game too.
            return False
        if self.control_center_open:
            if kv == Gdk.KEY_Left:
                self._nav_left()
            elif kv == Gdk.KEY_Right:
                self._nav_right()
            elif kv == Gdk.KEY_Up:
                self._nav_up()
            elif kv == Gdk.KEY_Down:
                self._nav_down()
            elif kv == Gdk.KEY_Return or kv == Gdk.KEY_KP_Enter:
                self._nav_activate()
            elif kv == Gdk.KEY_Escape:
                self._nav_back()
            return True
        self._dismiss_toast()
        _log(f"Key pressed: {Gdk.keyval_name(kv)}")
        # Ctrl+Alt+Delete = Log Out
        if (kv == Gdk.KEY_Delete
                and (event.state & Gdk.ModifierType.CONTROL_MASK)
                and (event.state & Gdk.ModifierType.MOD1_MASK)):
            _log("Ctrl+Alt+Delete -> Logout")
            self._on_logout()
            return True
        if kv == Gdk.KEY_q and (event.state & Gdk.ModifierType.CONTROL_MASK):
            _log("Ctrl+Q -> Quit")
            self.destroy()
            return True
        elif kv == Gdk.KEY_Right:
            self._nav_right(); return True
        elif kv == Gdk.KEY_Left:
            self._nav_left(); return True
        elif kv == Gdk.KEY_Up:
            self._nav_up(); return True
        elif kv == Gdk.KEY_Down:
            self._nav_down(); return True
        elif kv == Gdk.KEY_Tab:
            self._nav_next_tab(); return True
        elif kv in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._nav_activate(); return True
        elif kv == Gdk.KEY_Escape:
            self._nav_back(); return True
        # Settings options pane: type a value directly
        elif self.tab == 2 and self.settings_pane == 1:
            if self._is_printable_key(kv):
                self._show_text_input_dialog(); return True
        return False

    # ---- Gamepad (evdev) ----

    def _init_gamepad(self):
        if not HAVE_EVDEV:
            if not getattr(self, "_evdev_unavailable_logged", False):
                _log("evdev not available, gamepad disabled")
                self._evdev_unavailable_logged = True
            return False
        if self._pad_dev is not None or self._gamepad_scan_in_progress:
            return False

        self._gamepad_scan_in_progress = True

        def scan_devices():
            discovered = []
            scan_errors = []
            for path in sorted(glob.glob("/dev/input/event*")):
                dev = None
                try:
                    dev = evdev.InputDevice(path)
                    keys = dev.capabilities().get(ecodes.EV_KEY, [])
                except Exception as exc:
                    try:
                        if dev is not None:
                            dev.close()
                    except Exception:
                        pass
                    scan_errors.append(f"{path}: {exc}")
                    continue
                discovered.append((dev, keys))

            primary = next((dev for dev, keys in discovered
                            if ecodes.BTN_SOUTH in keys and
                            ecodes.BTN_START in keys), None)
            if primary is None:
                names = [f"{dev.name} ({dev.path})"
                         for dev, _keys in discovered[:8]]
                for dev, _keys in discovered:
                    try:
                        dev.close()
                    except Exception:
                        pass
                GLib.idle_add(self._finish_gamepad_scan_missing,
                              len(discovered), names, scan_errors)
                return
            GLib.idle_add(self._finish_gamepad_scan_found,
                          discovered, primary, scan_errors)

        threading.Thread(target=scan_devices, name="gamepad-discovery",
                         daemon=True).start()
        return False

    def _finish_gamepad_scan_missing(self, device_count, names, scan_errors):
        self._gamepad_scan_in_progress = False
        now = time.monotonic()
        if now - self._last_gamepad_scan_log >= 10:
            detail = "; ".join(scan_errors[:4])
            suffix = "; ".join(part for part in (", ".join(names), detail) if part)
            _log(f"No gamepad found in {device_count} readable input devices; "
                 f"retrying{': ' + suffix if suffix else ''}")
            self._last_gamepad_scan_log = now
        return False

    def _finish_gamepad_scan_found(self, discovered, primary, scan_errors):
        self._gamepad_scan_in_progress = False
        if self._pad_dev is not None:
            for dev, _keys in discovered:
                try:
                    dev.close()
                except Exception:
                    pass
            return False
        if scan_errors:
            now = time.monotonic()
            if now - self._last_gamepad_scan_log >= 10:
                _log("Some input nodes were unreadable: " + "; ".join(scan_errors[:4]))
                self._last_gamepad_scan_log = now

        self._pad_dev = primary
        self._pad_devices = [primary]
        _log(f"Gamepad found: {primary.name} ({primary.path})")
        primary_hardware = self._input_device_hardware_id(primary)
        info = getattr(primary, "info", None)
        if (getattr(info, "vendor", 0), getattr(info, "product", 0)) == (0x3537, 0x100B):
            self._raw_hid_capture_stop.clear()
            threading.Thread(
                target=self._raw_hid_capture_worker,
                args=(primary_hardware,),
                name="gamesir-share-capture",
                daemon=True).start()
        capture_candidates = {
            getattr(ecodes, name, None)
            for name in ("KEY_RECORD", "KEY_SYSRQ", "KEY_PRINT",
                         "KEY_SCREENSHOT", "KEY_CAMERA")
        }
        capture_candidates.discard(None)
        for dev, keys in discovered:
            if dev is not primary:
                codes = capture_candidates.intersection(keys)
                same_hardware = (
                    primary_hardware is not None and
                    self._input_device_hardware_id(dev) == primary_hardware)
                if codes and same_hardware:
                    self._capture_devices.append(dev)
                    self._capture_device_codes[dev.fd] = codes
                    watch_id = GLib.io_add_watch(
                        dev.fd, GLib.IO_IN, self._on_capture_readable)
                    self._capture_watch_ids.append(watch_id)
                    _log(f"Capture-button companion found: {dev.name} "
                         f"({dev.path}), codes={sorted(codes)}")
                else:
                    if codes:
                        _log(f"Ignoring unrelated capture-capable input: "
                             f"{dev.name} ({dev.path})")
                    dev.close()
                continue
            # KEY_RECORD is the normal Xbox Series Share event. Some generic
            # HID controllers identify a dedicated capture key as KEY_SYSRQ or
            # KEY_CAMERA. Happy1 is only a fallback when it is not part of the
            # four-button D-pad mapping used by some gamepads.
            happy_codes = [getattr(ecodes, f"BTN_TRIGGER_HAPPY{i}", None)
                           for i in range(1, 5)]
            happy1 = happy_codes[0]
            if happy1 is not None and happy1 in keys and not all(
                    code in keys for code in happy_codes if code is not None):
                capture_candidates.add(happy1)
            self._capture_button_codes.update(
                code for code in capture_candidates
                if code is not None and code in keys)
            watch_id = GLib.io_add_watch(dev.fd, GLib.IO_IN, self._on_pad_readable)
            self._pad_watch_ids.append(watch_id)
            if dev is primary:
                self._pad_src = watch_id

        if self._capture_button_codes:
            _log(f"Dedicated capture button code(s): {sorted(self._capture_button_codes)}")
        if not self._capture_button_codes and not self._capture_devices:
            _log("No recognized capture button on gamepad or companion input nodes")
        if self._gamepad_passthrough_active:
            _log("Controller discovered while a game is focused; leaving it available to the game")
        else:
            try:
                for dev in self._pad_devices:
                    dev.grab()
                    _log(f"Controller interface grabbed by DE: {dev.path}")
            except Exception as e:
                _log(f"Controller interface grab failed (may need permissions): {e}")
        self._on_controller_connected()
        return False

    @staticmethod
    def _input_device_hardware_id(dev):
        """Return a stable physical-device ID shared by evdev siblings."""
        event_name = os.path.basename(dev.path)
        hardware = ShellWindow._sysfs_usb_hardware_id(
            os.path.join("/sys/class/input", event_name, "device"))
        if hardware:
            return hardware
        info = getattr(dev, "info", None)
        vendor = getattr(info, "vendor", 0)
        product = getattr(info, "product", 0)
        phys = getattr(dev, "phys", "") or ""
        phys = re.sub(r"/input\d+(?:/.*)?$", "", phys)
        if phys:
            return f"phys:{vendor:04x}:{product:04x}:{phys}"
        if vendor or product:
            return f"id:{vendor:04x}:{product:04x}"
        return None

    @staticmethod
    def _sysfs_usb_hardware_id(sysfs_path):
        current = os.path.realpath(sysfs_path)
        while current and current != os.path.dirname(current):
            if (os.path.isfile(os.path.join(current, "idVendor")) and
                    os.path.isfile(os.path.join(current, "idProduct")) and
                    os.path.isfile(os.path.join(current, "busnum"))):
                try:
                    with open(os.path.join(current, "idVendor"), encoding="ascii") as f:
                        vendor = f.read().strip()
                    with open(os.path.join(current, "idProduct"), encoding="ascii") as f:
                        product = f.read().strip()
                    return f"usb:{vendor}:{product}:{current}"
                except OSError:
                    pass
            current = os.path.dirname(current)
        return None

    def _raw_hid_capture_nodes(self, expected_hardware):
        nodes = []
        for sysfs in glob.glob("/sys/class/hidraw/hidraw*"):
            try:
                with open(os.path.join(sysfs, "device", "uevent"),
                          encoding="ascii", errors="ignore") as uevent_file:
                    fields = dict(line.split("=", 1) for line in
                                  uevent_file.read().splitlines()
                                  if "=" in line)
            except OSError:
                continue
            parts = fields.get("HID_ID", "").split(":")
            if len(parts) != 3:
                continue
            try:
                bus, vendor, product = (int(value, 16) for value in parts)
            except ValueError:
                continue
            if (bus != 3 or vendor != 0x3537 or product != 0x100B):
                continue
            hardware = self._sysfs_usb_hardware_id(
                os.path.join(sysfs, "device"))
            if expected_hardware and hardware != expected_hardware:
                continue
            nodes.append((os.path.join("/dev", os.path.basename(sysfs)),
                          fields.get("HID_NAME", "GameSir HID")))
        return nodes

    def _raw_hid_capture_worker(self, expected_hardware):
        """Read the Cyclone 2's real Share-button state from its vendor HID report.

        Its keyboard interface emits PrintScreen as an instantaneous tap, so it
        cannot convey how long Share was held. The 0x12 vendor report carries
        the physical held state; 0x0F/0xF2 is its read-only input-stream keepalive.
        """
        last_error_log = 0.0
        last_missing_log = 0.0
        while not self._raw_hid_capture_stop.is_set():
            connected = False
            nodes = self._raw_hid_capture_nodes(expected_hardware)
            if not nodes and time.monotonic() - last_missing_log >= 10:
                _log("GameSir capture HID node not found for the connected controller")
                last_missing_log = time.monotonic()
            for path, name in nodes:
                if self._raw_hid_capture_stop.is_set():
                    return
                fd = None
                try:
                    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
                    _log(f"Opening GameSir capture HID: {name} ({path})")
                    probe_until = time.monotonic() + 1.5
                    last_heartbeat = 0.0
                    live = False
                    while (time.monotonic() < probe_until and
                           not self._raw_hid_capture_stop.is_set()):
                        now = time.monotonic()
                        if now - last_heartbeat >= 0.4:
                            os.write(fd, bytes([0x0F, 0xF2]) + bytes(62))
                            last_heartbeat = now
                        readable, _, _ = select.select([fd], [], [], 0.05)
                        if not readable:
                            continue
                        report = os.read(fd, 128)
                        if (len(report) >= 61 and report[0] == 0x12 and
                                any(report[1:5]) or
                                (len(report) >= 61 and report[0] == 0x12 and
                                 report[36] != 0)):
                            live = True
                            break
                    if not live:
                        os.close(fd)
                        fd = None
                        continue

                    connected = True
                    GLib.idle_add(self._set_raw_hid_capture_active, True, path)
                    _log(f"GameSir Share held-state stream active: {path}")
                    last_heartbeat = 0.0
                    while not self._raw_hid_capture_stop.is_set():
                        now = time.monotonic()
                        if now - last_heartbeat >= 0.4:
                            os.write(fd, bytes([0x0F, 0xF2]) + bytes(62))
                            last_heartbeat = now
                        readable, _, _ = select.select([fd], [], [], 0.05)
                        if not readable:
                            continue
                        report = os.read(fd, 128)
                        if len(report) < 61 or report[0] != 0x12:
                            continue
                        pressed = bool(report[60] & 0x02)
                        if pressed != self._raw_hid_capture_button_down:
                            self._raw_hid_capture_button_down = pressed
                            GLib.idle_add(
                                self._handle_capture_button,
                                ecodes.KEY_RECORD,
                                1 if pressed else 0,
                                f"hidraw:{path}")
                    break
                except (OSError, ValueError) as exc:
                    if time.monotonic() - last_error_log >= 10:
                        _log(f"GameSir capture HID unavailable ({path}): {exc}")
                        last_error_log = time.monotonic()
                finally:
                    if fd is not None:
                        try:
                            os.close(fd)
                        except OSError:
                            pass
            if connected or not self._raw_hid_capture_stop.is_set():
                GLib.idle_add(self._set_raw_hid_capture_active, False, "")
            if not self._raw_hid_capture_stop.wait(1.0):
                continue

    def _set_raw_hid_capture_active(self, active, path):
        self._raw_hid_capture_active = active
        if not active:
            self._raw_hid_capture_button_down = False
        return False

    def _on_pad_readable(self, fd, condition):
        dev = next((item for item in self._pad_devices if item.fd == fd), None)
        if dev is None:
            return False
        try:
            for event in dev.read():
                self._handle_pad_event(event)
        except BlockingIOError:
            pass
        except Exception as e:
            _log(f"Pad read error: {e}")
            GLib.idle_add(self._on_controller_disconnected)
            return False
        return True

    def _on_capture_readable(self, fd, condition):
        dev = next((item for item in self._capture_devices if item.fd == fd), None)
        if dev is None:
            return False
        try:
            for event in dev.read():
                if self._raw_hid_capture_active:
                    continue
                if (event.type == ecodes.EV_KEY and
                        event.code in self._capture_device_codes.get(fd, set())):
                    self._handle_capture_button(
                        event.code, event.value, dev.path)
        except BlockingIOError:
            pass
        except Exception as exc:
            _log(f"Capture-button input read failed ({dev.path}): {exc}")
            return False
        return True

    def _handle_pad_event(self, event):
        if event.type == ecodes.EV_KEY:
            self._handle_pad_button(event.code, event.value)
        elif event.type == ecodes.EV_ABS:
            self._handle_pad_abs(event.code, event.value)

    def _dismiss_toast(self):
        if hasattr(self, '_toast_label') and self._toast_label.get_visible():
            self._hide_toast()
        if hasattr(self, '_toast_timeout') and self._toast_timeout:
            try:
                GLib.source_remove(self._toast_timeout)
            except Exception:
                pass
            self._toast_timeout = 0

    def _handle_pad_button(self, code, value):
        # Capture remains a global action even while the game owns the rest
        # of the controller or the startup animation is still playing.
        if code in self._capture_button_codes:
            self._handle_capture_button(
                code, value, self._pad_dev.path if self._pad_dev else "gamepad")
            return
        if not self._startup_hidden:
            return
        if code == ecodes.BTN_MODE:
            if value == 1:
                self._guide_control_center_pending = True
                _log("Guide pressed; waiting for release before Super+Shift+Space dispatch")
            elif value == 0 and self._guide_control_center_pending:
                self._guide_control_center_pending = False
                GLib.idle_add(self._dispatch_control_center_shortcut)
            return
        # While an external game owns focus, its input stream is passed through
        # untouched. The DE keeps only the Guide button as a global action.
        owner = self._controller_owner
        if owner == "game" and not self.control_center_open:
            if self._game_input_probe_remaining > 0:
                self._game_input_probe_remaining -= 1
                _log(f"Post-resume controller probe: EV_KEY code={code}, "
                     f"value={value}; DE observes the ungrabbed device")
            if (value == 1 and self._game_session_active and
                    self._launch_black_screen is not None):
                _log(f"In-game controller input observed (code={code}); removing launch cover")
                self._hide_launch_black_screen()
            return
        dpad_names = (
            ("BTN_DPAD_UP", self._nav_up),
            ("BTN_DPAD_DOWN", self._nav_down),
            ("BTN_DPAD_LEFT", self._nav_left),
            ("BTN_DPAD_RIGHT", self._nav_right),
            ("KEY_UP", self._nav_up),
            ("KEY_DOWN", self._nav_down),
            ("KEY_LEFT", self._nav_left),
            ("KEY_RIGHT", self._nav_right),
        )
        for button_name, navigate in dpad_names:
            if code == getattr(ecodes, button_name, None):
                if value == 1:
                    self._dismiss_toast()
                    navigate()
                return
        if self.control_center_open:
            if value == 1:
                if code == ecodes.BTN_EAST:
                    self._nav_back()
                elif code == ecodes.BTN_SOUTH:
                    self._nav_activate()
            return
        # Select/View has no DE action and must never be repurposed as capture.
        if code == ecodes.BTN_SELECT:
            return
        # Other controls only act on press (value=1), not release (value=0).
        if value != 1:
            return
        self._dismiss_toast()

        # A button (BTN_SOUTH / BTN_A) = activate
        if code == ecodes.BTN_SOUTH:
            _log("Pad: A pressed")
            self._nav_activate()

        # B button (BTN_EAST) = back / undo previous input
        elif code == ecodes.BTN_EAST:
            _log("Pad: B pressed -> back")
            self._nav_back()

        # Start (BTN_START) = home / reset to games tab
        elif code == ecodes.BTN_START:
            _log("Pad: Start pressed -> home")
            self._set_tab(0)

        # L1 (BTN_TL) = previous tab
        elif code == ecodes.BTN_TL:
            _log("Pad: L1 pressed")
            self._nav_prev_tab()

        # R1 (BTN_TR) = next tab
        elif code == ecodes.BTN_TR:
            _log("Pad: R1 pressed")
            self._nav_next_tab()
        else:
            _log(f"Pad: unmapped button event code={code}")

    def _handle_capture_button(self, code, value, source="unknown"):
        if value == 1 and self._share_press_timer is None:
            self._share_long_press_fired = False
            self._share_press_started = time.monotonic()
            self._share_press_timer = GLib.timeout_add(
                700, self._on_share_long_press)
            _log(f"Capture button pressed (code={code}, source={source})")
        elif value == 0:
            if self._share_press_timer is not None:
                GLib.source_remove(self._share_press_timer)
                self._share_press_timer = None
                held_for = (time.monotonic() - self._share_press_started
                            if self._share_press_started is not None else 0)
                _log(f"Capture button released after {held_for:.2f}s "
                     f"(code={code}, source={source})")
                self._share_press_started = None
                if not self._share_long_press_fired:
                    self._dismiss_toast()
                    GLib.idle_add(self._do_screenshot)
            elif self._share_long_press_fired:
                self._share_long_press_fired = False
                self._share_press_started = None

    def _handle_pad_abs(self, code, value):
        if not self._startup_hidden:
            return
        if self._controller_owner == "game" and not self.control_center_open:
            if self._game_input_probe_remaining > 0:
                self._game_input_probe_remaining -= 1
                _log(f"Post-resume controller probe: EV_ABS code={code}, "
                     f"value={value}; DE observes the ungrabbed device")
            return
        # D-pad (ABS_HAT0X / ABS_HAT0Y)
        self._dismiss_toast()
        if code == ecodes.ABS_HAT0X:
            new_x = 1 if value > 0 else (-1 if value < 0 else 0)
            if new_x != self._hat_state[0]:
                if new_x > 0:
                    _log("Pad: D-pad Right")
                    self._nav_right()
                elif new_x < 0:
                    _log("Pad: D-pad Left")
                    self._nav_left()
                self._hat_state = (new_x, self._hat_state[1])
        elif code == ecodes.ABS_HAT0Y:
            new_y = 1 if value > 0 else (-1 if value < 0 else 0)
            if new_y != self._hat_state[1]:
                if new_y > 0:
                    _log("Pad: D-pad Down")
                    self._nav_down()
                elif new_y < 0:
                    _log("Pad: D-pad Up")
                    self._nav_up()
                self._hat_state = (self._hat_state[0], new_y)

    def _do_screenshot(self):
        path = take_screenshot()
        if path:
            self._play_sfx("PS5_Select")
            self._show_toast("Screenshot saved")
        else:
            self._show_toast("Screenshot failed")
        return False

    def _on_share_long_press(self):
        self._share_press_timer = None
        self._share_long_press_fired = True
        self._share_press_started = None
        _log("Capture button long-press threshold reached -> toggle recording")
        self._toggle_screen_recording()
        return False

    def _toggle_screen_recording(self):
        if self._screen_record_action_busy:
            return
        self._screen_record_action_busy = True
        self._show_toast("Starting screen recording" if
                         not self._screen_recording_active else
                         "Stopping screen recording")
        threading.Thread(
            target=self._screen_recording_worker,
            name="screen-record-toggle", daemon=True).start()

    def _screen_recording_worker(self):
        try:
            if self._screen_recording_active:
                proc = self._screen_record_proc
                if proc is None or proc.poll() is not None:
                    raise RuntimeError("The recorder is no longer running")
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=12)
                except subprocess.TimeoutExpired:
                    proc.terminate()
                    proc.wait(timeout=3)
                    raise RuntimeError("The recorder did not stop cleanly")
                self._screen_record_proc = None
                action = "stopped"
            else:
                recorder = shutil.which("wf-recorder")
                if not recorder:
                    raise RuntimeError(
                        "wf-recorder is missing; install it with pacman -S wf-recorder")
                videos_dir = os.path.join(HOME, "Videos")
                try:
                    videos_dir = subprocess.run(
                        ["xdg-user-dir", "VIDEOS"], capture_output=True,
                        text=True, timeout=3, check=True).stdout.strip() or videos_dir
                except (OSError, subprocess.SubprocessError):
                    pass
                os.makedirs(videos_dir, exist_ok=True)
                try:
                    default_sink = subprocess.run(
                        ["pactl", "get-default-sink"], capture_output=True,
                        text=True, timeout=3, check=True).stdout.strip()
                    source_list = subprocess.run(
                        ["pactl", "list", "short", "sources"],
                        capture_output=True, text=True, timeout=3,
                        check=True).stdout
                except (OSError, subprocess.SubprocessError) as exc:
                    raise RuntimeError(
                        "Could not find the system audio output") from exc
                audio_source = f"{default_sink}.monitor"
                source_names = {
                    fields[1] for line in source_list.splitlines()
                    if len(fields := line.split()) >= 2
                }
                if not default_sink or audio_source not in source_names:
                    raise RuntimeError(
                        "Could not find a monitor for the system audio output")
                filename = os.path.join(
                    videos_dir,
                    f"ConsoleDE_{time.strftime('%Y-%m-%d_%H-%M-%S')}.mp4")
                recorder_log = os.path.expanduser(
                    "~/.cache/console-de-wf-recorder.log")
                with open(recorder_log, "w", encoding="utf-8") as log_file:
                    proc = subprocess.Popen(
                        [recorder, f"--audio={audio_source}",
                         "--file", filename],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=log_file,
                        start_new_session=True)
                time.sleep(0.5)
                if proc.poll() is not None:
                    try:
                        with open(recorder_log, "r", encoding="utf-8") as log_file:
                            details = log_file.read().strip().splitlines()[-1:]
                    except OSError:
                        details = []
                    raise RuntimeError(
                        details[0][:100] if details else
                        "wf-recorder exited before recording started")
                self._screen_record_proc = proc
                self._screen_record_path = filename
                action = "started"

            GLib.idle_add(self._finish_screen_recording_action, action, None)
        except Exception as exc:
            _log(f"Screen recording failed: {exc}")
            GLib.idle_add(
                self._finish_screen_recording_action, None, str(exc))

    def _finish_screen_recording_action(self, action, error):
        self._screen_record_action_busy = False
        if error:
            self._show_toast(f"Recording: {error[:70]}")
        elif action == "started":
            self._screen_recording_active = True
            self._show_toast("Recording started")
        else:
            self._screen_recording_active = False
            self._show_toast("Recording saved to Videos")
        return False

    def _show_toast(self, msg):
        """Show a temporary message overlay."""
        if not hasattr(self, '_toast_label'):
            self._toast_label = Gtk.Label(label="")
            self._toast_label.override_background_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(0.0, 0.0, 0.0, 0.7))
            self._toast_label.override_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(1, 1, 1, 1))
            self._toast_label.set_halign(Gtk.Align.CENTER)
            self._toast_label.set_valign(Gtk.Align.END)
            self._toast_label.set_margin_bottom(self._sh_pct(4))
            # Add as overlay child so it floats on top
            self._overlay.add_overlay(self._toast_label)
            self._toast_label.show()

        self._toast_label.set_text(f"  {msg}  ")
        self._toast_label.show()
        if hasattr(self, '_toast_timeout') and self._toast_timeout:
            GLib.source_remove(self._toast_timeout)
        self._toast_timeout = GLib.timeout_add(1000, self._hide_toast)

    def _hide_toast(self):
        if hasattr(self, '_toast_label'):
            self._toast_label.hide()
        return False

    def _refresh_selection(self, prev=None):
        """Update game selection. prev = previous gsel (triggers carousel anim)."""
        _log(f"Selected: {self.games[self.gsel]['name']}")
        self._update_game_info()
        self._update_play_button_label()
        if prev is not None and prev != self.gsel:
            self._start_carousel_anim(prev)
        elif prev is None:
            # Selection was set directly (list reordered / reset): show the resting state
            self._cancel_carousel_anim()
            if hasattr(self, 'carousel'):
                self.carousel.queue_draw()

    def _is_background_resume_selection(self):
        if (not self._user_requested_de_foreground or
                not self.games):
            return False
        selected_appid = str(self.games[self.gsel].get("appid") or "")
        record = self._opened_game_record(selected_appid)
        if record is not None:
            return self._opened_application_is_running(record)
        return (selected_appid == str(self._active_game_appid or "") and
                self._is_launched_game_running())

    def _update_play_button_label(self):
        if hasattr(self, "play_btn"):
            self.play_btn.set_text(
                "Resume" if self._is_background_resume_selection() else "Play")

    def _cancel_carousel_anim(self):
        tid = getattr(self, '_car_anim_id', None)
        if tid is not None:
            try:
                self.carousel.remove_tick_callback(tid)
            except Exception:
                pass
        self._car_anim_id = None
        self._car_animating = False

    def _car_grow_at(self, i, target_sel):
        """How 'selected' tile i currently looks: 0 = normal size, 1 = fully selected.
        At rest only target_sel is 1. While animating, every tile eases from the
        growth it had when the animation started toward its new value."""
        tgt = 1.0 if i == target_sel else 0.0
        if not self._car_animating:
            return tgt
        frm = self._car_g_from[i] if i < len(self._car_g_from) else 0.0
        return frm + (tgt - frm) * self._car_progress

    def _carousel_offset_for_selection(self, index):
        """Return the belt offset for a selected tile.

        Selection 0: first tile rests at the old second-tile position.
        Selection 1: first tile slides back to the normal left inset.
        Selection 2: first tile is mostly off-screen (about 30% remains).
        Later selections continue scrolling at the normal tile stride.
        """
        if index <= 0:
            return 0.0
        if index == 1:
            return float(self._car_sel_size + self._car_gap)

        third_selection_offset = self._car_first + self._car_tile * 0.70
        if index == 2:
            return float(third_selection_offset)
        return float(third_selection_offset + (index - 2) * self._car_stride)

    def _start_carousel_anim(self, prev):
        """Start the belt morph: the strip slides while the old and new selected tiles
        shrink and grow, all on one clock. Rapid presses retarget from what is on screen."""
        n = len(self.games)

        # Capture the visual state before touching any animation state
        if self._car_animating:
            offset_from = self._car_offset_current
        else:
            offset_from = self._carousel_offset_for_selection(prev)
        grow_from = [self._car_grow_at(i, prev) for i in range(n)]
        self._cancel_carousel_anim()

        self._car_g_from = grow_from
        self._car_sel_from = prev
        self._car_offset_from = offset_from
        self._car_offset_to = self._carousel_offset_for_selection(self.gsel)
        self._car_offset_current = offset_from
        self._car_progress = 0.0
        self._car_start = time.monotonic()
        self._car_duration = 0.26

        if not self.carousel.get_mapped():
            # Not on screen (e.g. another tab is showing): nothing to animate
            self.carousel.queue_draw()
            return
        self._car_animating = True
        # Frame-clock tick: runs once per display frame, so motion stays smooth
        self._car_anim_id = self.carousel.add_tick_callback(self._on_carousel_tick)
        self.carousel.queue_draw()

    def _on_carousel_tick(self, widget, frame_clock):
        return GLib.SOURCE_CONTINUE if self._on_carousel_anim() else GLib.SOURCE_REMOVE

    def _on_carousel_anim(self):
        """Advance the animation one step. Returns True while it should keep running."""
        progress = min(1.0, (time.monotonic() - self._car_start) / self._car_duration)
        eased = _cubic_bezier_y(progress)
        self._car_progress = eased
        self._car_offset_current = (
            self._car_offset_from +
            (self._car_offset_to - self._car_offset_from) * eased)
        if progress >= 1.0:
            self._car_anim_id = None       # the tick callback removes itself
            self._car_animating = False    # back to the resting state derived from gsel
            self.carousel.queue_draw()
            return False
        self.carousel.queue_draw()
        return True

    def _on_carousel_draw(self, widget, cr):
        """Draw the PS5 tile belt conveyor using Cairo."""
        if not HAVE_CAIRO or not self.games:
            return False
        vp_w = widget.get_allocated_width()
        vp_h = widget.get_allocated_height()
        if vp_w <= 0 or vp_h <= 0:
            return False

        n = len(self.games)
        gsel = self.gsel
        tile = self._car_tile
        gap = self._car_gap
        sel_size = self._car_sel_size
        stride = self._car_stride
        ring_m = self._car_ring_margin
        ring_off = self._car_ring_off
        radius = self._sw_pct(1.2)  # rounded corners
        intro_elapsed = (
            max(0.0, time.monotonic() - self._carousel_intro_start)
            if self._carousel_intro_active else 0.0)

        # Use the same selection-to-offset mapping as the animation target.
        # This keeps the first tile at the intended positions: initial inset,
        # near the edge on selection 2, then ~30% visible on selection 3.
        if self._car_animating:
            belt_px = self._car_offset_current
        else:
            belt_px = self._carousel_offset_for_selection(gsel)

        # All tiles share one fixed top edge; only their widths morph.
        x_cursor = self._car_first - belt_px
        for i in range(n):
            grow = self._car_grow_at(i, gsel)
            target_w = tile + (sel_size - tile) * grow
            x = x_cursor
            x_cursor += target_w + gap
            intro_scale = 1.0
            if self._carousel_intro_active:
                # Stagger from first to last tile while keeping the whole
                # entrance bounded to the same two-second interval.
                lag = min(i * 0.10, self._carousel_intro_duration * 0.4)
                local_duration = self._carousel_intro_duration - lag
                local_progress = max(0.0, min(
                    1.0, (intro_elapsed - lag) / local_duration))
                eased_intro = 1.0 - (1.0 - local_progress) ** 3
                intro_scale = 0.18 + 0.82 * eased_intro
                start_x = vp_w + target_w
                x = x + (start_x - x) * (1.0 - eased_intro)
            w = target_w * intro_scale
            h = w                       # tiles are square
            # The selection outline extends beyond the tile. Offset the
            # selected art by the ring margin so the outer ring aligns with
            # the top edge of the unselected tiles, without moving the belt.
            y = ring_m * grow
            ring_a = (0.0 if self.tab_bar_focused
                      or self.play_btn_focused or self.more_btn_focused
                      else grow * intro_scale)

            if x > vp_w + 10:
                break                   # everything further right is off-screen too
            if x + w < -10:
                continue

            game = self.games[i]
            pb = self._carousel_pixbufs.get(i)

            # Clip to rounded rect
            cr.save()
            cr.new_sub_path()
            cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
            cr.arc(x + w - radius, y + radius, radius, 3 * math.pi / 2, 2 * math.pi)
            cr.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
            cr.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
            cr.close_path()
            cr.clip()

            # Draw art scaled to the tile (moved to the tile's position first)
            if pb:
                cr.save()
                cr.translate(x, y)
                cr.scale(w / pb.get_width(), h / pb.get_height())
                Gdk.cairo_set_source_pixbuf(cr, pb, 0, 0)
                cr.paint()
                cr.restore()
            else:
                cr.set_source_rgba(0.11, 0.13, 0.16, 1.0)
                cr.rectangle(x, y, w, h)
                cr.fill()

            cr.restore()

            # Animated outline sits just outside the selected tile edge.
            if ring_a > 0.01:
                off = ring_off
                self._paint_orbit_border(
                    cr, x - off, y - off, w + 2 * off, h + 2 * off,
                    radius, max(self._car_ring_w, self._font_px(1)), ring_a)

            # Selected tile name under it
            if ring_a > 0.5:
                cr.save()
                cr.select_font_face("SF Compact Display", cairo.FONT_SLANT_NORMAL,
                                    cairo.FONT_WEIGHT_NORMAL)
                cr.set_font_size(self._sw_pct(1.0))
                cr.set_source_rgba(1.0, 1.0, 1.0, ring_a)
                text = game.get('name', '')[:24]
                xb, yb, tw, th, dx, dy = cr.text_extents(text)
                cr.move_to(x + w / 2 - tw / 2, y + h + self._sw_pct(1.8))
                cr.show_text(text)
                cr.restore()

        return False

    def _load_carousel_pixbufs(self):
        """Decode game art off the GTK thread, then publish it in one UI update."""
        self._carousel_load_generation = getattr(
            self, "_carousel_load_generation", 0) + 1
        generation = self._carousel_load_generation
        games = [(i, dict(game)) for i, game in enumerate(self.games)]
        tile_size = self._car_sel_size

        def load_art():
            loaded = {}
            for index, game in games:
                if not game.get("steam"):
                    continue
                art = _find_tile_art(game.get("appid"))
                if not art:
                    continue
                try:
                    pixbuf = _cover_pixbuf(art, tile_size, tile_size)
                    if pixbuf is not None:
                        loaded[game.get("appid")] = pixbuf
                except Exception as exc:
                    _log(f"Carousel art decode failed for {game.get('appid')}: {exc}")
            GLib.idle_add(self._publish_carousel_pixbufs, generation, games, loaded)

        threading.Thread(target=load_art, name="carousel-art-loader",
                         daemon=True).start()
        return False

    def _publish_carousel_pixbufs(self, generation, games, loaded):
        if generation != self._carousel_load_generation:
            return False
        self._carousel_pixbufs = {
            index: loaded[game.get("appid")]
            for index, game in enumerate(self.games)
            if game.get("appid") in loaded
        }
        if hasattr(self, "carousel"):
            self.carousel.queue_draw()
        _log(f"Carousel art loaded off-thread: {len(self._carousel_pixbufs)} tiles")
        return False

    def _music_file(self):
        return os.path.expanduser("~/.config/console-de/sfx/PS5_Home_Menu.mp3")

    def _start_music(self):
        """Play PS5_Home_Menu.mp3 looping during startup animation."""
        if self._music_proc is not None:
            return False
        menu = self._music_file()
        if not os.path.isfile(menu):
            _log(f"Music file not found: {menu}")
            return False
        inputs = ["-stream_loop", "-1", "-i", menu]
        graph = ("[0:a]aformat=sample_rates=48000:channel_layouts=stereo[out]")
        ffmpeg_cmd = ("ffmpeg -nostdin -loglevel quiet "
                      "-probesize 32768 -analyzeduration 0 "
                      + " ".join(shlex.quote(a) for a in inputs)
                      + f" -filter_complex {shlex.quote(graph)} -map [out] "
                      + "-f s16le -ar 48000 -ac 2 -")

        def _preexec():
            # Die (and take our process group down) if the shell dies
            try:
                import ctypes
                libc = ctypes.CDLL("libc.so.6", use_errno=True)
                libc.prctl(1, signal.SIGTERM)  # PR_SET_PDEATHSIG
            except Exception:
                pass

        # Own session/process group; trap keeps ffmpeg+ffplay tied to the shell
        cmd = (f'trap "kill -9 -$$" TERM INT HUP; '
               f'{ffmpeg_cmd} | '
               'ffplay -nodisp -loglevel quiet -f s16le -ar 48000 '
               '-ch_layout stereo -i - -volume 25')
        try:
            self._music_proc = subprocess.Popen(
                ["sh", "-c", cmd],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, preexec_fn=_preexec)
            try:
                os.makedirs(os.path.expanduser("~/.cache"), exist_ok=True)
                with open(self._music_pgid_file(), "w", encoding="ascii") as pgid_file:
                    pgid_file.write(f"{self._music_proc.pid}\n")
            except OSError as exc:
                _log(f"Could not save home-music process group: {exc}")
            _log(f"Music pipeline started: {menu}")
        except Exception as e:
            _log(f"Music start failed: {e}")
        return False

    def _create_startup_screen(self, overlay):
        """PS5-inspired login transition with a soft light beam and fine motes."""
        self.startup_screen = StartupAnimation()
        overlay.add_overlay(self.startup_screen)
        self.startup_screen.show_all()
        # Keep the one-shot intro concise while leaving time for the dust to
        # spread, settle, and hold before the home carousel takes over.
        GLib.timeout_add_seconds(6, self._hide_startup_screen)

    def _hide_startup_screen(self):
        """Remove the startup screen overlay once services are ready."""
        if self._startup_hidden:
            return False
        self._startup_hidden = True
        self.main_box.set_sensitive(True)
        self.topbar.set_sensitive(True)
        if hasattr(self, 'startup_screen') and self.startup_screen.get_parent():
            self.startup_screen.stop_animation()
            try:
                self._overlay.remove(self.startup_screen)
            except Exception:
                pass
            _log("Startup screen hidden")
        # Play PS5_Home.mp3 immediately after the startup animation ends
        GLib.timeout_add(100, self._play_home_sound)
        # Start the home screen transition (fade in UI over background)
        GLib.timeout_add(100, self._start_home_transition)
        return False

    def _start_home_transition(self):
        """Show the content and start the one-time carousel entrance."""
        if self._carousel_intro_started:
            return False
        self._carousel_intro_started = True
        self.main_box.set_opacity(1.0)
        self.topbar.set_opacity(0.0)
        self._carousel_intro_start = time.monotonic()
        self._carousel_intro_active = True
        self.carousel.queue_draw()
        self._carousel_intro_id = self.carousel.add_tick_callback(
            self._on_carousel_intro_animate)
        return False

    def _on_carousel_intro_animate(self, widget, _frame_clock, _user_data=None):
        elapsed = time.monotonic() - self._carousel_intro_start
        progress = min(1.0, elapsed / self._carousel_intro_duration)
        widget.queue_draw()
        if progress >= 1.0:
            self._carousel_intro_active = False
            self._carousel_intro_id = None
            widget.queue_draw()
            self._start_topbar_fade()
            return False
        return True

    def _start_topbar_fade(self):
        self._topbar_fade_start = time.monotonic()
        self._topbar_fade_active = True
        self.topbar.set_opacity(0.0)
        self._topbar_fade_id = GLib.timeout_add(
            _ANIMATION_INTERVAL_MS, self._on_topbar_fade_animate)

    def _on_topbar_fade_animate(self):
        elapsed = time.monotonic() - self._topbar_fade_start
        progress = min(1.0, elapsed / self._topbar_fade_duration)
        eased = _cubic_bezier_y(progress)
        self.topbar.set_opacity(eased)
        if progress >= 1.0:
            self._topbar_fade_active = False
            self._topbar_fade_id = None
            self._show_status_notices(
                show_controller=self._pad_dev is not None,
                show_microphone=True)
            self._start_microphone_state_monitor()
            self._info_fade_delay_id = GLib.timeout_add(
                int(self._info_fade_delay * 1000), self._start_info_box_fade)
            return False
        return True

    def _start_info_box_fade(self):
        self._info_fade_start = time.monotonic()
        self._info_fade_delay_id = None
        self.info_box.set_opacity(0.0)
        self.info_box.set_margin_bottom(
            self._info_box_base_margin_bottom + self._info_box_start_offset)
        self._info_fade_id = GLib.timeout_add(
            _ANIMATION_INTERVAL_MS, self._on_info_box_fade_animate)
        return False

    def _on_info_box_fade_animate(self):
        elapsed = time.monotonic() - self._info_fade_start
        progress = min(1.0, elapsed / self._info_fade_duration)
        eased = _cubic_bezier_y(progress)
        self.info_box.set_opacity(eased)
        margin_bottom = round(
            self._info_box_base_margin_bottom +
            self._info_box_start_offset * (1.0 - eased))
        self.info_box.set_margin_bottom(margin_bottom)
        if progress >= 1.0:
            self._info_fade_id = None
            return False
        return True

    def _play_home_sound(self):
        """Play PS5_Home.mp3 once over the looping menu music."""
        stinger = os.path.expanduser("~/.config/console-de/sfx/PS5_Home.mp3")
        if not os.path.isfile(stinger):
            _log(f"Home sound not found: {stinger}")
            return False
        try:
            cmd = (f'ffmpeg -nostdin -loglevel quiet -i "{stinger}" '
                   f'-filter_complex "volume=4,aformat=sample_rates=48000:channel_layouts=stereo[out]" '
                   f'-map [out] -f s16le -ar 48000 -ac 2 - | '
                   f'ffplay -nodisp -loglevel quiet -f s16le -ar 48000 '
                   f'-ch_layout stereo -i - -volume 100 -autoexit')
            self._home_sound_proc = subprocess.Popen(
                ["sh", "-c", cmd],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            _log("Home sound playing once over menu music")
        except Exception as e:
            _log(f"Home sound failed: {e}")
        return False


    def _move_to_front(self, items, key, value):
        """Move the item whose items[i][key]==value to index 0 of items."""
        for i, item in enumerate(items):
            if item.get(key) == value:
                if i == 0:
                    return False  # already at front
                items.pop(i)
                items.insert(0, item)
                return True
        return False

    def _load_game_order(self):
        """Apply persisted game order (promoted games stay at front across restarts)."""
        order_file = os.path.expanduser("~/.config/console-de/game_order")
        if not os.path.isfile(order_file):
            return
        try:
            with open(order_file, "r") as f:
                saved_appids = [line.strip() for line in f if line.strip()]
        except Exception as e:
            _log(f"Failed to read game order: {e}")
            return
        if not saved_appids:
            return
        # Build a lookup by appid
        by_appid = {g.get('appid'): g for g in self.games}
        ordered = []
        for appid in saved_appids:
            g = by_appid.get(appid)
            if g:
                ordered.append(g)
        # Append any games not in the saved list (new games) at the end
        for g in self.games:
            if g not in ordered:
                ordered.append(g)
        if ordered != self.games:
            self.games = ordered
            _log(f"Restored saved game order ({len(saved_appids)} games)")
            self.gsel = 0

    def _refresh_steam_game_list(self):
        """Rescan Steam manifests away from GTK's drawing thread."""
        self._steam_game_refresh_timer_id = None
        if self._steam_game_scan_in_progress:
            self._steam_game_refresh_timer_id = GLib.timeout_add_seconds(
                5, self._refresh_steam_game_list)
            return False
        self._steam_game_scan_in_progress = True

        def scan_games():
            try:
                discovered = list_steam_games()
                error = None
            except Exception as exc:
                discovered = None
                error = exc
            GLib.idle_add(self._finish_steam_game_refresh, discovered, error)

        threading.Thread(target=scan_games, name="steam-library-scan",
                         daemon=True).start()
        self._steam_game_refresh_timer_id = GLib.timeout_add_seconds(
            30, self._refresh_steam_game_list)
        return False

    def _finish_steam_game_refresh(self, discovered, error):
        self._steam_game_scan_in_progress = False
        if error is not None:
            _log(f"Steam game-list refresh failed: {error}")
            return False
        try:
            selected_appid = (self.games[self.gsel].get("appid")
                              if self.games and 0 <= self.gsel < len(self.games)
                              else None)
            old_games = {game.get("appid"): game.get("name")
                         for game in self.games}
            new_games = {game.get("appid"): game.get("name")
                         for game in discovered}
            if old_games != new_games:
                self.games = discovered
                self._load_game_order()
                if selected_appid is not None:
                    self.gsel = next((i for i, game in enumerate(self.games)
                                      if game.get("appid") == selected_appid),
                                     min(self.gsel, max(0, len(self.games) - 1)))
                elif self.games:
                    self.gsel = min(self.gsel, len(self.games) - 1)
                else:
                    self.gsel = 0
                self._load_carousel_pixbufs()
                if self.games:
                    self._refresh_selection()
                else:
                    self._update_game_info()
                    self._update_play_button_label()
                if hasattr(self, "carousel"):
                    self.carousel.queue_draw()
                _log(f"Steam game list refreshed: {len(self.games)} games")
        except Exception as exc:
            _log(f"Steam game-list refresh update failed: {exc}")
        return False

    def _save_game_order(self):
        """Persist current game order so promoted games stay at front."""
        order_file = os.path.expanduser("~/.config/console-de/game_order")
        try:
            with open(order_file, "w") as f:
                for g in self.games:
                    aid = g.get('appid')
                    if aid is not None:
                        f.write(f"{aid}\n")
        except Exception as e:
            _log(f"Failed to save game order: {e}")

    def _schedule_promote(self, appid=None, name=None):
        """Schedule promoting a recently launched item to the front after 5s.

        Cancels any pending promotion so only the most recent launch counts.
        """
        if self._promote_timer_id is not None:
            try:
                GLib.source_remove(self._promote_timer_id)
            except Exception:
                pass
        self._promote_appid = appid
        self._promote_name = name
        self._promote_timer_id = GLib.timeout_add_seconds(5, self._promote_launched)

    def _promote_launched(self):
        """A few seconds after launch, move the recently opened item to the front.

        Runs while the user is in the (fullscreen) game/app, so they never see
        the reorder happen — when they return to the DE it's already at the start.
        """
        self._promote_timer_id = None
        changed = False
        if self._promote_appid is not None and self.games:
            changed = self._move_to_front(self.games, 'appid', self._promote_appid)
        if self._promote_name is not None and self.apps:
            changed = self._move_to_front(self.apps, 'name', self._promote_name) or changed
        if changed:
            self.gsel = 0
            self.asel = 0
            self._refresh_selection()
            self._refresh_app_selection()
            self._save_game_order()
            _log("Promoted recently launched item to front")
        self._promote_appid = None
        self._promote_name = None
        return False  # one-shot timer

    def _find_steam_pid(self):
        """Find a running Steam process PID."""
        try:
            result = subprocess.run(
                ["pgrep", "-x", "steam"],
                capture_output=True, text=True, timeout=3)
            if result.returncode == 0 and result.stdout.strip():
                return int(result.stdout.strip().splitlines()[0])
        except Exception:
            pass
        return None

    def _wake_steam_process(self, pid):
        """Resume Steam if an earlier shell version left it stopped."""
        try:
            pgid = os.getpgid(pid)
            if pgid == pid:
                # Older shell code launched Steam in its own session before
                # stopping the process group, so resume that group together.
                os.killpg(pgid, signal.SIGCONT)
            else:
                # Do not signal an unrelated session/process group.
                os.kill(pid, signal.SIGCONT)
            _log(f"Steam process resumed (pid={pid}, pgid={pgid})")
        except ProcessLookupError:
            _log(f"Steam process {pid} exited before resume")
        except Exception as e:
            _log(f"Steam resume failed (pid={pid}): {e}")

    def _ensure_steam_running(self):
        """Start Steam quietly in the background without freezing its client."""
        pid = self._find_steam_pid()
        if pid is None:
            try:
                proc = subprocess.Popen(
                    ["steam", "-silent"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True)
                pid = proc.pid
                # Let Steam finish initialization so later -applaunch requests
                # can reach its client. The DE remains above it during startup.
                _log(f"Steam started quietly in background (pid={pid})")
                # Reassert the DE after the compositor has had time to process
                # any window Steam created while starting.
                GLib.timeout_add(700, self._restore_de_foreground)
                GLib.timeout_add(1800, self._restore_de_foreground)
            except Exception as e:
                _log(f"Steam start failed: {e}")
                return False
        else:
            self._wake_steam_process(pid)

        _log("Steam running in background")
        # This is a one-shot GLib timeout; do not start duplicate checks.
        return False

    def _restore_de_foreground(self):
        """Keep the DE above Steam and restore it after Steam startup."""
        try:
            # Steam's startup callbacks can arrive after Play was pressed. Never
            # let a stale callback raise the shell over a game launch.
            if (self._active_game_appid and
                    not self._user_requested_de_foreground):
                _log("Skipped Steam startup foreground restore during game session")
                return False
            self.set_keep_above(True)
            self.present()
            _log("DE foreground restored after Steam startup")
        except Exception as e:
            _log(f"DE foreground restore failed: {e}")
        return False

    def _opened_game_record(self, appid):
        appid = str(appid or "")
        return next((record for record in self._opened_applications
                     if record.get("kind") == "game" and
                     record.get("appid") == appid), None)

    def _opened_application_is_running(self, record):
        now = time.monotonic()
        if record.get("kind") == "game":
            pids = self._find_game_processes_for(
                record.get("appid"), record.get("name"),
                record.get("pre_pids", set()))
            record["pids"] = pids
            if pids:
                record["ever_seen"] = True
                record["last_seen"] = now
                return True
            if record.get("ever_seen"):
                return now - record.get("last_seen", now) < 5.0
            # Leave time for Steam and Proton to create the actual game process.
            return now - record.get("opened_at", now) < 180.0

        pgid = record.get("pgid")
        if pgid is None:
            process = record.get("process")
            return process is not None and process.poll() is None
        process = record.get("process")
        if process is not None:
            process.poll()  # Reap the launcher wrapper when it has exited.
        try:
            os.killpg(int(pgid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except (OSError, TypeError, ValueError):
            process = record.get("process")
            return process is not None and process.poll() is None

    def _refresh_opened_applications(self):
        live = []
        for record in self._opened_applications:
            if self._opened_application_is_running(record):
                live.append(record)
            else:
                _log(f"Opened app exited: {record.get('name', 'Unknown app')}")
        self._opened_applications = live
        return live

    def _ensure_open_application_slot(self):
        """Keep at most four DE-launched games/apps, evicting oldest first."""
        self._refresh_opened_applications()
        while len(self._opened_applications) >= self._opened_application_limit:
            oldest = self._opened_applications.pop(0)
            self._close_opened_application(oldest)

    def _close_opened_application(self, record):
        """Request a close for the oldest app, then force it only if needed."""
        name = record.get("name", "Unknown app")
        if record.get("kind") == "game":
            pids = self._find_game_processes_for(
                record.get("appid"), name, record.get("pre_pids", set()))
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    _log(f"Could not close oldest game process {pid}: {exc}")
        else:
            pgid = record.get("pgid")
            if pgid is not None:
                try:
                    os.killpg(int(pgid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except (OSError, TypeError, ValueError) as exc:
                    _log(f"Could not close oldest app process group: {exc}")

        _log(f"Closing oldest opened app: {name} "
             f"(kind={record.get('kind')}, appid={record.get('appid', '')})")
        GLib.timeout_add(2000, self._force_close_opened_application, record)
        if (record.get("kind") == "game" and
                str(self._active_game_appid or "") == record.get("appid")):
            self._active_game_appid = None
            self._active_game_name = ""
            self._game_session_active = False
            self._game_handoff_confirmed = False
            self._game_handoff_time = None
            self._game_missing_since = None
            self._launch_pre_procs = set()
            self._user_requested_de_foreground = False
            self._update_play_button_label()
        elif (record.get("kind") == "app" and
              record.get("id") == self._active_external_launch_id):
            self._active_external_app = False
            self._active_external_launch_id = None

    def _force_close_opened_application(self, record):
        """Finish closing an evicted app if it ignored the graceful request."""
        name = record.get("name", "Unknown app")
        if record.get("kind") == "game":
            pids = self._find_game_processes_for(
                record.get("appid"), name, record.get("pre_pids", set()))
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except OSError as exc:
                    _log(f"Could not force-close game process {pid}: {exc}")
        else:
            pgid = record.get("pgid")
            if pgid is not None:
                try:
                    os.killpg(int(pgid), signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except (OSError, TypeError, ValueError) as exc:
                    _log(f"Could not force-close app process group: {exc}")
        _log(f"Evicted app close completed: {name}")
        return False

    def _latest_running_game_record(self):
        self._refresh_opened_applications()
        for record in reversed(self._opened_applications):
            if (record.get("kind") == "game" and
                    self._opened_application_is_running(record)):
                return record
        return None

    def _activate_game_record(self, record):
        """Make one tracked game the current game for existing audio/focus code."""
        self._active_game_appid = str(record.get("appid") or "")
        self._active_game_name = record.get("name", "")
        self._launch_pre_procs = set(record.get("pre_pids", set()))
        record["last_activated"] = time.monotonic()

    def _launch_app(self):
        self._cancel_home_foreground_enforcement()
        app = self.apps[self.asel]
        cmd = re.sub(r'%[fFuUdDnNickvm]', '', app.get('exec', '')).strip()
        if not cmd:
            _log(f"App has no exec: {app['name']}")
            return
        self._ensure_open_application_slot()
        _log(f"Launch app: {app['name']} -> {cmd}")
        # Let the launched app take the fullscreen layer and focus.
        self._set_shell_layer("bottom")
        self.set_keep_above(False)
        # Ungrab gamepad so the launched game can see it
        self._ungrab_gamepad()
        try:
            process = subprocess.Popen(
                cmd, shell=True, env=_gpu_offload_env(),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            self._opened_application_sequence += 1
            record = {
                "id": self._opened_application_sequence,
                "kind": "app", "name": app["name"], "command": cmd,
                "opened_at": time.monotonic(), "process": process,
                "pgid": process.pid,
            }
            self._opened_applications.append(record)
            self._active_external_app = True
            self._active_external_launch_id = record["id"]
            # Background games remain registered and muted, but must not be
            # treated as the foreground audio/input owner while this app runs.
            self._active_game_appid = None
            self._active_game_name = ""
            self._game_session_active = False
            self._user_requested_de_foreground = False
            _log(f"Tracked opened app {record['id']}/"
                 f"{self._opened_application_limit}: {app['name']} "
                 f"(pid={process.pid})")
        except Exception as e:
            _log(f"App launch failed: {e}")
        GLib.timeout_add(4000, self._music_focus_check)
        # Promote recently opened item to front a few seconds after launch
        self._schedule_promote(name=app.get('name'))

    def _play_selected(self):
        self._cancel_home_foreground_enforcement()
        g = self.games[self.gsel]
        if self._is_background_resume_selection():
            appid = str(g.get("appid") or "")
            previous_appid = str(self._active_game_appid or "")
            game_record = self._opened_game_record(appid)
            resume_specific_game = bool(
                game_record is not None and previous_appid != appid)
            _log(f"Resume background game: {g['name']} (appid={appid})")
            if game_record is not None:
                self._activate_game_record(game_record)
                self._game_session_active = True
                self._game_handoff_confirmed = True
                self._game_handoff_time = time.monotonic()
                self._game_missing_since = None
            self._active_external_app = False
            self._active_external_launch_id = None
            if self._background_suspend_timer is not None:
                try:
                    GLib.source_remove(self._background_suspend_timer)
                except Exception:
                    pass
                self._background_suspend_timer = None
            self._user_requested_de_foreground = False
            # Keep DE ownership until the compositor has actually moved focus
            # away. This resumes the existing game window instead of asking
            # Steam to relaunch an app that is already running.
            self._set_controller_owner("de")
            self._control_center_return_to_game = False
            self._pause_music()
            self._update_play_button_label()
            # A mapped layer-shell surface can retain its old layer and
            # keyboard focus in Labwc after set_layer()/set_keyboard_mode().
            # Unmap it first so the background layer and non-interactive
            # keyboard mode are committed when it is mapped again. This lets
            # the already-running game regain focus without a window-switcher
            # detour.
            remap_shell = bool(self._shell_layer_shell and self.get_mapped())
            if remap_shell:
                self.hide()
            self.set_accept_focus(False)
            self._set_shell_layer("bottom")
            self.set_keep_above(False)
            steam_pid = self._find_steam_pid()
            if steam_pid is not None:
                self._wake_steam_process(steam_pid)
            if remap_shell:
                self.show()
                _log("DE layer remapped to background before game resume")
            else:
                # Without layer-shell support, changing keep-above does not
                # reliably return keyboard focus beneath a fullscreen GTK
                # window. Iconify the DE to force the compositor to reactivate
                # the already-running game underneath it.
                self.iconify()
                _log("DE window iconified to return focus to the running game")
            if resume_specific_game:
                # Steam routes an -applaunch request for an already-running
                # AppID to that game's existing instance, allowing selection
                # of an older entry while other games remain open.
                try:
                    subprocess.Popen(
                        ["steam", "-silent", "-applaunch", appid],
                        env=_gpu_offload_env(), stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL, start_new_session=True)
                    _log(f"Requested existing Steam game to foreground: {appid}")
                except Exception as exc:
                    _log(f"Could not foreground selected Steam game {appid}: {exc}")
            else:
                # Preserve the established handoff for the most recently
                # focused game; it avoids the task-switcher flash.
                GLib.timeout_add(120, self._focus_previous_window_after_resume)
                _log("Resume requested; scheduling explicit Labwc game focus")
            if self._game_check_timer_id is None:
                self._game_check_timer_id = GLib.timeout_add(
                    200, self._check_game_launched)
            return

        _log(f"Launch: {g['name']} (appid={g.get('appid')})")
        self._ensure_open_application_slot()
        self._user_requested_de_foreground = False
        self._active_game_appid = str(g.get('appid') or "")
        self._active_game_name = g.get("name", "")
        self._update_play_button_label()
        self._show_launch_black_screen(g)
        self._opened_application_sequence += 1
        game_record = {
            "id": self._opened_application_sequence,
            "kind": "game", "name": self._active_game_name,
            "appid": self._active_game_appid,
            "opened_at": time.monotonic(),
            "pre_pids": set(self._launch_pre_procs),
            "pids": set(), "ever_seen": False,
            "last_seen": time.monotonic(),
        }
        self._opened_applications.append(game_record)
        _log(f"Tracked opened app {game_record['id']}/"
             f"{self._opened_application_limit}: {game_record['name']} "
             f"(appid={game_record['appid']})")
        # The DE stays above Steam while browsing, but must yield the top
        # fullscreen layer so the game window can become visible.
        self._set_shell_layer("bottom")
        self.set_keep_above(False)
        self._ungrab_gamepad()
        # Recover a client that an earlier shell version may have left stopped.
        steam_pid = self._find_steam_pid()
        if steam_pid is not None:
            self._wake_steam_process(steam_pid)
        try:
            subprocess.Popen(
                ["steam", "-silent", "-applaunch", str(g['appid'])],
                env=_gpu_offload_env(),
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _log("Launch command sent")
        except Exception as e:
            _log(f"Launch failed: {e}")
        # Game audio can attach a little after its process starts. Retry
        # against the current stream IDs so launch never inherits a stale mute.
        self._resume_background_audio()
        self._schedule_promote(appid=g.get('appid'))

    def _show_launch_black_screen(self, game=None):
        """Show a compositor-level black curtain while the game takes over."""
        if self._launch_black_screen is not None:
            return
        _log("BLACK SCREEN: shown")
        self._pause_music()
        use_layer_shell = self._shell_layer_shell
        if use_layer_shell:
            cover = Gtk.ApplicationWindow(
                application=self.get_application(), title="Console DE launch cover")
            cover.set_decorated(False)
            cover.set_skip_taskbar_hint(True)
            cover.set_skip_pager_hint(True)
            cover.set_accept_focus(False)
            GtkLayerShell.init_for_window(cover)
            GtkLayerShell.set_namespace(cover, "console-de-launch-cover")
            if self._primary_monitor is not None:
                try:
                    GtkLayerShell.set_monitor(cover, self._primary_monitor)
                except Exception as exc:
                    _log(f"Could not assign launch cover to primary monitor: {exc}")
            GtkLayerShell.set_layer(cover, GtkLayerShell.Layer.OVERLAY)
            GtkLayerShell.set_keyboard_mode(
                cover, GtkLayerShell.KeyboardMode.NONE)
            GtkLayerShell.set_exclusive_zone(cover, -1)
            for edge in (GtkLayerShell.Edge.TOP, GtkLayerShell.Edge.BOTTOM,
                         GtkLayerShell.Edge.LEFT, GtkLayerShell.Edge.RIGHT):
                GtkLayerShell.set_anchor(cover, edge, True)
            black = Gtk.EventBox()
            black.override_background_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(0.0, 0.0, 0.0, 1.0))
            cover.add(black)
            self._launch_black_screen = cover
            cover.show_all()
            _log("BLACK SCREEN: using Wayland overlay layer")
        else:
            # X11 fallback: keep the cover in the shell window when the
            # layer-shell protocol is unavailable.
            black = Gtk.EventBox()
            black.set_hexpand(True)
            black.set_vexpand(True)
            black.override_background_color(
                Gtk.StateFlags.NORMAL, Gdk.RGBA(0.0, 0.0, 0.0, 1.0))
            self._overlay.add_overlay(black)
            self._launch_black_screen = black
            black.show_all()
        self._launch_show_time = time.monotonic()
        self._game_handoff_confirmed = False
        self._game_session_active = False
        self._game_handoff_time = None
        self._game_missing_since = None
        self._launch_game_seen_at = None
        # Store game info for process matching
        self._launch_game_name = game.get("name", "") if game else ""
        self._launch_game_appid = str(game.get("appid", "")) if game else ""
        # Snapshot current processes (backup detection only)
        self._launch_pre_procs = self._get_running_procs()
        # Only hide after both the game exists and the DE is no longer active.
        if self._game_check_timer_id is None:
            self._game_check_timer_id = GLib.timeout_add(
                200, self._check_game_launched)

    def _hide_launch_black_screen(self):
        _log("BLACK SCREEN: hidden")
        self._launch_show_time = None
        if self._launch_black_screen is not None:
            cover = self._launch_black_screen
            self._launch_black_screen = None
            try:
                if cover.get_parent() is self._overlay:
                    self._overlay.remove(cover)
                else:
                    cover.destroy()
            except Exception:
                pass

    def _place_launch_cover_below_game(self):
        """Keep the launch curtain visible but let the fullscreen game cover it."""
        cover = self._launch_black_screen
        if cover is None or not self._shell_layer_shell:
            return
        try:
            GtkLayerShell.set_layer(cover, GtkLayerShell.Layer.BOTTOM)
            GtkLayerShell.set_keyboard_mode(
                cover, GtkLayerShell.KeyboardMode.NONE)
            _log("BLACK SCREEN: moved below game layer")
        except Exception as exc:
            _log(f"BLACK SCREEN: could not lower behind game: {exc}")

    def _get_running_procs(self):
        """Return dict of {pid: cmdline} for all running processes."""
        procs = {}
        try:
            for pid_dir in os.listdir("/proc"):
                if not pid_dir.isdigit():
                    continue
                try:
                    with open(f"/proc/{pid_dir}/cmdline", "r") as f:
                        cmdline = f.read().replace("\x00", " ").strip()
                    if cmdline:
                        procs[pid_dir] = cmdline
                except Exception:
                    pass
        except Exception:
            pass
        return procs

    def _find_game_processes_for(self, appid, game_name, pre_pids=None):
        """Find one game's processes without confusing other Steam titles."""
        appid = str(appid or "")
        title = re.sub(r"[^a-z0-9]+", " ",
                       (game_name or "").lower()).strip()
        pre_pids = {str(pid) for pid in (pre_pids or set())}
        title_tokens = [token for token in title.split() if len(token) > 2]
        helper_names = {
            "steam", "steamwebhelper", "gameoverlayui", "wineserver",
            "wine", "wine64", "wineboot", "services", "winedevice",
            "plugplay", "svchost", "lsass", "explorer", "rpcss",
            "tabtip", "xalia",
            "reaper", "srt-bwrap", "pv-adverb", "pressure-vessel",
            "pressure-vessel-wrap", "bwrap", "python", "python3", "sh",
            "bash", "steam-runtime-launcher-service",
        }
        matches = set()
        try:
            for entry in os.scandir("/proc"):
                if not entry.name.isdigit():
                    continue
                # Steam and its client existed before Play; they cannot be
                # evidence that the selected game is still running.
                if entry.name in pre_pids:
                    continue
                try:
                    with open(os.path.join(entry.path, "cmdline"), "rb") as cmd_file:
                        cmd = cmd_file.read().replace(b"\0", b" ").decode(
                            "utf-8", "ignore").strip().lower()
                    argv = shlex.split(cmd) if cmd else []
                    binary = os.path.basename(argv[0]) if argv else ""
                    binary = binary.removesuffix(".exe")
                    if binary in helper_names:
                        continue
                    normalized = re.sub(r"[^a-z0-9]+", " ", cmd)
                    title_match = bool(title_tokens and any(
                        token in normalized for token in title_tokens))
                    appid_match = False
                    if appid:
                        try:
                            with open(os.path.join(entry.path, "environ"), "rb") as env_file:
                                env = env_file.read().replace(b"\0", b"\n").decode(
                                    "utf-8", "ignore")
                            appid_match = any(
                                line.split("=", 1)[-1] == appid
                                for line in env.splitlines()
                                if line.startswith(("SteamAppId=", "SteamGameId=",
                                                   "STEAM_COMPAT_APP_ID=")))
                        except (OSError, PermissionError):
                            pass
                    if title_match or appid_match:
                        matches.add(int(entry.name))
                except (OSError, PermissionError, ValueError):
                    pass
        except OSError as exc:
            _log(f"Game process check failed: {exc}")
        return matches

    def _find_launched_game_processes(self):
        """Find the current game's processes, excluding Steam's helpers."""
        record = self._opened_game_record(self._active_game_appid)
        pre_pids = (record.get("pre_pids", set()) if record is not None
                    else self._launch_pre_procs)
        return self._find_game_processes_for(
            self._active_game_appid, self._active_game_name, pre_pids)

    def _is_launched_game_running(self):
        """Detect the launched game executable, ignoring persistent Steam helpers."""
        return bool(self._find_launched_game_processes())

    def _check_game_launched(self):
        """Track game startup and exit; remove the cover at confirmed handoff."""
        if (self._launch_black_screen is None and
                not self._game_session_active and
                self._launch_game_seen_at is None):
            self._game_check_timer_id = None
            return False
        game_running = bool(
            self._active_game_appid and self._is_launched_game_running())
        if game_running and self._launch_game_seen_at is None:
            self._launch_game_seen_at = time.monotonic()
            _log("Launched game process detected")
        controller_passed_to_game = (
            game_running and self._gamepad_passthrough_active)
        if controller_passed_to_game and not self._game_handoff_confirmed:
            _log("Game detected with controller passthrough; placing cover below game")
            # Keep the curtain above the shell but below normal fullscreen
            # application windows in the compositor stack.
            self._set_shell_layer("background")
            self._place_launch_cover_below_game()
            self._game_handoff_confirmed = True
            self._game_session_active = True
            self._game_handoff_time = time.monotonic()
            self._game_missing_since = None
        if game_running:
            self._game_missing_since = None
        if (not game_running and
                (self._game_session_active or
                 self._launch_game_seen_at is not None) and
                self._game_exit_confirmed()):
            _log("Launched game exited; clearing launch cover and reclaiming shell input")
            self._game_check_timer_id = None
            self._finish_game_session()
            return False
        if not game_running:
            return True
        return True

    def _game_exit_confirmed(self):
        """Ignore short process gaps while Steam is opening the actual game."""
        now = time.monotonic()
        reference_time = (self._game_handoff_time
                          if self._game_handoff_time is not None
                          else self._launch_game_seen_at)
        if reference_time is not None and now - reference_time < 5.0:
            return False
        if self._game_missing_since is None:
            self._game_missing_since = now
            return False
        return now - self._game_missing_since >= 1.0

    def _set_controller_owner(self, owner):
        """Route controller events only to the current top interaction layer."""
        if owner not in ("de", "control_center", "game"):
            raise ValueError(f"Invalid controller owner: {owner}")
        previous = self._controller_owner
        self._controller_owner = owner
        if previous != owner:
            _log(f"Controller owner: {previous} -> {owner}")

    def _log_controller_device_openers(self):
        """Log same-user processes holding the gamepad event nodes open."""
        paths = {os.path.realpath(dev.path) for dev in self._pad_devices}
        if not paths:
            _log("Controller opener probe: no event devices are registered")
            return
        found = set()
        try:
            proc_entries = os.scandir("/proc")
        except OSError as exc:
            _log(f"Controller opener probe unavailable: {exc}")
            return
        with proc_entries:
            for proc_entry in proc_entries:
                if not proc_entry.name.isdigit():
                    continue
                pid = proc_entry.name
                fd_dir = os.path.join("/proc", pid, "fd")
                try:
                    fds = os.scandir(fd_dir)
                except OSError:
                    continue
                with fds:
                    for fd_entry in fds:
                        try:
                            target = os.path.realpath(
                                os.readlink(fd_entry.path))
                        except OSError:
                            continue
                        if target not in paths:
                            continue
                        try:
                            with open(os.path.join("/proc", pid, "cmdline"),
                                      "rb") as cmd_file:
                                command = cmd_file.read(512).replace(
                                    b"\0", b" ").decode("utf-8", "replace").strip()
                        except OSError:
                            command = "unknown command"
                        item = (pid, target, command or "unknown command")
                        if item not in found:
                            found.add(item)
                            _log(f"Controller device opener: pid={pid}, "
                                 f"device={target}, command={item[2]}")
        if not found:
            _log("Controller opener probe: no readable process handles found")

    def _ungrab_gamepad(self):
        self._set_controller_owner("game")
        self._ungrab_gamepad_devices()

    def _ungrab_gamepad_devices(self):
        """Release evdev grabs so a foreground game can read the controller."""
        self._gamepad_passthrough_active = True
        if self._gamepad_regrab_timer is not None:
            try:
                GLib.source_remove(self._gamepad_regrab_timer)
            except Exception:
                pass
            self._gamepad_regrab_timer = None
        _log("Controller passthrough enabled for launched game")
        for dev in self._pad_devices:
            try:
                dev.ungrab()
                _log(f"Controller interface ungrabbed for game launch: {dev.path}")
            except Exception as e:
                _log(f"Ungrab failed for {dev.path}: {e}")

    def _regrab_gamepad(self, attempt=0):
        """Take controller ownership back, retrying while the game releases it."""
        if attempt == 0 and self._gamepad_regrab_timer is not None:
            try:
                GLib.source_remove(self._gamepad_regrab_timer)
            except Exception:
                pass
        self._gamepad_regrab_timer = None
        if self._controller_owner == "game":
            _log("Controller re-grab cancelled; game owns the top interaction layer")
            return False
        failures = []
        for dev in self._pad_devices:
            try:
                dev.grab()
                _log(f"Controller interface re-grabbed by DE: {dev.path}")
            except Exception as exc:
                failures.append((dev, exc))
                if attempt == 0 or attempt % 10 == 0:
                    _log(f"Re-grab failed for {dev.path}: {exc}")
        if failures:
            self._gamepad_passthrough_active = True
            keep_retrying_for_home = bool(
                self._user_requested_de_foreground and
                self._active_game_appid and self._is_launched_game_running())
            if attempt < 30 or keep_retrying_for_home:
                self._gamepad_regrab_timer = GLib.timeout_add(
                    100 if attempt < 30 else 500,
                    self._regrab_gamepad, attempt + 1)
            else:
                _log("Controller reclaim still failing after 3 seconds; hotplug scan will retry")
            return False
        self._gamepad_passthrough_active = False
        return False

    def _on_destroy(self, w):
        self._raw_hid_capture_stop.set()
        intro_tick = getattr(self, "_carousel_intro_id", None)
        if intro_tick is not None:
            try:
                self.carousel.remove_tick_callback(intro_tick)
            except Exception:
                pass
            self._carousel_intro_id = None
        for tick_attr in ("_orbit_timer_id", "_app_animation_source"):
            tick_id = getattr(self, tick_attr, None)
            if tick_id is not None:
                try:
                    self.remove_tick_callback(tick_id)
                except Exception:
                    pass
                setattr(self, tick_attr, None)
        _log("=== Session ended (Ctrl+Alt+Del or logout) ===")
        _log("ShellWindow destroyed")
        if self._background_game_suspended:
            self._resume_background_game()
        for attr in ("_topbar_fade_id",
                     "_info_fade_delay_id", "_info_fade_id",
                     "_share_press_timer", "_gamepad_monitor_id",
                     "_gamepad_regrab_timer",
                     "_game_check_timer_id", "_background_audio_resume_timer",
                     "_app_page_build_source",
                     "_home_foreground_timer",
                     "_background_suspend_timer",
                     "_cursor_hide_timer", "_focus_restore_timer",
                     "_microphone_monitor_id", "_settings_stats_timer_id",
                     "_process_log_timer_id", "_steam_game_refresh_timer_id",
                     "_status_notice_fade_id", "_status_notice_dismiss_id"):
            timer_id = getattr(self, attr, None)
            if timer_id is not None:
                try:
                    GLib.source_remove(timer_id)
                except Exception:
                    pass
                setattr(self, attr, None)
        self._stop_music()
        if self._screen_record_proc and self._screen_record_proc.poll() is None:
            try:
                self._screen_record_proc.send_signal(signal.SIGINT)
                self._screen_record_proc.wait(timeout=8)
            except Exception as e:
                _log(f"Recording shutdown failed: {e}")
                try:
                    self._screen_record_proc.terminate()
                except Exception:
                    pass
            self._screen_record_proc = None
            self._screen_recording_active = False
        for watch_id in self._pad_watch_ids:
            try:
                GLib.source_remove(watch_id)
            except Exception:
                pass
        for watch_id in self._capture_watch_ids:
            try:
                GLib.source_remove(watch_id)
            except Exception:
                pass
        if self._pad_dev:
            try:
                self._pad_dev.ungrab()
            except Exception:
                pass
        for dev in self._pad_devices:
            try:
                dev.close()
            except Exception:
                pass
        for dev in self._capture_devices:
            try:
                dev.close()
            except Exception:
                pass
        Gtk.main_quit()

    def _pause_music(self):
        """Pause music (used when a game/app takes focus)."""
        if self._music_proc is None or self._music_paused:
            return
        if self._music_proc.poll() is not None:
            return
        try:
            os.killpg(self._music_proc.pid, signal.SIGSTOP)
            self._music_paused = True
            _log("Music paused")
        except Exception as e:
            _log(f"Music pause failed: {e}")

    def _resume_music(self):
        """Resume music when focus returns to the DE."""
        if self._music_proc is None or not self._music_paused:
            return
        if self._music_proc.poll() is not None:
            self._music_paused = False
            return
        try:
            os.killpg(self._music_proc.pid, signal.SIGCONT)
            self._music_paused = False
            _log("Music resumed")
        except Exception as e:
            _log(f"Music resume failed: {e}")

    def _music_focus_check(self):
        """Resume if the DE still has focus 4s after a launch (launch failed/stayed)."""
        if self.is_active() and not self._active_game_appid:
            self._resume_music()
        return False

    def _finish_game_session(self):
        """Restore shell ownership after the game process exits."""
        finished_appid = str(self._active_game_appid or "")
        # The launch cover is a separate Wayland surface and must be destroyed
        # explicitly when returning to the DE.
        self._hide_launch_black_screen()
        self._resume_background_audio()
        if self._background_audio_resume_timer is not None:
            try:
                GLib.source_remove(self._background_audio_resume_timer)
            except Exception:
                pass
            self._background_audio_resume_timer = None
        self._background_audio_resume_attempts = 0
        self._cancel_home_foreground_enforcement()
        if self._background_suspend_timer is not None:
            try:
                GLib.source_remove(self._background_suspend_timer)
            except Exception:
                pass
            self._background_suspend_timer = None
        self._background_game_suspended = False
        self._background_game_pids.clear()
        self._game_handoff_confirmed = False
        if finished_appid:
            self._opened_applications = [
                record for record in self._opened_applications
                if not (record.get("kind") == "game" and
                        record.get("appid") == finished_appid)]
        self._active_game_appid = None
        self._active_game_name = ""
        self._active_external_app = False
        self._active_external_launch_id = None
        self._user_requested_de_foreground = False
        self._game_session_active = False
        self._game_handoff_confirmed = False
        self._game_handoff_time = None
        self._game_missing_since = None
        self._launch_game_seen_at = None
        self._launch_pre_procs = set()
        self._control_center_return_to_game = False
        next_game = self._latest_running_game_record()
        if next_game is not None:
            self._activate_game_record(next_game)
            self._user_requested_de_foreground = True
            self._game_session_active = True
            self._game_handoff_confirmed = True
            self._game_handoff_time = time.monotonic()
            self._game_missing_since = None
            if self._game_check_timer_id is None:
                self._game_check_timer_id = GLib.timeout_add(
                    200, self._check_game_launched)
            _log(f"Game exited; {next_game['name']} remains open in background")
        self._set_controller_owner(
            "control_center" if self.control_center_open else "de")
        self._regrab_gamepad()
        self.set_accept_focus(True)
        # The shell was demoted while the game ran. Restore it above normal
        # application windows and give it exclusive keyboard ownership.
        self._set_shell_layer("overlay")
        self.set_keep_above(True)
        self.deiconify()
        if not self.control_center_open:
            self._schedule_home_foreground()
        self._update_play_button_label()
        if self.is_active():
            self._resume_music()

    def _stop_music(self):
        if self._music_proc is None:
            return
        pid = self._music_proc.pid
        try:
            os.killpg(pid, signal.SIGCONT)  # unstick if paused
        except Exception:
            pass
        try:
            # kill the WHOLE group (sh wrapper + ffplay), not just the wrapper
            os.killpg(pid, signal.SIGTERM)
            self._music_proc.wait(timeout=2)
        except Exception:
            try:
                os.killpg(pid, signal.SIGKILL)
            except Exception:
                pass
            try:
                self._music_proc.wait(timeout=1)
            except Exception:
                pass
        self._music_proc = None
        self._music_paused = False
        try:
            os.unlink(self._music_pgid_file())
        except OSError:
            pass
        _log("Music stopped")

    @staticmethod
    def _music_pgid_file():
        return os.path.expanduser("~/.cache/console-de-home-music.pgid")

    def _on_focus_out(self, widget, event):
        """DE lost focus; keep the launch curtain until the game is ready."""
        _log("Focus: lost")
        if self.control_center_open:
            self._set_controller_owner("control_center")
        elif (self._user_requested_de_foreground and
              self._active_game_appid and self._is_launched_game_running()):
            self._set_controller_owner("de")
            if self._home_foreground_timer is None:
                self._schedule_home_foreground()
        elif (not self._user_requested_de_foreground and
              (self._active_game_appid or
               (self._gamepad_passthrough_active and self._game_session_active))):
            self._ungrab_gamepad()
            if self._active_game_appid:
                self._resume_background_audio()
        self._focus_out_time = time.monotonic()
        if self._focus_restore_timer is not None:
            try:
                GLib.source_remove(self._focus_restore_timer)
            except Exception:
                pass
            self._focus_restore_timer = None
        # labwc can report a short focus loss while the DE layer is being
        # restored after a game exits. Home music should continue in that
        # case; pause it only for a known launched game/app handoff.
        if (self._active_game_appid or self._game_session_active or
                self._active_external_app):
            self._pause_music()
        # Promote right away so the user never sees the reorder when they return
        if self._promote_appid is not None or self._promote_name is not None:
            if self._promote_timer_id is not None:
                try:
                    GLib.source_remove(self._promote_timer_id)
                except Exception:
                    pass
                self._promote_timer_id = None
            self._promote_launched()
        return False

    def _on_focus_in(self, widget, event):
        """Ignore incidental focus while a launched game owns the session."""
        _log("Focus: gained (DE)")
        if self._active_external_app:
            self._active_external_app = False
            self._active_external_launch_id = None
            latest_game = self._latest_running_game_record()
            if latest_game is not None:
                self._activate_game_record(latest_game)
                self._user_requested_de_foreground = True
                self._game_session_active = True
                self._game_handoff_confirmed = True
                self._game_handoff_time = time.monotonic()
                self._game_missing_since = None
                if self._game_check_timer_id is None:
                    self._game_check_timer_id = GLib.timeout_add(
                        200, self._check_game_launched)
            else:
                self._user_requested_de_foreground = False
            self._resume_music()
        if self.control_center_open:
            self._set_controller_owner("control_center")
        elif (self._active_game_appid and
              not self._user_requested_de_foreground and
              self._is_launched_game_running()):
            self._ungrab_gamepad()
        elif (self._user_requested_de_foreground and self._active_game_appid and
              self._is_launched_game_running()):
            self._set_controller_owner("de")
            if self._home_foreground_timer is None:
                self._schedule_home_foreground()
        else:
            self._set_controller_owner("de")
        if self._game_session_active and self._active_game_appid:
            if (not self._is_launched_game_running() and
                    self._game_exit_confirmed()):
                _log("DE focus returned after game process exited")
                self._finish_game_session()
                return False
        if (self._active_game_appid and not self._user_requested_de_foreground
                and not self.control_center_open):
            _log("Ignoring incidental DE focus during game session")
            self._pause_music()
            if self._focus_restore_timer is not None:
                try:
                    GLib.source_remove(self._focus_restore_timer)
                except Exception:
                    pass
                self._focus_restore_timer = None
            # Keep the black launch cover in place through the handoff. Once
            # the game is visible, lower the DE without presenting it again.
            if (self._launch_black_screen is None and
                    self._is_launched_game_running()):
                self._set_shell_layer("bottom")
                self.set_keep_above(False)
                if not self._shell_layer_shell:
                    self.iconify()
            return False
        if not self._active_game_appid:
            self._resume_music()
        # Wait for focus to settle: games can briefly bounce focus through the
        # DE while opening. Raise the shell only if it still owns focus.
        if self._focus_restore_timer is not None:
            try:
                GLib.source_remove(self._focus_restore_timer)
            except Exception:
                pass
        self._focus_restore_timer = GLib.timeout_add_seconds(
            3, self._restore_de_layer_if_active)
        self._focus_out_time = None
        return False

    def _restore_de_layer_if_active(self):
        """Put the DE above Steam after focus has returned and settled."""
        self._focus_restore_timer = None
        if self._user_requested_de_foreground and self._active_game_appid:
            game_running = self._is_launched_game_running()
            if game_running or self._game_session_active:
                self._set_controller_owner("de")
                self._set_shell_layer("overlay")
                self.set_keep_above(True)
                if self._home_foreground_timer is None:
                    self._schedule_home_foreground()
                return False
            # The launch never reached a game window, so stop treating a stale
            # app id as a background game that needs foreground enforcement.
            self._active_game_appid = None
            self._active_game_name = ""
        if (self._active_game_appid and
                not self._user_requested_de_foreground):
            _log("Skipped DE foreground restore; game session has not been returned")
            return False
        if self.is_active():
            if (self._active_game_appid and not self._user_requested_de_foreground
                    and self._is_launched_game_running()):
                _log("Focus returned temporarily while the launched game is still running")
                if not self.control_center_open:
                    self.set_keep_above(False)
                    if not self._shell_layer_shell:
                        self.iconify()
                return False
            self._active_game_appid = None
            self._active_game_name = ""
            self._set_controller_owner("de")
            if self._gamepad_passthrough_active:
                self._regrab_gamepad()
            self.set_keep_above(True)
            self._set_shell_layer("top")
            _log("DE layer restored after focus settled")
        return False

        """Move the item whose items[i][key]==value to index 0 of items."""
        for i, item in enumerate(items):
            if item.get(key) == value:
                if i == 0:
                    return False  # already at front
                items.pop(i)
                items.insert(0, item)
                return True
        return False

        """Apply persisted game order (promoted games stay at front across restarts)."""
        order_file = os.path.expanduser("~/.config/console-de/game_order")
        if not os.path.isfile(order_file):
            return
        try:
            with open(order_file, "r") as f:
                saved_appids = [line.strip() for line in f if line.strip()]
        except Exception as e:
            _log(f"Failed to read game order: {e}")
            return
        if not saved_appids:
            return
        # Build a lookup by appid
        by_appid = {g.get('appid'): g for g in self.games}
        ordered = []
        for appid in saved_appids:
            g = by_appid.get(appid)
            if g:
                ordered.append(g)
        # Append any games not in the saved list (new games) at the end
        for g in self.games:
            if g not in ordered:
                ordered.append(g)
        if ordered != self.games:
            self.games = ordered
            _log(f"Restored saved game order ({len(saved_appids)} games)")
            self.gsel = 0

        """Persist current game order so promoted games stay at front."""
        order_file = os.path.expanduser("~/.config/console-de/game_order")
        try:
            with open(order_file, "w") as f:
                for g in self.games:
                    aid = g.get('appid')
                    if aid is not None:
                        f.write(f"{aid}\n")
        except Exception as e:
            _log(f"Failed to save game order: {e}")

        """Move the item whose items[i][key]==value to index 0 of items."""
        for i, item in enumerate(items):
            if item.get(key) == value:
                if i == 0:
                    return False  # already at front
                items.pop(i)
                items.insert(0, item)
                return True
        return False

        """Apply persisted game order (promoted games stay at front across restarts)."""
        order_file = os.path.expanduser("~/.config/console-de/game_order")
        if not os.path.isfile(order_file):
            return
        try:
            with open(order_file, "r") as f:
                saved_appids = [line.strip() for line in f if line.strip()]
        except Exception as e:
            _log(f"Failed to read game order: {e}")
            return
        if not saved_appids:
            return
        # Build a lookup by appid
        by_appid = {g.get('appid'): g for g in self.games}
        ordered = []
        for appid in saved_appids:
            g = by_appid.get(appid)
            if g:
                ordered.append(g)
        # Append any games not in the saved list (new games) at the end
        for g in self.games:
            if g not in ordered:
                ordered.append(g)
        if ordered != self.games:
            self.games = ordered
            _log(f"Restored saved game order ({len(saved_appids)} games)")
            self.gsel = 0

        """Persist current game order so promoted games stay at front."""
        order_file = os.path.expanduser("~/.config/console-de/game_order")
        try:
            with open(order_file, "w") as f:
                for g in self.games:
                    aid = g.get('appid')
                    if aid is not None:
                        f.write(f"{aid}\n")
        except Exception as e:
            _log(f"Failed to save game order: {e}")

class StartupAnimation(Gtk.DrawingArea):
    """Full-screen cinematic startup animation drawn with Cairo."""

    def __init__(self):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.connect("draw", self._on_draw)
        self._start_time = time.monotonic()
        self._last_frame_time = 0.0
        self._particles = self._create_particles()
        self._glow_surfaces = {}
        self._welcome_layout = None
        self._welcome_size = None
        # Follow GTK's frame clock instead of a free-running GLib timer. This
        # avoids queuing redraws between display frames and reduces jitter.
        self._anim_id = self.add_tick_callback(self._on_animate)

    def stop_animation(self):
        """Stop the frame callback as soon as the one-shot intro is removed."""
        if self._anim_id is not None:
            try:
                self.remove_tick_callback(self._anim_id)
            except Exception:
                pass
            self._anim_id = None

    def _create_particles(self):
        import random
        particles = []
        for _ in range(72 if _LOW_PERF_MODE else 140):
            if random.random() < 0.66:
                final_x = min(1.0, max(0.0, random.gauss(0.39, 0.22)))
                final_y = min(1.0, max(0.0, random.gauss(0.49, 0.25)))
            else:
                final_x = random.random()
                final_y = random.random()
            size = (random.uniform(2.4, 4.4) if random.random() < 0.08
                    else random.uniform(0.3, 2.2))
            gold = random.random() < 0.18
            # Reuse a small set of pre-rendered glow sprites instead of
            # constructing a Cairo radial-gradient pattern for every mote on
            # every frame.
            glow_radius = max(4, int(round(size * 3.2 / 2.0)) * 2)
            particles.append({
                'x': final_x,
                'y': final_y,
                'wave_x': random.random(),
                'wave_y_offset': random.gauss(0.0, 0.018),
                'drift_x': random.uniform(-0.025, 0.025),
                'drift_y': random.uniform(-0.025, 0.025),
                'size': size,
                'base_alpha': (random.uniform(0.66, 0.96)
                               if random.random() < 0.12
                               else random.uniform(0.06, 0.64)),
                'gold': gold,
                'color': (1.0, 0.73, 0.54) if gold else (0.93, 0.95, 1.0),
                'glow_key': (glow_radius, gold),
                'twinkle': random.uniform(0.0, 2 * math.pi),
            })
        return particles

    def _glow_surface(self, radius, gold):
        key = (radius, gold)
        surface = self._glow_surfaces.get(key)
        if surface is not None:
            return surface
        size = radius * 2 + 2
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        glow_cr = cairo.Context(surface)
        center = size / 2.0
        color = (1.0, 0.73, 0.54) if gold else (0.93, 0.95, 1.0)
        gradient = cairo.RadialGradient(center, center, 0.0,
                                        center, center, radius)
        gradient.add_color_stop_rgba(0.0, *color, 1.0)
        gradient.add_color_stop_rgba(1.0, *color, 0.0)
        glow_cr.set_source(gradient)
        glow_cr.paint()
        surface.flush()
        self._glow_surfaces[key] = surface
        return surface

    def _get_welcome_layout(self, w, h):
        size = (w, h)
        if self._welcome_layout is not None and self._welcome_size == size:
            return self._welcome_layout
        metrics = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        metrics_cr = cairo.Context(metrics)
        text = "Welcome"
        metrics_cr.select_font_face("SF Compact Display", cairo.FONT_SLANT_NORMAL,
                                    cairo.FONT_WEIGHT_BOLD)
        font_options = cairo.FontOptions()
        font_options.set_antialias(cairo.ANTIALIAS_BEST)
        font_options.set_hint_style(cairo.HINT_STYLE_FULL)
        font_options.set_hint_metrics(cairo.HINT_METRICS_ON)
        metrics_cr.set_font_options(font_options)
        metrics_cr.set_font_size(h * 0.055)
        ascent, descent, _height, _max_x_advance, _max_y_advance = (
            metrics_cr.font_extents())
        text_extents = metrics_cr.text_extents(text)
        text_advance = text_extents[4]
        layout = {
            "text": text,
            "width": text_advance,
            "x_bearing": text_extents[0],
            "text_height": text_extents[3],
            "baseline": (h - ascent - descent) / 2.0 + ascent,
            "ascent": ascent,
            "descent": descent,
            "font_options": font_options,
            "font_size": h * 0.055,
        }
        self._welcome_layout = layout
        self._welcome_size = size
        return layout

    def _on_animate(self, widget, frame_clock, user_data=None):
        frame_time = frame_clock.get_frame_time() / 1_000_000.0
        interval = _ANIMATION_INTERVAL_MS / 1000.0
        if frame_time - self._last_frame_time >= interval:
            self._last_frame_time = frame_time
            self.queue_draw()
        return True

    def _on_draw(self, widget, cr):
        w = widget.get_allocated_width()
        h = widget.get_allocated_height()
        if w <= 0 or h <= 0 or not HAVE_CAIRO:
            return False

        t = time.monotonic() - self._start_time
        _paint_settings_gradient(cr, w, h)

        # Trace the whole word from left to right after the incoming wave has
        # arrived. The lettering completes one second before this screen ends.
        text_start = 1.0
        text_finish = 5.0
        if t >= text_start:
            layout = self._get_welcome_layout(w, h)
            draw_progress = min(1.0, max(
                0.0, (t - text_start) / (text_finish - text_start)))
            draw_progress = 0.5 - 0.5 * math.cos(math.pi * draw_progress)
            cr.save()
            cr.set_font_options(layout["font_options"])
            cr.select_font_face("SF Compact Display", cairo.FONT_SLANT_NORMAL,
                                cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(layout["font_size"])
            gradient = cairo.LinearGradient(0, 0, w, 0)
            gradient.add_color_stop_rgba(0.0, 0.99, 0.87, 0.77, 1.0)
            gradient.add_color_stop_rgba(0.48, 0.91, 0.91, 0.91, 1.0)
            gradient.add_color_stop_rgba(1.0, 0.73, 0.82, 0.96, 1.0)
            left = (w - layout["width"]) / 2.0
            cr.move_to(left, layout["baseline"])
            cr.text_path(layout["text"])
            text_path = cr.copy_path()
            cr.new_path()
            cr.rectangle(left - layout["x_bearing"], 0,
                         layout["width"] * draw_progress, h)
            cr.clip()

            # A faint two-pass outline creates a soft halo around the portion
            # being written, while the gradient fill keeps the letter edges
            # crisp.
            cr.append_path(text_path)
            cr.set_source_rgba(1.0, 0.84, 0.70, 0.035)
            cr.set_line_width(max(5.0, h * 0.004))
            cr.stroke()
            cr.append_path(text_path)
            cr.set_source_rgba(0.93, 0.92, 0.88, 0.055)
            cr.set_line_width(max(2.0, h * 0.0018))
            cr.stroke()

            cr.append_path(text_path)
            cr.clip()
            cr.set_source(gradient)
            cr.paint()
            cr.restore()

        # The wave travels first; the particles then spread across the screen.
        # Compute frame-wide easing once, not per mote.
        # Let the incoming wave finish first, then spread the dust over 3.8s.
        # Continue subtle individual drift after the spread completes so the
        # cloud stays alive through the final frame of the intro.
        disperse_progress = min(1.0, max(0.0, (t - 1.0) / 3.8))
        disperse_progress = disperse_progress * disperse_progress * (3 - 2 * disperse_progress)
        settle = 1.0 - disperse_progress
        drift = 1.0 - math.exp(-t * 0.35)
        glow_threshold = 3.4 if _LOW_PERF_MODE else 1.8

        for particle in self._particles:
            wx = particle['wave_x']
            wave_y = (0.70 - 0.24 * wx +
                      0.075 * math.sin(wx * 2 * math.pi - t * 0.65) +
                      particle['wave_y_offset'])
            appeared = min(1.0, max(0.0, (t - wx * 0.82) / 0.16))
            px = w * (wx * settle + particle['x'] * disperse_progress)
            py = h * (wave_y * settle + particle['y'] * disperse_progress)
            phase = particle['twinkle']
            px += w * (particle['drift_x'] * drift +
                       0.006 * math.sin(t * 0.72 + phase))
            py += h * (particle['drift_y'] * drift +
                       0.0045 * math.sin(t * 0.55 + phase * 1.37))

            twinkle = 0.58 + 0.42 * math.sin(t * 3.0 + particle['twinkle'])
            alpha = particle['base_alpha'] * appeared * twinkle
            color = particle['color']

            if particle['size'] >= glow_threshold:
                radius, gold = particle['glow_key']
                sprite = self._glow_surface(radius, gold)
                center = sprite.get_width() / 2.0
                cr.set_source_surface(sprite, px - center, py - center)
                cr.paint_with_alpha(alpha * 0.22)

            cr.set_source_rgba(*color, alpha if particle['gold'] else alpha * 0.88)
            cr.arc(px, py, particle['size'], 0, 2 * math.pi)
            cr.fill()

        return False

def main():
    _log(f"=== Console DE shell starting (scale={_scale}x, "
         f"low_perf={_LOW_PERF_MODE}) ===")
    _log(f"Session started at {time.strftime('%Y-%m-%d %H:%M:%S')}")

    app = Gtk.Application(
        application_id="com.console.de",
        flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
    current_window = {"window": None}
    pending_control_center = {"value": False}

    def on_activate(a):
        if current_window["window"] is not None:
            if pending_control_center["value"]:
                pending_control_center["value"] = False
                GLib.idle_add(
                    current_window["window"]._toggle_control_center)
                return
            current_window["window"]._bring_de_home()
            return
        win = ShellWindow(a)
        current_window["window"] = win
        win.present()
        if pending_control_center["value"]:
            pending_control_center["value"] = False
            GLib.idle_add(win._toggle_control_center)
        # Log actual GTK scale
        screen = win.get_screen()
        scale = screen.get_monitor_scale(0) if hasattr(screen, 'get_monitor_scale') else 'N/A'
        win_scale = win.get_scale_factor() if hasattr(win, 'get_scale_factor') else 'N/A'
        _log(f"Window scale_factor={win_scale}, monitor_scale={scale}, "
             f"GDK_SCALE={os.environ.get('GDK_SCALE')}")
        _log("Window presented")
        libs = _steam_library_paths()
        _log(f"Steam libraries: {libs}")

    def on_command_line(a, command_line):
        args = command_line.get_arguments()
        if "--control-center" in args:
            _log("Control Center requested through shell command line")
            window = current_window["window"]
            if window is not None:
                GLib.idle_add(window._toggle_control_center)
            else:
                pending_control_center["value"] = True
                a.activate()
        else:
            a.activate()
        return 0

    app.connect("activate", on_activate)
    app.connect("command-line", on_command_line)
    return app.run(sys.argv)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        _log(f"FATAL: {e}")
        import traceback
        _log(traceback.format_exc())
        traceback.print_exc()
        sys.exit(1)
