#!/bin/sh
# udhcpc callback for BusyBox 1.36.1.
# That client exports DHCP option 1 as dotted $subnet (sprint_nip) and the
# prefix length as $mask (%u from mton). A prefix-only $subnet is also
# accepted. Nothing from the packet is evaluated. No default route.
set -eu
[ "${1:-}" = "bound" ] || exit 0

case ${interface:-} in
  *[!A-Za-z0-9_.-]*) exit 1 ;;
  [A-Za-z]*) ;;
  *) exit 1 ;;
esac
case ${ip:-} in
  *.*.*.*) ;;
  *) exit 1 ;;
esac

host=${M4_TELEMETRY_HOST:-}
if [ -z "$host" ] && [ -r /etc/m4diag-host ]; then
  host=$(cat /etc/m4diag-host)
fi
case $host in
  *.*.*.*) ;;
  *) exit 1 ;;
esac

is_ipv4() {
  printf '%s\n' "$1" | awk -F. '
    function octet(o) {
      return o ~ /^([0-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5])$/
    }
    { exit (NF == 4 && octet($1) && octet($2) && octet($3) && octet($4)) ? 0 : 1 }
  '
}

is_prefix() {
  case $1 in
    [0-9]|[1-2][0-9]|3[0-2]) return 0 ;;
    *) return 1 ;;
  esac
}

# Contiguous dotted mask to a prefix. Non-contiguous masks fail.
prefix_from_dotted() {
  printf '%s\n' "$1" | awk -F. '
    function octet(o) {
      return o ~ /^([0-9]|[1-9][0-9]|1[0-9][0-9]|2[0-4][0-9]|25[0-5])$/
    }
    {
      if (NF != 4 || !octet($1) || !octet($2) || !octet($3) || !octet($4)) exit 1
      n = $1
      n = n * 256 + $2
      n = n * 256 + $3
      n = n * 256 + $4
      pow = 1
      for (i = 1; i <= 31; i++) pow = pow * 2
      bits = 0
      seen0 = 0
      for (i = 31; i >= 0; i--) {
        bit = int(n / pow)
        bit = bit - int(bit / 2) * 2
        if (bit == 1) {
          if (seen0) exit 1
          bits++
        } else {
          seen0 = 1
        }
        if (i > 0) pow = int(pow / 2)
      }
      printf "%d\n", bits
    }
  '
}

pfx=
if [ -n "${subnet:-}" ]; then
  if is_prefix "$subnet"; then
    pfx=$subnet
  elif is_ipv4 "$subnet"; then
    pfx=$(prefix_from_dotted "$subnet") || exit 1
  else
    exit 1
  fi
fi
if [ -n "${mask:-}" ]; then
  mp=
  if is_prefix "$mask"; then
    mp=$mask
  elif is_ipv4 "$mask"; then
    mp=$(prefix_from_dotted "$mask") || exit 1
  else
    exit 1
  fi
  if [ -n "$pfx" ] && [ "$pfx" != "$mp" ]; then
    exit 1
  fi
  pfx=${pfx:-$mp}
fi
[ -n "$pfx" ] || exit 1
is_ipv4 "$ip" || exit 1
is_ipv4 "$host" || exit 1

router_first=${router%% *}
case ${router_first:-} in
  '') ;;
  *.*.*.*) is_ipv4 "$router_first" || exit 1 ;;
  *) exit 1 ;;
esac

# Validation finished. Only now is the interface changed.
command ip addr flush dev "$interface" 2>/dev/null || true
command ip addr add "$ip/$pfx" dev "$interface"
if [ -n "$router_first" ]; then
  command ip route replace "$host/32" via "$router_first" dev "$interface"
else
  command ip route replace "$host/32" dev "$interface"
fi
exit 0
