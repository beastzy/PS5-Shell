#!/bin/sh
# Console DE installer — detects the package manager, installs dependencies,
# copies configs, registers the session. Safe to re-run.
# NOTE: does NOT abort if a package install fails — configs are copied anyway
# and `console-de-doctor` reports what's missing.

SRC="$(cd "$(dirname "$0")" && pwd)"
USER_NAME="${SUDO_USER:-$USER}"
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
PKG_FAIL=""

echo "== Console DE installer =="

# Detect the distribution first, then select its native package manager.
DISTRO_ID="unknown"
DISTRO_LIKE=""
DISTRO_NAME="unknown Linux distribution"
if [ -r /etc/os-release ]; then
  . /etc/os-release
  DISTRO_ID="${ID:-unknown}"
  DISTRO_LIKE="${ID_LIKE:-}"
  DISTRO_NAME="${PRETTY_NAME:-$DISTRO_ID}"
fi
printf '%s\n' "-- detected distribution: $DISTRO_NAME (ID=$DISTRO_ID${DISTRO_LIKE:+, ID_LIKE=$DISTRO_LIKE})"

PM=""
case "$DISTRO_ID" in
  arch|cachyos|manjaro|endeavouros|garuda|artix) PM="pacman" ;;
  debian|ubuntu|linuxmint|pop|elementary|zorin) PM="apt" ;;
  fedora|rhel|centos|rocky|almalinux|nobara) PM="dnf" ;;
  opensuse*|sles|sled) PM="zypper" ;;
esac
if [ -z "$PM" ]; then
  case " $DISTRO_LIKE " in
    *" arch "*) PM="pacman" ;;
    *" debian "*|*" ubuntu "*) PM="apt" ;;
    *" fedora "*|*" rhel "*) PM="dnf" ;;
    *" suse "*|*" opensuse "*) PM="zypper" ;;
  esac
fi
# Some derivatives omit ID_LIKE; retain the package-manager fallback for them.
if [ -z "$PM" ]; then
  if command -v pacman >/dev/null 2>&1; then PM="pacman"
  elif command -v apt-get >/dev/null 2>&1; then PM="apt"
  elif command -v dnf >/dev/null 2>&1; then PM="dnf"
  elif command -v zypper >/dev/null 2>&1; then PM="zypper"
  fi
fi
if [ -n "$PM" ]; then
  echo "-- package manager: $PM"
else
  echo "!! No supported package manager found. Install dependencies manually."
  PKG_FAIL="$PKG_FAIL (no package manager)"
fi

# ---- 1. packages ----------------------------------------------------------
# core: compositor, panel, launcher, wallpaper, terminal, screenshots
# shell: GTK3 + PyGObject for the PS5-style home shell
if [ "$PM" = "pacman" ]; then
  sudo pacman -S --needed --noconfirm labwc waybar wofi swaybg foot gamescope \
    xorg-xwayland python-gobject gtk3 grim slurp brightnessctl wireplumber xdg-desktop-portal-wlr \
    inter-font \
    || PKG_FAIL="$PKG_FAIL (pacman core)"
elif [ "$PM" = "apt" ]; then
  sudo apt-get update || true
  sudo apt-get install -y labwc waybar wofi swaybg foot \
    python3-gi python3-gi-cairo gir1.2-gtk-3.0 grim slurp brightnessctl \
    wireplumber xdg-desktop-portal-wlr gamescope xwayland fonts-inter ffmpeg \
    || PKG_FAIL="$PKG_FAIL (apt core)"
elif [ "$PM" = "dnf" ]; then
  sudo dnf install -y labwc waybar wofi swaybg foot \
    python3-gobject gtk3 grim slurp brightnessctl wireplumber xdg-desktop-portal-wlr \
    xorg-x11-server-Xwayland \
    gamescope rsms-inter-fonts \
    || PKG_FAIL="$PKG_FAIL (dnf core)"
elif [ "$PM" = "zypper" ]; then
  sudo zypper --non-interactive install labwc waybar wofi swaybg foot \
    python3-gobject gtk3-tools grim slurp brightnessctl wireplumber xdg-desktop-portal-wlr \
    gamescope xwayland inter-fonts \
    || PKG_FAIL="$PKG_FAIL (zypper core)"
else
  echo "!! No supported package manager found. Install manually:"
  echo "   labwc waybar wofi swaybg foot python-gobject gtk3 grim slurp"
  echo "   brightnessctl wireplumber xdg-desktop-portal-wlr xwayland Steam"
fi
[ -n "$PM" ] && echo "-- packages attempted via $PM"

# Steam is installed separately so an unavailable Steam repository does not
# prevent the rest of the DE from being installed. The native client manages
# game downloads and account sign-in; this script never handles credentials.
if ! command -v steam >/dev/null 2>&1; then
  case "$PM" in
    pacman)
      # Steam and its 32-bit graphics runtime are in Arch's multilib repo.
      # Detect the active GPU runtime to avoid installing a mismatched Vulkan ICD.
      STEAM_PACKAGES="steam steam-devices lib32-vulkan-icd-loader"
      if pacman -Q nvidia-utils >/dev/null 2>&1 || pacman -Q nvidia-open >/dev/null 2>&1 || pacman -Q nvidia-dkms >/dev/null 2>&1; then
        STEAM_PACKAGES="$STEAM_PACKAGES lib32-nvidia-utils"
      elif pacman -Q vulkan-radeon >/dev/null 2>&1; then
        STEAM_PACKAGES="$STEAM_PACKAGES lib32-vulkan-radeon lib32-mesa"
      elif pacman -Q vulkan-intel >/dev/null 2>&1; then
        STEAM_PACKAGES="$STEAM_PACKAGES lib32-vulkan-intel lib32-mesa"
      fi
      if grep -Eq '^[[:space:]]*\[multilib\][[:space:]]*$' /etc/pacman.conf 2>/dev/null; then
        sudo pacman -S --needed --noconfirm $STEAM_PACKAGES \
          || PKG_FAIL="$PKG_FAIL (Steam via pacman; check multilib/repositories)"
      else
        echo "!! Steam needs Arch's multilib repository, which is not enabled."
        echo "   Enable [multilib] in /etc/pacman.conf, then rerun this installer."
        PKG_FAIL="$PKG_FAIL (Steam multilib repository disabled)"
      fi
      ;;
    apt)
      # Steam's Linux client requires 32-bit support on Debian-family systems.
      if command -v dpkg >/dev/null 2>&1; then
        sudo dpkg --add-architecture i386 \
          && sudo apt-get update \
          && sudo apt-get install -y steam-installer steam-devices \
          || PKG_FAIL="$PKG_FAIL (Steam via apt; enable the distro's non-free/multiverse repository)"
      else
        PKG_FAIL="$PKG_FAIL (Steam via apt; dpkg unavailable)"
      fi
      ;;
    dnf)
      sudo dnf install -y steam \
        || PKG_FAIL="$PKG_FAIL (Steam via dnf; enable a Steam-providing repository such as RPM Fusion)"
      ;;
    zypper)
      sudo zypper --non-interactive install steam \
        || PKG_FAIL="$PKG_FAIL (Steam via zypper; enable a repository that provides Steam)"
      ;;
    *)
      PKG_FAIL="$PKG_FAIL (Steam install unsupported for detected distro)"
      ;;
  esac
else
  echo "-- Steam is already installed"
fi

# python-evdev for the gamepad daemon
if ! python3 -c "import evdev" 2>/dev/null; then
  case "$PM" in
    pacman) sudo pacman -S --needed --noconfirm python-evdev || PKG_FAIL="$PKG_FAIL (python-evdev)" ;;
    apt)    sudo apt-get install -y python3-evdev || PKG_FAIL="$PKG_FAIL (python-evdev)" ;;
    dnf)    sudo dnf install -y python3-evdev || PKG_FAIL="$PKG_FAIL (python-evdev)" ;;
    zypper) sudo zypper --non-interactive install python3-evdev || PKG_FAIL="$PKG_FAIL (python-evdev)" ;;
  esac
fi

# ---- 2. configs -----------------------------------------------------------
CFG="$USER_HOME/.config"
sudo -u "$USER_NAME" mkdir -p \
  "$CFG/labwc/theme/openbox-3" "$CFG/waybar" "$CFG/wofi" "$CFG/console-de" "$CFG/foot"

copy() {
  # copy + announce what actually changed (stale installs otherwise go
  # unnoticed — e.g. new keybinds or shell fixes that "don't work")
  if ! sudo -u "$USER_NAME" cmp -s "$1" "$2" 2>/dev/null; then
    sudo -u "$USER_NAME" cp -r "$1" "$2"
    echo "  updated: $2"
  fi
}
copy_tree() {
  # Merge bundled assets into the user's config directory, preserving any
  # extra files they have added themselves.
  if [ ! -d "$1" ]; then
    echo "!! bundled asset directory is missing: $1"
    return 0
  fi
  find "$1" -type f -print | while IFS= read -r asset_src; do
    asset_rel="${asset_src#"$1"/}"
    asset_dst="$2/$asset_rel"
    if ! sudo -u "$USER_NAME" cmp -s "$asset_src" "$asset_dst" 2>/dev/null; then
      sudo -u "$USER_NAME" mkdir -p "$(dirname "$asset_dst")"
      sudo -u "$USER_NAME" cp "$asset_src" "$asset_dst"
      echo "  updated: $asset_dst"
    fi
  done
}
copy "$SRC/config/labwc/rc.xml"              "$CFG/labwc/rc.xml"
copy "$SRC/config/labwc/menu.xml"            "$CFG/labwc/menu.xml"
copy "$SRC/config/labwc/autostart"           "$CFG/labwc/autostart"
sudo -u "$USER_NAME" chmod +x "$CFG/labwc/autostart"
copy "$SRC/config/labwc/theme/openbox-3/themerc" "$CFG/labwc/theme/openbox-3/themerc"
copy "$SRC/config/waybar/config"             "$CFG/waybar/config"
copy "$SRC/config/waybar/style.css"          "$CFG/waybar/style.css"
copy "$SRC/config/wofi/config"               "$CFG/wofi/config"
copy "$SRC/config/wofi/style.css"            "$CFG/wofi/style.css"
copy "$SRC/config/console-de/gamepad.py"     "$CFG/console-de/gamepad.py"
copy "$SRC/config/console-de/shell.py"       "$CFG/console-de/shell.py"
copy "$SRC/config/console-de/cursor_hide.py" "$CFG/console-de/cursor_hide.py"
sudo -u "$USER_NAME" chmod +x "$CFG/console-de/gamepad.py" "$CFG/console-de/shell.py"
sudo -u "$USER_NAME" chmod +x "$CFG/console-de/cursor_hide.py"
copy "$SRC/config/console-de/labwc-output-scale.py" \
     "$CFG/console-de/labwc-output-scale.py"
copy_tree "$SRC/config/console-de/icons"     "$CFG/console-de/icons"
copy_tree "$SRC/config/console-de/sfx"       "$CFG/console-de/sfx"
copy "$SRC/config/console-de/settings_startup_gradient.png" \
     "$CFG/console-de/settings_startup_gradient.png"
copy "$SRC/config/console-de/status_notice_controller_bg.png" \
     "$CFG/console-de/status_notice_controller_bg.png"
copy "$SRC/config/console-de/status_notice_microphone_bg.png" \
     "$CFG/console-de/status_notice_microphone_bg.png"
copy "$SRC/config/console-de/status_notice_controller_icon.png" \
     "$CFG/console-de/status_notice_controller_icon.png"
copy "$SRC/config/console-de/status_notice_battery_outline.png" \
     "$CFG/console-de/status_notice_battery_outline.png"
copy "$SRC/config/console-de/status_notice_microphone_icon.png" \
     "$CFG/console-de/status_notice_microphone_icon.png"
copy "$SRC/config/console-de/confirm.wav"    "$CFG/console-de/confirm.wav"
copy "$SRC/config/console-de/select.wav"      "$CFG/console-de/select.wav"
# Record the installed source revision for Settings → About update checks.
# Git checkouts have an exact revision. GitHub source archives omit .git, so
# use the public main-branch revision available at install time as their ID.
BUILD_COMMIT=""
if git -C "$SRC" rev-parse --verify HEAD >/dev/null 2>&1; then
  BUILD_COMMIT="$(git -C "$SRC" rev-parse --verify HEAD 2>/dev/null)"
else
  BUILD_COMMIT="$(python3 -c 'import json, urllib.request; r=urllib.request.Request("https://api.github.com/repos/beastzy/PS5-Shell/commits/beta", headers={"User-Agent":"PS5-Shell-installer"}); print(json.load(urllib.request.urlopen(r, timeout=6))["sha"])' 2>/dev/null || true)"
fi
case "$BUILD_COMMIT" in
  *[!0-9a-fA-F]*|"")
    echo "-- could not record the installed GitHub revision; update comparison will be unavailable"
    ;;
  *)
    if [ "${#BUILD_COMMIT}" -eq 40 ]; then
      printf '%s\n' "$BUILD_COMMIT" | sudo -u "$USER_NAME" tee "$CFG/console-de/install_commit" >/dev/null
      BUILD_COMMIT_SHORT="$(printf '%s' "$BUILD_COMMIT" | cut -c1-7)"
      echo "-- installed revision: $BUILD_COMMIT_SHORT"
    else
      echo "-- GitHub returned an invalid revision; update comparison will be unavailable"
    fi
    ;;
esac
# foot terminal theme (200% font)
if [ ! -f "$CFG/foot/foot.ini" ]; then
  copy "$SRC/config/foot/foot.ini" "$CFG/foot/foot.ini"
else
  echo "-- keeping your existing foot.ini"
fi
echo "-- configs copied to ~/.config"

# ---- 3. session + helpers -------------------------------------------------
sudo install -m 755 "$SRC/scripts/console-de-session" /usr/local/bin/console-de-session
sudo install -m 755 "$SRC/scripts/console-de-shell"   /usr/local/bin/console-de-shell
sudo install -m 755 "$SRC/scripts/console-de-env"     /usr/local/bin/console-de-env
sudo install -m 755 "$SRC/scripts/console-de-screenshot" /usr/local/bin/console-de-screenshot
sudo install -m 755 "$SRC/scripts/console-de-reload"  /usr/local/bin/console-de-reload
sudo install -m 755 "$SRC/scripts/power-menu"         /usr/local/bin/console-de-power
sudo install -m 755 "$SRC/scripts/console-de-doctor"  /usr/local/bin/console-de-doctor
sudo ln -sf /usr/local/bin/console-de-power /usr/local/bin/power-menu
sudo install -Dm 644 "$SRC/session/console-de.desktop" /usr/share/wayland-sessions/console-de.desktop
echo "-- session registered (Console DE)"

# default scale file (300%, matches the PS5's oversized couch UI) unless
# the user has chosen one — the shell applies it instantly and live
if [ ! -f "$CFG/console-de/scale" ]; then
  sudo -u "$USER_NAME" sh -c "echo 3 > '$CFG/console-de/scale'"
fi
# if an old install left scale at 200% and the user never picked, keep it;
# only the missing-file case gets the new default

# ---- 4. gamepad access ----------------------------------------------------
if getent group input >/dev/null; then
  sudo usermod -aG input "$USER_NAME"
  echo "-- added $USER_NAME to the 'input' group (log out/in for it to apply)"
fi

# the daemon's virtual keyboard needs write access to /dev/uinput
if [ ! -e /dev/uinput ]; then
  sudo modprobe uinput 2>/dev/null || true
fi
sudo tee /etc/udev/rules.d/80-console-de-uinput.rules >/dev/null <<'EOF'
KERNEL=="uinput", GROUP="input", MODE="0660"
EOF
sudo udevadm control --reload 2>/dev/null || true
sudo udevadm trigger --sysname-match=uinput 2>/dev/null || true
echo "-- uinput permissions configured (gamepad virtual keyboard)"

if [ -n "$PKG_FAIL" ]; then
  echo "!! Some packages failed:$PKG_FAIL"
  echo "   Everything else is installed. Run: console-de-doctor"
fi

echo "-- done."
echo "   1. Log out of this session."
echo "   2. At the GDM login screen: click your name -> gear icon -> 'Console DE'."
echo "   3. If anything looks wrong, run: console-de-doctor"
