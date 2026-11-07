#!/bin/sh
# udhcpc callback. A host route to one address, and no default route.
set -eu
[ "${1:-}" = "bound" ] || exit 0
host=${M4_TELEMETRY_HOST:-}
if [ -z "$host" ] && [ -r /etc/m4diag-host ]; then
  host=$(cat /etc/m4diag-host)
fi
[ -n "$host" ] || exit 0
case $host in
  *[!0-9.]*) exit 0 ;;
esac
ip addr flush dev "$interface" 2>/dev/null || true
if [ -n "${subnet:-}" ]; then
  ip addr add "$ip/$subnet" dev "$interface"
else
  ip addr add "$ip" dev "$interface"
fi
if [ -n "${router:-}" ]; then
  ip route replace "$host/32" via "$router" dev "$interface"
else
  ip route replace "$host/32" dev "$interface"
fi
exit 0
