#!/bin/sh
# Write local.conf from the running M4 system (run as the desktop user, from the repo root). Refuses to overwrite.
set -eu
[ ! -e local.conf ] || { echo "local.conf exists; edit it by hand" >&2; exit 1; }
owner=${SUDO_USER:-$(id -un)}
ctrl=$(cat /sys/class/bluetooth/hci0/address 2>/dev/null | tr a-f A-F || true)
head=$(sed -n 's/^device=//p' /etc/m4/bluetooth.conf 2>/dev/null | tr a-f A-F || true)
[ -n "$ctrl" ] || { echo "no Bluetooth controller (hci0): fill M4_BT_CONTROLLER by hand" >&2; ctrl=00:00:00:00:00:00; }
[ -n "$head" ] || { echo "no headset in /etc/m4/bluetooth.conf: fill M4_BT_HEADSET by hand" >&2; head=00:00:00:00:00:00; }
umask 077
printf 'M4_OWNER=%s\nM4_BT_CONTROLLER=%s\nM4_BT_HEADSET=%s\n' "$owner" "$ctrl" "$head" > local.conf   # M4_OWNER: only the privacy check uses it
echo "wrote local.conf"
