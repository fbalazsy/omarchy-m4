#!/bin/sh
# Enroll the wrapped Aura rescue boot object. kmutil without --raw.
# Does not write the ESP or the Linux root. Does not reboot. Does not fetch /c.
set -u

say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$STATUS"; }
die() {
  printf 'STOP %s\n' "$*" >&2
  printf 'STOP %s\n' "$*" >> "$STATUS"
  upload
  exit 1
}
file_size() {
  sz=$(stat -f %z "$1" 2>/dev/null) || sz=$(stat -c %s "$1" 2>/dev/null) || return 1
  printf '%s\n' "$sz"
}
sha256_file() {
  if [ -x /sbin/sha256 ]; then /sbin/sha256 "$1" | awk '{print $NF}'
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else return 1; fi
}
valid_sum() { printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{64}$'; }
read_answer() {
  if [ -n "${RESCUE_ANSWERS:-}" ]; then IFS= read -r REPLY <&3 || return 1
  else IFS= read -r REPLY < /dev/tty || return 1; fi
}
upload() {
  [ -n "${H:-}" ] || return 0
  [ -f "${STATUS:-}" ] || return 0
  command -v curl >/dev/null 2>&1 || return 0
  curl -fsS --max-time 30 -T "$STATUS" "$H/up/rescue-v2-status.txt" >/dev/null 2>&1 || true
}

load_pins() {
  p=$1
  [ -L "$p" ] && die "pins file is a symlink"
  [ -f "$p" ] || die "pins file missing"
  sz=$(file_size "$p") || die "pins size unreadable"
  [ "$sz" -le 4096 ] || die "pins file too large"
  H= BOOT= BOOT_SHA= BOOT_BYTES= ESP_UUID= ESP_START= ESP_BLOCKS= STORE_START=
  seen=""
  cr=$(printf '\r')
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%"$cr"}
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in *=*) ;; *) die "pins line has no equals" ;; esac
    key=${line%%=*}; val=${line#*=}
    case "$key" in
      H|BOOT|BOOT_SHA|BOOT_BYTES|ESP_UUID|ESP_START|ESP_BLOCKS|STORE_START) ;;
      *) die "pins key not allowed" ;;
    esac
    case " $seen " in *" $key "*) die "duplicate pin $key" ;; esac
    case "$val" in *'$'*|*'`'*|*' '*|*'	'*|*'|'|*'&'*|*'<'|*'>'*) die "pins value rejected" ;; esac
    seen="$seen $key"
    case "$key" in
      H) H=$val ;;
      BOOT) BOOT=$val ;;
      BOOT_SHA) BOOT_SHA=$val ;;
      BOOT_BYTES) BOOT_BYTES=$val ;;
      ESP_UUID) ESP_UUID=$val ;;
      ESP_START) ESP_START=$val ;;
      ESP_BLOCKS) ESP_BLOCKS=$val ;;
      STORE_START) STORE_START=$val ;;
    esac
  done < "$p"
  for req in H BOOT BOOT_SHA BOOT_BYTES ESP_UUID ESP_START ESP_BLOCKS STORE_START; do
    case " $seen " in *" $req "*) ;; *) die "pins missing $req" ;; esac
  done
  ESP_UUID=$(printf '%s\n' "$ESP_UUID" | tr '[:lower:]' '[:upper:]')
  BOOT_SHA=$(printf '%s\n' "$BOOT_SHA" | tr '[:upper:]' '[:lower:]')
  printf '%s\n' "$ESP_UUID" | grep -Eq '^[0-9A-F]{8}-([0-9A-F]{4}-){3}[0-9A-F]{12}$' || die "ESP_UUID is not a UUID"
  valid_sum "$BOOT_SHA" || die "BOOT_SHA is not a sha256"
  case "$BOOT" in *[!A-Za-z0-9._-]*) die "BOOT name rejected" ;; esac
  case "$H" in http://*|https://*) ;; *) die "H must be http or https" ;; esac
  case "$H" in *@*) die "H must not carry credentials" ;; esac
}

main() {
  [ $# -eq 1 ] || { printf '%s\n' "STOP usage: sh enroll-v2 pinsfile" >&2; exit 1; }
  W=${RESCUE_WORK:-/tmp/m4-rescue-v2}
  case "$W" in *..*) printf '%s\n' "STOP bad work dir" >&2; exit 1 ;; /tmp/*) ;; *) printf '%s\n' "STOP work dir must be under /tmp" >&2; exit 1 ;; esac
  mkdir -p "$W" || exit 1
  STATUS=$W/status.txt
  : > "$STATUS"
  if [ -n "${RESCUE_ANSWERS:-}" ]; then exec 3< "$RESCUE_ANSWERS" || die "cannot open answers"; fi
  say "RESCUE_V2 1"
  say "ESP_WRITE none"
  say "ROOTFS_WRITE none"
  say "REBOOT none"
  say "RAW_KMUTIL none"
  say "CREDENTIALS not-fetched"
  load_pins "$1"
  uid=$(id -u 2>/dev/null || echo nope)
  [ "$uid" = 0 ] || die "not root"
  say "1 download"
  curl -fsS --max-time 180 -o "$W/boot.macho" "$H/$BOOT" || die "boot object download failed"
  curl -fsS --max-time 60 -o "$W/boot.sha" "$H/$BOOT.sha256" || die "boot object sidecar failed"
  got=$(sha256_file "$W/boot.macho") || die "boot object hash failed"
  side=$(awk '{print $1}' "$W/boot.sha" | tr '[:upper:]' '[:lower:]')
  [ "$got" = "$side" ] && [ "$got" = "$BOOT_SHA" ] || die "boot object hash mismatch"
  [ "$(file_size "$W/boot.macho")" = "$BOOT_BYTES" ] || die "boot object size mismatch"
  say "BOOT_SHA256 $got"
  say "2 ESP identity, read-only"
  bytes=$((ESP_BLOCKS * 4096))
  list=$(diskutil list disk0 2>"$W/list.err") || die "diskutil list disk0 failed"
  nmatch=0
  for n in $(printf '%s\n' "$list" | awk '{print $NF}' | grep '^disk0s' || true); do
    info=$(diskutil info "$n" 2>"$W/info.err") || die "diskutil info $n failed"
    if printf '%s\n' "$info" | grep -qi "$ESP_UUID" \
      && printf '%s\n' "$info" | grep -q "($ESP_START 4096-Byte-Device-Blocks)" \
      && printf '%s\n' "$info" | grep -q "($bytes Bytes)"; then
      nmatch=$((nmatch + 1))
    fi
  done
  [ "$nmatch" = 1 ] || die "ESP identity matches=$nmatch want=1"
  say "ESP_MATCHES 1"
  say "3 kcbz"
  kmutil inspect -z -B "$W/boot.macho" --show-mach-boot-properties > "$W/inspect.txt" 2>&1 || die "kmutil inspect failed"
  grep -qiE 'kcbz[[:space:]]*:' "$W/inspect.txt" || die "kcbz not reported; nothing enrolled"
  grep -qiE 'kcbz[[:space:]]*:[[:space:]]*(0x)?0+[[:space:]]*$' "$W/inspect.txt" || die "kcbz is not 0; nothing enrolled"
  say "KCBZ 0"
  say "4 system volume"
  STORE=
  for n in $(printf '%s\n' "$list" | awk '{print $NF}' | grep '^disk0s' || true); do
    diskutil info "$n" 2>/dev/null | grep -q "($STORE_START 4096-Byte-Device-Blocks)" && STORE=$n
  done
  [ -n "$STORE" ] || die "macOS container store not found"
  CONT=$(diskutil info "$STORE" | sed -n 's/.*APFS Container:[[:space:]]*\(disk[0-9]*\).*/\1/p')
  [ -n "$CONT" ] || die "APFS container not found"
  diskutil apfs list "$CONT" > "$W/apfs.txt" 2>&1 || die "apfs list failed"
  SYSDEV=$(awk '/APFS Volume Disk \(Role\):/ && /\(System\)/ {print $(NF-1); exit}' "$W/apfs.txt")
  [ -n "$SYSDEV" ] || die "no System volume"
  VGID=$(diskutil info "$SYSDEV" | sed -n 's/.*APFS Volume Group:[[:space:]]*\([0-9A-Fa-f-]*\).*/\1/p' | head -n 1)
  [ -n "$VGID" ] || die "no volume group"
  vol=$(diskutil info "$SYSDEV" | sed -n 's/.*Mount Point:[[:space:]]*//p' | head -n 1)
  case "$vol" in /*) ;; *) diskutil mount "$SYSDEV" >/dev/null 2>&1 || die "cannot mount System volume"; vol=$(diskutil info "$SYSDEV" | sed -n 's/.*Mount Point:[[:space:]]*//p' | head -n 1) ;; esac
  [ -d "$vol" ] || die "System volume not a directory"
  say "SYSDEV $SYSDEV"
  say "5 confirm"
  printf '%s\n' "Enroll the rescue Mach-O on $vol. ESP is not written. No reboot."
  printf '%s\n' "Proceed? [y/N] "
  read_answer || die "no confirm"
  [ "$REPLY" = y ] || die "declined; nothing enrolled"
  printf '%s\n' "macOS user [morpheus]: "
  read_answer || die "no user"
  U=${REPLY:-morpheus}
  printf 'password for %s: ' "$U"
  stty -echo < /dev/tty 2>/dev/null || true
  read_answer || { stty echo < /dev/tty 2>/dev/null || true; die "no password"; }
  stty echo < /dev/tty 2>/dev/null || true
  printf '\n'
  PW=$REPLY
  [ -n "$PW" ] || die "empty password"
  say "6 boot policy"
  bputil -nc -v "$VGID" -u "$U" -p "$PW" > "$W/bputil.txt" 2>&1 || true
  if grep -qi "incorrect\|failed\|error" "$W/bputil.txt"; then unset PW; die "bputil refused; nothing enrolled"; fi
  if grep -q "AP boot mode\|Failed to create local policy" "$W/bputil.txt"; then unset PW; die "not One True Recovery; nothing enrolled"; fi
  bputil -d -v "$VGID" > "$W/policy.txt" 2>&1 || true
  if ! grep -qi "permissive" "$W/policy.txt"; then unset PW; die "boot policy is not Permissive; nothing enrolled"; fi
  say "7 configure-boot"
  unset PW
  [ -r /dev/tty ] && [ -w /dev/tty ] || die "no terminal"
  rm -f "$W/kmutil.fifo"
  mkfifo "$W/kmutil.fifo" || die "cannot make kmutil fifo"
  tee "$W/kmutil.txt" < "$W/kmutil.fifo" > /dev/tty &
  teepid=$!
  kmutil configure-boot -c "$W/boot.macho" -v "$vol" < /dev/tty > "$W/kmutil.fifo" 2>&1
  krc=$?
  wait "$teepid" || true
  rm -f "$W/kmutil.fifo"
  say "KMUTIL_EXIT $krc"
  if [ "$krc" != 0 ] || grep -qi 'abort' "$W/kmutil.txt"; then die "kmutil exited $krc; boot object not changed"; fi
  grep -q 'done' "$W/kmutil.txt" || die "kmutil exited $krc without done"
  say "RESCUE_V2_DONE boot=$got"
  say "Restart from the Apple menu when you choose. This script does not reboot."
  upload
  exit 0
}
main "$@"
