#!/bin/sh
# Synthetic NetworkManager fixtures. The log must not contain the fixture ssid or psk.
set -eu
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
T=$(mktemp -d)
cleanup() {
  if findmnt -R -n "$T" 2>/dev/null | grep -q .; then
    printf '%s\n' "CLEANUP_REFUSED mounted"
    exit 1
  fi
  rm -rf "$T"
}
trap cleanup EXIT
umask 077
src=$T/profiles
out=$T/out
mkdir -p "$src"
ssid_a=fixture-alpha
psk_a=fixture-psk-alpha-1
ssid_b=fixture-beta
psk_b=fixture-psk-beta-22
ssid_h=fixture-hidden
psk_h=fixture-psk-hidden-3
hex_p=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
cat > "$src/01-a" << EOF
[connection]
type=wifi
autoconnect=true
autoconnect-priority=10

[wifi]
ssid=$ssid_a
mode=infrastructure

[wifi-security]
key-mgmt=wpa-psk
psk=$psk_a

[ipv4]
method=auto
EOF
cat > "$src/02-eap" << EOF
[connection]
type=wifi
autoconnect=true

[wifi]
ssid=fixture-eap

[wifi-security]
key-mgmt=wpa-eap

[ipv4]
method=auto
EOF
cat > "$src/03-b" << EOF
[connection]
type=wifi
autoconnect=yes

[wifi]
ssid=$ssid_b

[wifi-security]
key-mgmt=wpa-psk
psk=$psk_b

[ipv4]
method=auto
EOF
cat > "$src/04-hidden" << EOF
[connection]
type=wifi
autoconnect-priority=5

[wifi]
ssid=$ssid_h
hidden=true

[wifi-security]
key-mgmt=wpa-psk
psk=$psk_h
EOF
printf '%s\n' '[connection]' 'type=wifi' 'autoconnect=false' '[wifi]' 'ssid=fixture-off' '[wifi-security]' 'key-mgmt=wpa-psk' 'psk=fixture-psk-off-000' > "$src/05-off"
printf '%s\n' '[connection]' 'type=wifi' '[wifi]' 'ssid=fixture-sae' '[wifi-security]' 'key-mgmt=sae' 'psk=fixture-psk-sae-000' > "$src/06-sae"
printf '%s\n' '[connection]' 'type=wifi' '[wifi]' 'ssid=fixture-open' '[wifi-security]' 'key-mgmt=none' > "$src/07-open"
printf '%s\n' '[connection]' 'type=wifi' '[wifi]' 'ssid=fixture-manual' '[wifi-security]' 'key-mgmt=wpa-psk' 'psk=fixture-psk-manual' '[ipv4]' 'method=manual' > "$src/08-manual"
printf '%s\n' '[connection]' 'type=wifi' '[wifi]' 'ssid=fixture-quote' '[wifi-security]' 'key-mgmt=wpa-psk' 'psk=bad"quote' > "$src/09-quote"
cat > "$src/10-hex" << EOF
[connection]
type=wifi
autoconnect-priority=7
interface-name=wlan0

[wifi]
ssid=fixture-hex

[wifi-security]
key-mgmt=wpa-psk
psk=$hex_p
EOF
printf '%s\n' '[connection]' 'type=wifi' '[wifi]' 'ssid=fixture-wep' '[wifi-security]' 'key-mgmt=wep' 'wep-key0=fixture' > "$src/11-wep"
printf '%s\n' '[connection]' 'type=wifi' '[wifi]' 'mode=ap' 'ssid=fixture-ap' '[wifi-security]' 'key-mgmt=wpa-psk' 'psk=fixture-psk-ap-0000' > "$src/12-ap"
printf '%s\n' '[connection]' 'type=wifi' '[wifi]' 'ssid=fixture-agent' '[wifi-security]' 'key-mgmt=wpa-psk' 'psk-flags=1' > "$src/13-agent"
printf '%s\n' '[connection]' 'type=ethernet' '[ethernet]' 'mac-address=00:00:00:00:00:00' > "$src/14-eth"
ln -s /etc/passwd "$src/15-link"
printf '%s\n' '[connection]' 'type=wifi' '[wifi]' 'ssid=fixture-short' '[wifi-security]' 'key-mgmt=wpa-psk' 'psk=short' > "$src/16-short"
# shellcheck disable=SC1091
. "$HERE/nm-wifi.sh"
NM_WIFI_AWK=$HERE/nm-wifi.awk nm_wifi_select "$src" "$out" > "$T/select"
for secret in "$ssid_a" "$psk_a" "$ssid_b" "$psk_b" "$ssid_h" "$psk_h" "$hex_p" 'bad"quote'; do
  if grep -q -F "$secret" "$T/select"; then
    printf '%s\n' "LEAK_SELECT"
    exit 1
  fi
done
fail() {
  printf '%s\n' "FAIL_$1"
  grep -E '^(WIFI_|ACCEPT |REJECT )' "$T/select" || true
  exit 1
}
grep -q 'WIFI_USABLE 5' "$T/select" || fail usable
grep -q 'WIFI_TRY 1 1' "$T/select" || fail try1
grep -q 'WIFI_TRY 2 10' "$T/select" || fail try2
grep -q 'WIFI_TRY 3 4' "$T/select" || fail try3
grep -q 'WIFI_REJECT symlink' "$T/select" || fail symlink
grep -q 'REJECT unsupported-auth' "$T/select" || fail auth
grep -q 'REJECT autoconnect-off' "$T/select" || fail off
grep -q 'REJECT ipv4-not-dhcp' "$T/select" || fail ipv4
grep -q 'REJECT bad-psk' "$T/select" || fail badpsk
grep -q -F 'psk="bad\"quote"' "$out/9.conf" || fail escape
grep -q 'REJECT no-psk' "$T/select" || fail nopsk
grep -q 'REJECT not-infra' "$T/select" || fail infra
grep -q 'REJECT not-wifi' "$T/select" || fail notwifi
mode=$(stat -c %a "$out/1.conf")
[ "$mode" = "600" ] || fail mode
grep -q 'scan_ssid=0' "$out/1.conf" || fail scan0
grep -q 'key_mgmt=WPA-PSK' "$out/1.conf" || fail keymgmt
grep -q -F "ssid=\"$ssid_a\"" "$out/1.conf" || fail ssid
grep -q -F "psk=\"$psk_a\"" "$out/1.conf" || fail psk
grep -q 'scan_ssid=1' "$out/4.conf" || fail scan1
grep -q "psk=$hex_p" "$out/10.conf" || fail hex
grep -q 'iface wlan0' "$out/10.meta" || fail iface
if [ -f "$out/2.conf" ] || [ -f "$out/14.conf" ] || [ -f "$out/15.conf" ]; then
  printf '%s\n' "REJECTED_PROFILE_WRITTEN"
  exit 1
fi
wpa=${WPA_BIN:-$HERE/out/wpa_supplicant}
if [ -x "$wpa" ]; then
  qemu=${QEMU:-qemu-aarch64-static}
  if ! command -v "$qemu" >/dev/null 2>&1; then
    printf '%s\n' "NO_QEMU"
    exit 1
  fi
  set +e
  timeout 3 "$qemu" "$wpa" -i lo -c "$out/1.conf" -qq > "$T/wpa.out" 2>&1
  set -e
  for secret in "$ssid_a" "$psk_a"; do
    if grep -q -F "$secret" "$T/wpa.out"; then
      printf '%s\n' "LEAK_DAEMON"
      exit 1
    fi
  done
  if grep -q 'Invalid configuration' "$T/wpa.out"; then
    printf '%s\n' "DAEMON_CONFIG"
    exit 1
  fi
  printf '%s\n' "DAEMON_QUIET"
fi
printf '%s\n' "NM_WIFI_OK"
