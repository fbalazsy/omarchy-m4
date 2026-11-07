#!/bin/sh
# udhcpc callback. One host route to the pinned address. No default route.
# DHCP option strings are not executed.
set -eu
[ "${1:-}" = "bound" ] || exit 0
case ${interface:-} in
  [A-Za-z][A-Za-z0-9_.-]*) ;;
  *) exit 0 ;;
esac
case ${ip:-} in
  [0-9]*.[0-9]*.[0-9]*.[0-9]*) ;;
  *) exit 0 ;;
esac
host=${M4_TELEMETRY_HOST:-}
if [ -z "$host" ] && [ -r /etc/m4diag-host ]; then
  host=$(cat /etc/m4diag-host)
fi
case $host in
  [0-9]*.[0-9]*.[0-9]*.[0-9]*) ;;
  *) exit 0 ;;
esac
ip addr flush dev "$interface" 2>/dev/null || true
if [ -n "${subnet:-}" ]; then
  case $subnet in
    [0-9]|[0-9][0-9]) ;;
    *) exit 0 ;;
  esac
  ip addr add "$ip/$subnet" dev "$interface"
else
  ip addr add "$ip" dev "$interface"
fi
case ${router:-} in
  [0-9]*.[0-9]*.[0-9]*.[0-9]*)
    ip route replace "$host/32" via "$router" dev "$interface"
    ;;
  '')
    ip route replace "$host/32" dev "$interface"
    ;;
  *) exit 0 ;;
esac
exit 0
