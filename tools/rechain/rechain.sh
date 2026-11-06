#!/bin/sh
# rechain — install the chainloading stage1. Does not write the ESP or reboot.
# Machine pins come from the file on the command line. No credential file is fetched.
set -u

say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$STATUS"; }
die() {
  printf 'STOP %s\n' "$*" >&2
  printf 'STOP %s\n' "$*" >> "$STATUS"
  if [ "${MOUNTED:-0}" = 1 ] && [ -n "${esp_dir:-}" ]; then umount "$esp_dir" >/dev/null 2>&1 || true; MOUNTED=0; fi
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
  if [ -n "${RECHAIN_ANSWERS:-}" ]; then IFS= read -r REPLY <&3 || return 1
  else IFS= read -r REPLY < /dev/tty || return 1; fi
}

load_pins() {
  p=$1
  [ -L "$p" ] && die "pins file is a symlink"
  [ -f "$p" ] || die "pins file missing"
  sz=$(file_size "$p") || die "pins size unreadable"
  [ "$sz" -le 4096 ] || die "pins file too large"
  H= LOADER= LOADER_SHA= ESP_UUID= ESP_START= ESP_BLOCKS= STAGE2_SHA= STAGE2_BYTES= STORE_START=
  seen=""
  cr=$(printf '\r')
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%"$cr"}
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in *=*) ;; *) die "pins line has no equals" ;; esac
    key=${line%%=*}
    val=${line#*=}
    case "$key" in
      H|LOADER|LOADER_SHA|ESP_UUID|ESP_START|ESP_BLOCKS|STAGE2_SHA|STAGE2_BYTES|STORE_START) ;;
      *) die "pins key not allowed" ;;
    esac
    case " $seen " in *" $key "*) die "duplicate pin $key" ;; esac
    case "$val" in *'$'*|*'`'*|*' '*|*'	'*|*'|'|*'&'*|*'<'|*'>'*) die "pins value rejected" ;; esac
    seen="$seen $key"
    case "$key" in
      H) H=$val ;;
      LOADER) LOADER=$val ;;
      LOADER_SHA) LOADER_SHA=$val ;;
      ESP_UUID) ESP_UUID=$val ;;
      ESP_START) ESP_START=$val ;;
      ESP_BLOCKS) ESP_BLOCKS=$val ;;
      STAGE2_SHA) STAGE2_SHA=$val ;;
      STAGE2_BYTES) STAGE2_BYTES=$val ;;
      STORE_START) STORE_START=$val ;;
    esac
  done < "$p"
  for req in H LOADER LOADER_SHA ESP_UUID ESP_START ESP_BLOCKS STAGE2_SHA STAGE2_BYTES STORE_START; do
    case " $seen " in *" $req "*) ;; *) die "pins missing $req" ;; esac
  done
  ESP_UUID=$(printf '%s\n' "$ESP_UUID" | tr '[:lower:]' '[:upper:]')
  LOADER_SHA=$(printf '%s\n' "$LOADER_SHA" | tr '[:upper:]' '[:lower:]')
  STAGE2_SHA=$(printf '%s\n' "$STAGE2_SHA" | tr '[:upper:]' '[:lower:]')
  printf '%s\n' "$ESP_UUID" | grep -Eq '^[0-9A-F]{8}-([0-9A-F]{4}-){3}[0-9A-F]{12}$' || die "ESP_UUID is not a UUID"
  [ "$ESP_UUID" != "00000000-0000-0000-0000-000000000000" ] || die "ESP_UUID is the placeholder"
  valid_sum "$LOADER_SHA" || die "LOADER_SHA is not a sha256"
  valid_sum "$STAGE2_SHA" || die "STAGE2_SHA is not a sha256"
  case "$LOADER" in *[!A-Za-z0-9._-]*) die "LOADER name rejected" ;; esac
  case "$ESP_START" in ''|*[!0-9]*) die "ESP_START is not a number" ;; esac
  case "$ESP_BLOCKS" in ''|*[!0-9]*) die "ESP_BLOCKS is not a number" ;; esac
  case "$STAGE2_BYTES" in ''|*[!0-9]*) die "STAGE2_BYTES is not a number" ;; esac
  case "$STORE_START" in ''|*[!0-9]*) die "STORE_START is not a number" ;; esac
  case "$H" in http://*|https://*) ;; *) die "H must be http or https" ;; esac
  case "$H" in *@*) die "H must not carry credentials" ;; esac
}

upload() {
  [ -n "${H:-}" ] || return 0
  [ -f "${STATUS:-}" ] || return 0
  command -v curl >/dev/null 2>&1 || return 0
  curl -fsS --max-time 30 -T "$STATUS" "$H/up/rechain-status.txt" >/dev/null 2>&1 || true
}

cleanup_esp() {
  if [ "${MOUNTED:-0}" = 1 ]; then
    umount "$esp_dir" || say "UMOUNT_FAILED $esp_dir"
    MOUNTED=0
  fi
}

main() {
  MOUNTED=0
  [ $# -eq 1 ] || { printf '%s\n' "STOP usage: sh rechain pinsfile" >&2; exit 1; }
  W=${RECHAIN_WORK:-/tmp/m4-rechain}
  case "$W" in *..*) printf '%s\n' "STOP bad work dir" >&2; exit 1 ;; /tmp/*) ;; *) printf '%s\n' "STOP work dir must be under /tmp" >&2; exit 1 ;; esac
  mkdir -p "$W" || { printf '%s\n' "STOP cannot create work dir" >&2; exit 1; }
  STATUS=$W/status.txt
  : > "$STATUS"
  if [ -n "${RECHAIN_ANSWERS:-}" ]; then exec 3< "$RECHAIN_ANSWERS" || die "cannot open answers"; fi
  say "RECHAIN 1"
  say "ESP_WRITE none"
  say "REBOOT none"
  say "CREDENTIALS not-fetched"
  load_pins "$1"
  uid=$(id -u 2>/dev/null || echo nope)
  [ "$uid" = 0 ] || die "not root"
  say "1 loader download"
  curl -fsS --max-time 60 -o "$W/loader.bin" "$H/$LOADER" || die "loader download failed"
  curl -fsS --max-time 60 -o "$W/loader.sha" "$H/$LOADER.sha256" || die "loader sidecar download failed"
  side=$(awk '{print $1}' "$W/loader.sha" | tr '[:upper:]' '[:lower:]')
  got=$(sha256_file "$W/loader.bin") || die "loader hash failed"
  valid_sum "$got" || die "loader hash failed"
  [ "$got" = "$side" ] || die "loader sidecar mismatch"
  [ "$got" = "$LOADER_SHA" ] || die "loader is not the pinned stage1"
  say "LOADER_SHA256 $got"
  say "2 ESP read-only"
  bytes=$((ESP_BLOCKS * 4096))
  list=$(diskutil list disk0 2>"$W/list.err") || die "diskutil list disk0 failed"
  nmatch=0; edev=
  for n in $(printf '%s\n' "$list" | awk '{print $NF}' | grep '^disk0s' || true); do
    info=$(diskutil info "$n" 2>"$W/info.err") || die "diskutil info $n failed"
    if printf '%s\n' "$info" | grep -qi "$ESP_UUID" \
      && printf '%s\n' "$info" | grep -q "($ESP_START 4096-Byte-Device-Blocks)" \
      && printf '%s\n' "$info" | grep -q "($bytes Bytes)"; then
      nmatch=$((nmatch + 1)); edev=$n; printf '%s\n' "$info" > "$W/esp-info.txt"
    fi
  done
  [ "$nmatch" = 1 ] || die "ESP identity matches=$nmatch want=1"
  case "$edev" in disk0s[0-9]|disk0s[0-9][0-9]|disk0s[0-9][0-9][0-9]) ;; *) die "ESP device name rejected" ;; esac
  say "ESP_DEV $edev"
  mp_now=$(sed -n 's/^.*Mount Point:[[:space:]]*//p' "$W/esp-info.txt" | head -n 1 | tr -d '[:space:]')
  case "$mp_now" in [Nn]ot*) mp_now="" ;; esac
  esp_dir=
  if [ -n "$mp_now" ]; then
    case "$mp_now" in /*) ;; *) die "unexpected Mount Point" ;; esac
    mline=$(mount | grep -F " on ${mp_now} " || true)
    [ -n "$mline" ] || die "mount options not visible"
    case "$mline" in *read-only*|*rdonly*) esp_dir=$mp_now ;; *) die "ESP is not read-only; not unmounting" ;; esac
  else
    esp_dir=$W/esp
    mkdir -p "$esp_dir"
    mount -t msdos -o rdonly "/dev/$edev" "$esp_dir" || die "read-only mount failed"
    MOUNTED=1
  fi
  stage2=$esp_dir/aurora/stage2.bin
  [ -L "$stage2" ] && die "stage2 is a symlink"
  [ -f "$stage2" ] || die "aurora/stage2.bin missing"
  sz=$(file_size "$stage2") || die "stage2 size unreadable"
  [ "$sz" = "$STAGE2_BYTES" ] || die "stage2 size $sz != pinned $STAGE2_BYTES"
  sum=$(sha256_file "$stage2") || die "stage2 hash failed"
  valid_sum "$sum" || die "stage2 hash failed"
  [ "$sum" = "$STAGE2_SHA" ] || die "stage2 is not the pinned file"
  say "STAGE2_SHA256 $sum"
  say "STAGE2_BYTES $sz"
  for rel in aura-try aurora/aura-try; do
    f=$esp_dir/$rel
    if [ ! -f "$f" ] || [ -L "$f" ]; then say "MARKER_ABSENT $rel"; continue; fi
    msz=$(file_size "$f") || { say "MARKER_STAT_FAILED $rel"; continue; }
    say "MARKER $rel bytes=$msz"
    if [ "$msz" -le 64 ] && command -v od >/dev/null 2>&1; then
      od -An -tx1 -N 64 "$f" | awk -v r="$rel" 'NR==1 { printf "MARKER_HEX %s", r; for (i=1;i<=NF;i++) printf " %s", $i; print "" }'
    fi
  done >> "$STATUS"
  # also show marker lines on the terminal
  grep '^MARKER' "$STATUS" || true
  cleanup_esp
  say "3 kcbz"
  kmutil inspect -z -B "$W/loader.bin" --show-mach-boot-properties > "$W/inspect.txt" 2>&1 || die "kmutil inspect failed"
  if ! grep -qiE 'kcbz[[:space:]]*:' "$W/inspect.txt"; then die "kcbz not reported; boot object not changed"; fi
  if ! grep -qiE 'kcbz[[:space:]]*:[[:space:]]*(0x)?0+[[:space:]]*$' "$W/inspect.txt"; then die "kcbz is not 0; boot object not changed"; fi
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
  printf '%s\n' "Install chainloader $got onto System volume $vol. ESP stage2 stays $sum. No reboot."
  printf '%s\n' "Proceed? [y/N] "
  read_answer || die "no confirm"
  [ "$REPLY" = y ] || die "declined; boot object not changed"
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
  if grep -qi "incorrect\|failed\|error" "$W/bputil.txt"; then unset PW; die "bputil refused; boot object not changed"; fi
  if grep -q "AP boot mode\|Failed to create local policy" "$W/bputil.txt"; then unset PW; die "not One True Recovery; boot object not changed"; fi
  bputil -d -v "$VGID" > "$W/policy.txt" 2>&1 || true
  if ! grep -qi "permissive" "$W/policy.txt"; then unset PW; die "boot policy is not Permissive; boot object not changed"; fi
  say "7 configure-boot"
  if kmutil configure-boot --help 2>&1 | grep -q -- '--password'; then
    printf 'y\n' | kmutil configure-boot -c "$W/loader.bin" -v "$vol" -u "$U" -p "$PW" > "$W/kmutil.txt" 2>&1 || true
  else
    kmutil configure-boot -c "$W/loader.bin" -v "$vol" > "$W/kmutil.txt" 2>&1 || true
  fi
  unset PW
  if grep -qi "error\|fail\|incorrect" "$W/kmutil.txt"; then die "kmutil failed; see the terminal log"; fi
  if ! grep -qi "done" "$W/kmutil.txt"; then die "kmutil did not report done"; fi
  bless --getBoot > "$W/bless.txt" 2>&1 || say "BLESS_GET_FAILED"
  say "RECHAIN_DONE loader=$got stage2=$sum"
  say "Restart from the Apple menu when you choose. This script does not reboot."
  upload
  exit 0
}
main "$@"
