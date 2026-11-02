#!/bin/sh
# Remove PS5 Shell / Console DE files installed by install.sh.
set -eu

USER_NAME="${SUDO_USER:-${USER:-}}"
if [ -z "$USER_NAME" ]; then
  echo "Could not determine the target user." >&2
  exit 1
fi
USER_HOME="$(getent passwd "$USER_NAME" | cut -d: -f6)"
if [ -z "$USER_HOME" ] || [ ! -d "$USER_HOME" ]; then
  echo "Could not determine the home directory for $USER_NAME." >&2
  exit 1
fi

echo "This removes the Console DE session, its installed launchers, udev rule, and configuration/assets for $USER_NAME."
echo "Steam and system packages are left installed because install.sh does not track which packages were already present."
printf 'Continue? [y/N] '
read answer
case "$answer" in y|Y|yes|YES) ;; *) echo "Cancelled."; exit 0 ;; esac

CFG="$USER_HOME/.config"
remove_path() {
  path="$1"
  if [ -e "$path" ] || [ -L "$path" ]; then
    sudo rm -rf -- "$path"
    echo "Removed $path"
  fi
}

remove_path /usr/share/wayland-sessions/console-de.desktop
remove_path /usr/local/bin/console-de-session
remove_path /usr/local/bin/console-de-shell
remove_path /usr/local/bin/console-de-env
remove_path /usr/local/bin/console-de-screenshot
remove_path /usr/local/bin/console-de-reload
remove_path /usr/local/bin/console-de-power
remove_path /usr/local/bin/console-de-doctor
remove_path /usr/local/bin/power-menu
remove_path /etc/udev/rules.d/80-console-de-uinput.rules
sudo udevadm control --reload 2>/dev/null || true
sudo udevadm trigger --sysname-match=uinput 2>/dev/null || true

for path in \
  "$CFG/labwc/rc.xml" "$CFG/labwc/menu.xml" "$CFG/labwc/autostart" \
  "$CFG/labwc/theme/openbox-3/themerc" \
  "$CFG/waybar/config" "$CFG/waybar/style.css" \
  "$CFG/wofi/config" "$CFG/wofi/style.css" \
  "$CFG/foot/foot.ini" "$CFG/console-de"; do
  remove_path "$path"
done

echo "Console DE files removed. Review empty directories under ~/.config if desired."
echo "Log out or restart your display manager to leave the session completely."
