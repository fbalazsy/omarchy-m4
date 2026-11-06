#!/bin/sh
# Install the Aura stage1 (not --raw) and a rescue stage2.
# The stage2 initramfs does not switch_root. No rootfs write. No reboot.
# Pins come from the file on the command line. /c is never fetched.
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
  if [ -n "${RESCUE_ANSWERS:-}" ]; then IFS= read -r REPLY <&3 || return 1
  else IFS= read -r REPLY < /dev/tty || return 1; fi
}
upload() {
  [ -n "${H:-}" ] || return 0
  [ -f "${STATUS:-}" ] || return 0
  command -v curl >/dev/null 2>&1 || return 0
  curl -fsS --max-time 30 -T "$STATUS" "$H/up/rescue-status.txt" >/dev/null 2>&1 || true
}

load_pins() {
  p=$1
  [ -L "$p" ] && die "pins file is a symlink"
  [ -f "$p" ] || die "pins file missing"
  sz=$(file_size "$p") || die "pins size unreadable"
  [ "$sz" -le 4096 ] || die "pins file too large"
  H= LOADER= LOADER_SHA= STAGE2= STAGE2_SHA= STAGE2_BYTES=
  ESP_UUID= ESP_START= ESP_BLOCKS= OLD_STAGE2_SHA= OLD_STAGE2_BYTES=
  BACKUP= STORE_START=
  seen=""
  cr=$(printf '\r')
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%"$cr"}
    case "$line" in ''|'#'*) continue ;; esac
    case "$line" in *=*) ;; *) die "pins line has no equals" ;; esac
    key=${line%%=*}; val=${line#*=}
    case "$key" in
      H|LOADER|LOADER_SHA|STAGE2|STAGE2_SHA|STAGE2_BYTES|ESP_UUID|ESP_START|ESP_BLOCKS|OLD_STAGE2_SHA|OLD_STAGE2_BYTES|BACKUP|STORE_START) ;;
      *) die "pins key not allowed" ;;
    esac
    case " $seen " in *" $key "*) die "duplicate pin $key" ;; esac
    case "$val" in *'$'*|*'`'*|*' '*|*'	'*|*'|'|*'&'*|*'<'|*'>'*) die "pins value rejected" ;; esac
    seen="$seen $key"
    case "$key" in
      H) H=$val ;;
      LOADER) LOADER=$val ;;
      LOADER_SHA) LOADER_SHA=$val ;;
      STAGE2) STAGE2=$val ;;
      STAGE2_SHA) STAGE2_SHA=$val ;;
      STAGE2_BYTES) STAGE2_BYTES=$val ;;
      ESP_UUID) ESP_UUID=$val ;;
      ESP_START) ESP_START=$val ;;
      ESP_BLOCKS) ESP_BLOCKS=$val ;;
      OLD_STAGE2_SHA) OLD_STAGE2_SHA=$val ;;
      OLD_STAGE2_BYTES) OLD_STAGE2_BYTES=$val ;;
      BACKUP) BACKUP=$val ;;
      STORE_START) STORE_START=$val ;;
    esac
  done < "$p"
  for req in H LOADER LOADER_SHA STAGE2 STAGE2_SHA STAGE2_BYTES ESP_UUID ESP_START ESP_BLOCKS OLD_STAGE2_SHA OLD_STAGE2_BYTES BACKUP STORE_START; do
    case " $seen " in *" $req "*) ;; *) die "pins missing $req" ;; esac
  done
  ESP_UUID=$(printf '%s\n' "$ESP_UUID" | tr '[:lower:]' '[:upper:]')
  LOADER_SHA=$(printf '%s\n' "$LOADER_SHA" | tr '[:upper:]' '[:lower:]')
  STAGE2_SHA=$(printf '%s\n' "$STAGE2_SHA" | tr '[:upper:]' '[:lower:]')
  OLD_STAGE2_SHA=$(printf '%s\n' "$OLD_STAGE2_SHA" | tr '[:upper:]' '[:lower:]')
  printf '%s\n' "$ESP_UUID" | grep -Eq '^[0-9A-F]{8}-([0-9A-F]{4}-){3}[0-9A-F]{12}$' || die "ESP_UUID is not a UUID"
  valid_sum "$LOADER_SHA" || die "LOADER_SHA is not a sha256"
  valid_sum "$STAGE2_SHA" || die "STAGE2_SHA is not a sha256"
  valid_sum "$OLD_STAGE2_SHA" || die "OLD_STAGE2_SHA is not a sha256"
  case "$LOADER" in *[!A-Za-z0-9._-]*) die "LOADER name rejected" ;; esac
  case "$STAGE2" in *[!A-Za-z0-9._-]*) die "STAGE2 name rejected" ;; esac
  case "$BACKUP" in aurora/stage2-d487.bin) ;; *) die "BACKUP path rejected" ;; esac
  case "$H" in http://*|https://*) ;; *) die "H must be http or https" ;; esac
  case "$H" in *@*) die "H must not carry credentials" ;; esac
}

identify_esp() {
  bytes=$((ESP_BLOCKS * 4096))
  list=$(diskutil list disk0 2>"$W/list.err") || die "diskutil list disk0 failed"
  nmatch=0; edev=
  for n in $(printf '%s\n' "$list" | awk '{print $NF}' | grep '^disk0s' || true); do
    info=$(diskutil info "$n" 2>"$W/info.err") || die "diskutil info $n failed"
    if printf '%s\n' "$info" | grep -qi "$ESP_UUID" \
      && printf '%s\n' "$info" | grep -q "($ESP_START 4096-Byte-Device-Blocks)" \
      && printf '%s\n' "$info" | grep -q "($bytes Bytes)"; then
      nmatch=$((nmatch + 1)); edev=$n
      printf '%s\n' "$info" > "$W/esp-info.txt"
    fi
  done
  [ "$nmatch" = 1 ] || die "ESP identity matches=$nmatch want=1"
  case "$edev" in disk0s[0-9]|disk0s[0-9][0-9]|disk0s[0-9][0-9][0-9]) ;; *) die "ESP device name rejected" ;; esac
  say "ESP_DEV $edev"
}

mount_esp() {
  mode=$1
  mp_now=$(sed -n 's/^.*Mount Point:[[:space:]]*//p' "$W/esp-info.txt" | head -n 1 | tr -d '[:space:]')
  case "$mp_now" in [Nn]ot*) mp_now="" ;; esac
  esp_dir=
  if [ "$mode" = ro ] && [ -n "$mp_now" ]; then
    mline=$(mount | grep -F " on ${mp_now} " || true)
    case "$mline" in *read-only*|*rdonly*) esp_dir=$mp_now; return 0 ;; esac
  fi
  esp_dir=$W/esp
  mkdir -p "$esp_dir"
  if [ "$mode" = ro ]; then
    mount -t msdos -o rdonly "/dev/$edev" "$esp_dir" || die "read-only mount failed"
  else
    mount -t msdos -o rw "/dev/$edev" "$esp_dir" || die "ESP read-write mount failed"
  fi
  MOUNTED=1
}

hash_stage2() {
  f=$esp_dir/aurora/stage2.bin
  [ -L "$f" ] && die "stage2 is a symlink"
  [ -f "$f" ] || die "aurora/stage2.bin missing"
  sz=$(file_size "$f") || die "stage2 size unreadable"
  sum=$(sha256_file "$f") || die "stage2 hash failed"
  valid_sum "$sum" || die "stage2 hash failed"
  printf '%s %s\n' "$sum" "$sz"
}

main() {
  MOUNTED=0
  [ $# -eq 1 ] || [ $# -eq 2 ] || { printf '%s\n' "STOP usage: sh rescue pinsfile [rollback]" >&2; exit 1; }
  mode=install
  if [ $# -eq 2 ]; then
    [ "$2" = rollback ] || { printf '%s\n' "STOP usage: sh rescue pinsfile [rollback]" >&2; exit 1; }
    mode=rollback
  fi
  W=${RESCUE_WORK:-/tmp/m4-rescue}
  case "$W" in *..*) printf '%s\n' "STOP bad work dir" >&2; exit 1 ;; /tmp/*) ;; *) printf '%s\n' "STOP work dir must be under /tmp" >&2; exit 1 ;; esac
  mkdir -p "$W" || exit 1
  STATUS=$W/status.txt
  : > "$STATUS"
  if [ -n "${RESCUE_ANSWERS:-}" ]; then exec 3< "$RESCUE_ANSWERS" || die "cannot open answers"; fi
  say "RESCUE 1"
  say "ROOTFS_WRITE none"
  say "REBOOT none"
  say "FORMAT none"
  say "CREDENTIALS not-fetched"
  load_pins "$1"
  uid=$(id -u 2>/dev/null || echo nope)
  [ "$uid" = 0 ] || die "not root"
  say "1 downloads"
  curl -fsS --max-time 120 -o "$W/loader.bin" "$H/$LOADER" || die "loader download failed"
  curl -fsS --max-time 120 -o "$W/loader.sha" "$H/$LOADER.sha256" || die "loader sidecar failed"
  curl -fsS --max-time 180 -o "$W/stage2.bin" "$H/$STAGE2" || die "stage2 download failed"
  curl -fsS --max-time 60 -o "$W/stage2.sha" "$H/$STAGE2.sha256" || die "stage2 sidecar failed"
  lgot=$(sha256_file "$W/loader.bin") || die "loader hash failed"
  sgot=$(sha256_file "$W/stage2.bin") || die "stage2 hash failed"
  lside=$(awk '{print $1}' "$W/loader.sha" | tr '[:upper:]' '[:lower:]')
  sside=$(awk '{print $1}' "$W/stage2.sha" | tr '[:upper:]' '[:lower:]')
  [ "$lgot" = "$lside" ] && [ "$lgot" = "$LOADER_SHA" ] || die "loader is not the Aura stage1"
  [ "$sgot" = "$sside" ] && [ "$sgot" = "$STAGE2_SHA" ] || die "stage2 is not the rescue image"
  [ "$(file_size "$W/stage2.bin")" = "$STAGE2_BYTES" ] || die "stage2 size mismatch"
  say "LOADER_SHA256 $lgot"
  say "RESCUE_STAGE2_SHA256 $sgot"
  say "2 ESP read-only"
  identify_esp
  mount_esp ro
  set -- $(hash_stage2)
  curs=$1; curz=$2
  say "CURRENT_STAGE2 $curs $curz"
  if [ "$curs" = "$STAGE2_SHA" ]; then
    say "ESP already holds the rescue stage2"
  elif [ "$curs" = "$OLD_STAGE2_SHA" ] && [ "$curz" = "$OLD_STAGE2_BYTES" ]; then
    say "CURRENT_IS_D487"
  else
    die "ESP stage2 is neither d487 nor the rescue image"
  fi
  umount "$esp_dir" >/dev/null 2>&1 || die "ESP unmount failed"
  MOUNTED=0
  if [ "$mode" = rollback ]; then
    printf '%s\n' "Restore $BACKUP onto aurora/stage2.bin. Boot object is not changed. No reboot."
    printf '%s\n' "Proceed? [y/N] "
    read_answer || die "no confirm"
    [ "$REPLY" = y ] || die "declined; nothing changed"
    identify_esp
    mount_esp rw
    bsum=$(sha256_file "$esp_dir/$BACKUP") || die "backup missing"
    [ "$bsum" = "$OLD_STAGE2_SHA" ] || die "backup is not the saved stage2"
    cp "$esp_dir/$BACKUP" "$esp_dir/aurora/stage2.bin" || die "restore copy failed"
    set -- $(hash_stage2)
    [ "$1" = "$OLD_STAGE2_SHA" ] && [ "$2" = "$OLD_STAGE2_BYTES" ] || die "restore read-back failed"
    umount "$esp_dir" >/dev/null 2>&1 || die "ESP unmount failed"
    MOUNTED=0
    say "ROLLBACK_DONE stage2=$1"
    upload
    exit 0
  fi
  say "3 kcbz"
  kmutil inspect -z -B "$W/loader.bin" --show-mach-boot-properties > "$W/inspect.txt" 2>&1 || die "kmutil inspect failed"
  grep -qiE 'kcbz[[:space:]]*:' "$W/inspect.txt" || die "kcbz not reported; nothing changed"
  grep -qiE 'kcbz[[:space:]]*:[[:space:]]*(0x)?0+[[:space:]]*$' "$W/inspect.txt" || die "kcbz is not 0; nothing changed"
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
  printf '%s\n' "Install Aura stage1 (not raw) and replace ESP aurora/stage2.bin with the rescue image."
  printf '%s\n' "Current stage2 is copied to $BACKUP first. Trial file is not touched. No reboot."
  printf '%s\n' "Proceed? [y/N] "
  read_answer || die "no confirm"
  [ "$REPLY" = y ] || die "declined; nothing changed"
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
  if grep -qi "incorrect\|failed\|error" "$W/bputil.txt"; then unset PW; die "bputil refused; ESP not changed"; fi
  if grep -q "AP boot mode\|Failed to create local policy" "$W/bputil.txt"; then unset PW; die "not One True Recovery; ESP not changed"; fi
  bputil -d -v "$VGID" > "$W/policy.txt" 2>&1 || true
  if ! grep -qi "permissive" "$W/policy.txt"; then unset PW; die "boot policy is not Permissive; ESP not changed"; fi
  say "7 configure-boot"
  unset PW
  [ -r /dev/tty ] && [ -w /dev/tty ] || die "no terminal"
  rm -f "$W/kmutil.fifo"
  mkfifo "$W/kmutil.fifo" || die "cannot make kmutil fifo"
  tee "$W/kmutil.txt" < "$W/kmutil.fifo" > /dev/tty &
  teepid=$!
  kmutil configure-boot -c "$W/loader.bin" -v "$vol" < /dev/tty > "$W/kmutil.fifo" 2>&1
  krc=$?
  wait "$teepid" || true
  rm -f "$W/kmutil.fifo"
  say "KMUTIL_EXIT $krc"
  if [ "$krc" != 0 ] || grep -qi 'abort' "$W/kmutil.txt"; then die "kmutil exited $krc; ESP not changed"; fi
  grep -q 'done' "$W/kmutil.txt" || die "kmutil exited $krc without done; ESP not changed"
  say "8 ESP write"
  identify_esp
  mount_esp rw
  set -- $(hash_stage2)
  curs=$1; curz=$2
  if [ "$curs" != "$OLD_STAGE2_SHA" ] && [ "$curs" != "$STAGE2_SHA" ]; then
    die "stage2 changed under us; ESP left as it is"
  fi
  avail=$(df -k "$esp_dir" | awk 'NR==2 { print $4 }')
  need=$(( (OLD_STAGE2_BYTES + STAGE2_BYTES) / 1024 + 1024 ))
  [ "$avail" -gt "$need" ] || die "ESP free space ${avail} KiB is not enough"
  if [ "$curs" = "$OLD_STAGE2_SHA" ]; then
    if [ -e "$esp_dir/$BACKUP" ]; then
      bsum=$(sha256_file "$esp_dir/$BACKUP") || die "backup hash failed"
      [ "$bsum" = "$OLD_STAGE2_SHA" ] || die "backup exists and is not d487; ESP stage2 not replaced"
    else
      cp "$esp_dir/aurora/stage2.bin" "$esp_dir/$BACKUP" || die "backup copy failed"
      bsum=$(sha256_file "$esp_dir/$BACKUP") || die "backup hash failed"
      [ "$bsum" = "$OLD_STAGE2_SHA" ] || die "backup hash mismatch; ESP stage2 not replaced"
    fi
    cp "$W/stage2.bin" "$esp_dir/aurora/stage2.bin" || die "stage2 copy failed"
  fi
  set -- $(hash_stage2)
  [ "$1" = "$STAGE2_SHA" ] && [ "$2" = "$STAGE2_BYTES" ] || die "ESP read-back is not the rescue image"
  say "ESP_STAGE2 $1"
  say "BACKUP $BACKUP"
  umount "$esp_dir" >/dev/null 2>&1 || die "ESP unmount failed after write"
  MOUNTED=0
  say "RESCUE_DONE loader=$lgot stage2=$sgot"
  say "Restart from the Apple menu when you choose. This script does not reboot."
  upload
  exit 0
}
main "$@"
