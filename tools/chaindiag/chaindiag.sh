#!/bin/sh
# chaindiag — read-only diagnostic of one ESP stage2. Not a recovery.
# Pins (ESP uuid, start, blocks, report URL) come from the file named on
# the command line. This file has no machine address and no partition id.
# It does not install a loader, write the ESP, repartition, or fetch credentials.
set -u

D487=d4877ab1c94d4324d11179dbd5f143cd8454269e61aaa0807dba5a4b8d49b61d
STAGE2_MAX=268435456
SIB_MAX=67108864

say() { printf '%s\n' "$*"; printf '%s\n' "$*" >> "$REPORT"; }
fail() { code=1; say "STOP $*"; }

catalog() {
  case "$1" in
    d4877ab1c94d4324d11179dbd5f143cd8454269e61aaa0807dba5a4b8d49b61d) printf '%s\n' "default_stage2.bin prior-m4.220-fallback" ;;
    850c303096b741049c860b2192943f38f47bb13bdb414800544a1b3786c373ea) printf '%s\n' "bchain-STAGE2_WANT" ;;
    44ea752c331fb32a9c63d826d7b7ff324d42768d137df2e056ffdcbf43e40160) printf '%s\n' "bchain-prev-STAGE2_WANT" ;;
    8278136603f9fbbf8ee64eb83a5f2231e43af084a94612f45326978b676577ef) printf '%s\n' "trial_g55obs2" ;;
    9ac3f6870d23e39ce60b99d684e1520af115e7ed6f717d16c3f8ebd7a3c309ab) printf '%s\n' "trial_g55e3d-NOT_ARMED" ;;
    54447d15f85207f7c67587044689835c1828584cf3aace421eab138b386b4046) printf '%s\n' "Image_g55e3d-NOT_ARMED" ;;
    *) printf '%s\n' "UNLISTED" ;;
  esac
}

file_size() {
  sz=$(stat -f %z "$1" 2>/dev/null) || sz=$(stat -c %s "$1" 2>/dev/null) || return 1
  printf '%s\n' "$sz"
}

sha256_file() {
  if [ -x /sbin/sha256 ]; then
    /sbin/sha256 "$1" | awk '{print $NF}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    return 1
  fi
}

valid_sum() {
  printf '%s\n' "$1" | grep -Eq '^[0-9a-f]{64}$'
}

load_pins() {
  p=$1
  if [ -L "$p" ]; then fail "pins file is a symlink"; return 1; fi
  if [ ! -f "$p" ]; then fail "pins file missing"; return 1; fi
  sz=$(file_size "$p") || { fail "pins size unreadable"; return 1; }
  if [ "$sz" -gt 4096 ]; then fail "pins file larger than 4096 bytes"; return 1; fi
  ESP_UUID= ESP_START= ESP_BLOCKS= H=
  seen=""
  cr=$(printf '\r')
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%"$cr"}
    case "$line" in
      ''|'#'*) continue ;;
    esac
    case "$line" in
      *=*) ;;
      *) fail "pins line has no equals"; return 1 ;;
    esac
    key=${line%%=*}
    val=${line#*=}
    case "$key" in
      ESP_UUID|ESP_START|ESP_BLOCKS|H) ;;
      *) fail "pins key not allowed"; return 1 ;;
    esac
    case " $seen " in
      *" $key "*) fail "duplicate pin $key"; return 1 ;;
    esac
    case "$val" in
      *'$'*|*'`'*) fail "pins value rejected"; return 1 ;;
      *' '*|*'	'*|*'|'|*'&'|*'<'|*'>'*|*'('*|*')'*|*'\"'*|*"'"*) fail "pins value rejected"; return 1 ;;
    esac
    seen="$seen $key"
    case "$key" in
      ESP_UUID) ESP_UUID=$val ;;
      ESP_START) ESP_START=$val ;;
      ESP_BLOCKS) ESP_BLOCKS=$val ;;
      H) H=$val ;;
    esac
  done < "$p"
  case " $seen " in
    *" ESP_UUID "*) ;;
    *) fail "pins missing ESP_UUID"; return 1 ;;
  esac
  case " $seen " in
    *" ESP_START "*) ;;
    *) fail "pins missing ESP_START"; return 1 ;;
  esac
  case " $seen " in
    *" ESP_BLOCKS "*) ;;
    *) fail "pins missing ESP_BLOCKS"; return 1 ;;
  esac
  return 0
}

validate_pins() {
  ESP_UUID=$(printf '%s\n' "$ESP_UUID" | tr '[:lower:]' '[:upper:]')
  if ! printf '%s\n' "$ESP_UUID" | grep -Eq '^[0-9A-F]{8}-([0-9A-F]{4}-){3}[0-9A-F]{12}$'; then
    fail "ESP_UUID is not a UUID"; return 1
  fi
  if [ "$ESP_UUID" = "00000000-0000-0000-0000-000000000000" ]; then
    fail "ESP_UUID is the placeholder"; return 1
  fi
  case "$ESP_START" in
    ''|*[!0-9]*) fail "ESP_START is not a number"; return 1 ;;
  esac
  case "$ESP_BLOCKS" in
    ''|*[!0-9]*) fail "ESP_BLOCKS is not a number"; return 1 ;;
  esac
  if [ "$ESP_START" -le 0 ] || [ ${#ESP_START} -gt 12 ]; then
    fail "ESP_START out of range"; return 1
  fi
  if [ "$ESP_BLOCKS" -le 0 ] || [ ${#ESP_BLOCKS} -gt 6 ]; then
    fail "ESP_BLOCKS out of range"; return 1
  fi
  case "$H" in
    '') ;;
    http://*|https://*) ;;
    *) fail "H must be http or https"; return 1 ;;
  esac
  case "$H" in
    *@*) fail "H must not carry credentials"; return 1 ;;
  esac
  return 0
}

identify() {
  nmatch=0
  edev=
  : > "$W/vgids"
  bytes=$((ESP_BLOCKS * 4096))
  list=$(diskutil list disk0 2>"$W/list.err") || { fail "diskutil list disk0 failed"; return 1; }
  slices=$(printf '%s\n' "$list" | awk '{print $NF}' | grep '^disk0s' || true)
  if [ -z "$slices" ]; then fail "no disk0 slices"; return 1; fi
  for n in $slices; do
    case "$n" in
      disk0s*) ;;
      *) continue ;;
    esac
    info=$(diskutil info "$n" 2>"$W/info.err") || { fail "diskutil info $n failed"; return 1; }
    printf '%s\n' "$info" | grep -E 'Device Identifier:|Volume Name:|Partition Type:|Partition UUID:|Partition Offset:|Disk Size:|Mount Point:|APFS Volume Group:|Role' | sed "s/^/SLICE $n /" > "$W/slice.txt" || true
    while IFS= read -r line; do say "$line"; done < "$W/slice.txt"
    if printf '%s\n' "$info" | grep -E -q 'Role:.*System|\(System\)'; then
      g=$(printf '%s\n' "$info" | sed -n 's/.*APFS Volume Group:[[:space:]]*//p' | head -n 1 | tr -d '[:space:]')
      if printf '%s\n' "$g" | grep -Eq '^[0-9A-Fa-f]{8}-([0-9A-Fa-f]{4}-){3}[0-9A-Fa-f]{12}$'; then
        printf '%s\n' "$g" >> "$W/vgids"
      fi
    fi
    if printf '%s\n' "$info" | grep -q '0FC63DAF-8483-4772-8E79-3D69D8477DE4'; then
      say "LINUX_PART_HINT $n"
    fi
    if printf '%s\n' "$info" | grep -qi "$ESP_UUID" \
      && printf '%s\n' "$info" | grep -q "($ESP_START 4096-Byte-Device-Blocks)" \
      && printf '%s\n' "$info" | grep -q "($bytes Bytes)"; then
      nmatch=$((nmatch + 1))
      edev=$n
      printf '%s\n' "$info" > "$W/esp-info.txt"
    fi
  done
  say "ESP_MATCHES $nmatch"
  if [ "$nmatch" != 1 ]; then
    fail "ESP identity matches=$nmatch want=1"
    return 1
  fi
  case "$edev" in
    disk0s[0-9]|disk0s[0-9][0-9]|disk0s[0-9][0-9][0-9]) ;;
    *) fail "ESP device name not a GPT slice: $edev"; return 1 ;;
  esac
  say "ESP_DEV $edev"
  return 0
}

mount_esp() {
  MOUNTED=0
  esp_dir=
  esp_info=$(cat "$W/esp-info.txt")
  mp_now=$(printf '%s\n' "$esp_info" | sed -n 's/^.*Mount Point:[[:space:]]*//p' | head -n 1 | tr -d '[:space:]')
  case "$mp_now" in
    [Nn]ot*) mp_now="" ;;
  esac
  if [ -n "$mp_now" ]; then
    case "$mp_now" in
      /*) ;;
      *) fail "unexpected Mount Point"; return 1 ;;
    esac
    mline=$(mount | grep -F " on ${mp_now} " || true)
    if [ -z "$mline" ]; then
      fail "mount options not visible for $mp_now; not touching it"
      return 1
    fi
    case "$mline" in
      *read-only*|*rdonly*) esp_dir=$mp_now ;;
      *) fail "ESP at $mp_now is not read-only; not unmounting and not reading"; return 1 ;;
    esac
    say "ESP_MOUNT existing-ro $esp_dir"
    return 0
  fi
  esp_dir=$W/esp
  mkdir -p "$esp_dir" || { fail "cannot create mount point"; return 1; }
  if ! mount -t msdos -o rdonly "/dev/$edev" "$esp_dir"; then
    fail "read-only mount failed"
    return 1
  fi
  MOUNTED=1
  say "ESP_MOUNT rdonly /dev/$edev"
  return 0
}

hash_line() {
  f=$1
  rel=$2
  cap=$3
  if [ -L "$f" ]; then
    say "FILE_SYMLINK $rel refused"
    return 0
  fi
  if [ ! -f "$f" ]; then
    return 0
  fi
  sz=$(file_size "$f") || { say "FILE_STAT_FAILED $rel"; return 0; }
  if [ "$sz" -gt "$cap" ]; then
    say "FILE $rel bytes=$sz sha256=skipped-large"
    return 0
  fi
  sum=$(sha256_file "$f") || sum=
  if ! valid_sum "$sum"; then
    say "FILE_HASH_FAILED $rel"
    return 0
  fi
  cat=$(catalog "$sum")
  say "FILE $rel bytes=$sz sha256=$sum catalog=$cat"
  if [ "$cat" != "UNLISTED" ]; then
    say "CATALOG_HIT $rel $cat"
  fi
}

inventory() {
  dir=$1
  label=$2
  if [ -L "$dir" ]; then say "DIR_SYMLINK $label refused"; return 0; fi
  if [ ! -d "$dir" ]; then say "DIR_ABSENT $label"; return 0; fi
  if ! command -v find >/dev/null 2>&1; then
    say "INVENTORY not-read reason=no-find label=$label"
    return 0
  fi
  find -P "$dir" -xdev -type f > "$W/files.txt" 2>/dev/null || true
  n=0
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    n=$((n + 1))
    if [ "$n" -gt 40 ]; then
      say "INVENTORY_TRUNCATED $label"
      break
    fi
    case "$f" in
      "$dir"/*) ;;
      *) fail "inventory path escaped $label"; return 1 ;;
    esac
    rel=$label/${f#"$dir"/}
    hash_line "$f" "$rel" "$SIB_MAX"
  done < "$W/files.txt"
}

collect_esp() {
  say "ESP_DIR $esp_dir"
  ls -la "$esp_dir" 2>/dev/null | awk 'NR<=80 { print "ROOT " $0 }' > "$W/rootls.txt" || true
  while IFS= read -r line; do say "$line"; done < "$W/rootls.txt"
  inventory "$esp_dir/aurora" aurora
  inventory "$esp_dir/trial" trial
  if [ -d "$esp_dir" ] && [ ! -L "$esp_dir" ]; then
    ls -1A "$esp_dir" 2>/dev/null > "$W/rootnames.txt" || true
    while IFS= read -r base; do
      [ -n "$base" ] || continue
      f=$esp_dir/$base
      case "$base" in
        *try*|*spent*|*marker*|*.arm|*.conf) ;;
        *) continue ;;
      esac
      hash_line "$f" "root/$base" 4096
    done < "$W/rootnames.txt"
  fi
  stage2=$esp_dir/aurora/stage2.bin
  if [ -L "$stage2" ]; then fail "aurora/stage2.bin is a symlink; not following"; return 1; fi
  if [ ! -f "$stage2" ]; then fail "aurora/stage2.bin missing"; return 1; fi
  sz=$(file_size "$stage2") || { fail "stage2 size unreadable"; return 1; }
  say "STAGE2_BYTES $sz"
  if [ "$sz" -gt "$STAGE2_MAX" ]; then fail "stage2 larger than 256MiB; not hashing"; return 1; fi
  sum=$(sha256_file "$stage2") || sum=
  if ! valid_sum "$sum"; then fail "stage2 hash failed"; return 1; fi
  say "STAGE2_SHA256 $sum"
  cat=$(catalog "$sum")
  say "STAGE2_CATALOG $cat"
  if [ "$sum" = "$D487" ]; then
    say "STAGE2_EQUALS_D487 yes"
    say "NOTE d487 is the prior default_stage2 / m4.220 fallback. The file hash does not prove the current boot is healthy."
  else
    say "STAGE2_EQUALS_D487 no"
  fi
  if command -v od >/dev/null 2>&1; then
    od -An -tx1 -N 64 "$stage2" 2>/dev/null | awk 'NR<=8 { print "HEADER_HEX" $0 }' > "$W/hex.txt" || true
    while IFS= read -r line; do say "$line"; done < "$W/hex.txt"
    m4=$(od -An -tx1 -N 4 "$stage2" 2>/dev/null | tr -d ' \n')
    m38=$(od -An -tx1 -j 56 -N 4 "$stage2" 2>/dev/null | tr -d ' \n')
    kind=""
    case "$m4" in
      1f8b*) kind="$kind gzip" ;;
    esac
    case "$m4" in
      7f454c46) kind="$kind elf" ;;
    esac
    case "$m4" in
      feedfacf|cffaedfe|feedface|cefaedfe) kind="$kind macho" ;;
    esac
    case "$m38" in
      41524d64) kind="$kind linux-arm64-image" ;;
    esac
    if [ -z "$kind" ]; then kind=" unclassified"; fi
    say "HEADER_KIND${kind}"
  else
    say "HEADER_HEX not-read reason=no-od"
  fi
  if command -v strings >/dev/null 2>&1; then
    strings -n 8 -a "$stage2" 2>/dev/null | awk '
      length($0) > 220 { next }
      $0 ~ /m1n1|aurora|stage2|kver|Linux version|boot-pending|chainload|aura-try|m4\.[0-9]/ {
        if (n < 120) { print; n++ }
      }
    ' > "$W/strings.txt" || true
    if [ -s "$W/strings.txt" ]; then
      while IFS= read -r line; do say "STRING $line"; done < "$W/strings.txt"
    else
      say "STRINGS none-matched"
    fi
  else
    say "STRINGS not-read reason=no-strings"
  fi
  return 0
}

read_nvram() {
  if ! command -v nvram >/dev/null 2>&1; then
    say "NVRAM not-read reason=no-nvram"
    return 0
  fi
  nvram -p > "$W/nvram-full.txt" 2>"$W/nvram.err" || say "NVRAM_READ_FAILED"
  if [ -f "$W/nvram-full.txt" ]; then
    awk -F '\t' '
      $1=="failboot-breadcrumbs" || $1=="boot-image-last" || $1=="panic-count" || $1=="panicmedic" || $1=="auto-boot" || $1=="boot-args" {
        v=$2
        if (length(v) > 200) v=substr(v, 1, 200)
        print $1 "\t" v
      }
    ' "$W/nvram-full.txt" > "$W/nvram-keep.txt" || true
    rm -f "$W/nvram-full.txt"
    if [ -f "$W/nvram-full.txt" ]; then
      fail "nvram full dump still present"
    else
      say "NVRAM_FULL_DELETED"
    fi
    if [ -s "$W/nvram-keep.txt" ]; then
      while IFS= read -r line; do say "NVRAM $line"; done < "$W/nvram-keep.txt"
    else
      say "NVRAM allowlist-empty"
    fi
  fi
}

read_bless() {
  if ! command -v bless >/dev/null 2>&1; then
    say "BLESS not-read reason=no-bless"
    return 0
  fi
  bless --getBoot > "$W/bless-get.txt" 2>&1 || say "BLESS_GET_FAILED"
  awk 'NR<=20 { print "BLESS_GET " $0 }' "$W/bless-get.txt" > "$W/bless-get.out" || true
  while IFS= read -r line; do say "$line"; done < "$W/bless-get.out"
  bless --info > "$W/bless-info.txt" 2>&1 || say "BLESS_INFO_FAILED"
  awk 'NR<=40 { print "BLESS_INFO " $0 }' "$W/bless-info.txt" > "$W/bless-info.out" || true
  while IFS= read -r line; do say "$line"; done < "$W/bless-info.out"
}

read_policy() {
  if [ ! -f "$W/vgids" ]; then say "POLICY not-read reason=no-scan"; return 0; fi
  vn=$(grep -c . "$W/vgids" || true)
  if [ "$vn" != 1 ]; then
    say "POLICY not-read reason=system-volumes-$vn"
    return 0
  fi
  if ! command -v bputil >/dev/null 2>&1; then
    say "POLICY not-read reason=no-bputil"
    return 0
  fi
  vgid=$(head -n 1 "$W/vgids")
  bputil -d -v "$vgid" > "$W/policy.txt" 2>&1 || say "POLICY command-failed"
  awk 'NR<=80 { print "POLICY " $0 }' "$W/policy.txt" > "$W/policy.out" || true
  while IFS= read -r line; do say "$line"; done < "$W/policy.out"
}

cleanup_mount() {
  if [ "${MOUNTED:-0}" != 1 ]; then
    say "UMOUNT_NOT_NEEDED"
    return 0
  fi
  if umount "$esp_dir"; then
    MOUNTED=0
    say "UMOUNT_OK"
    return 0
  fi
  say "UMOUNT_FAILED $esp_dir"
  return 1
}

upload() {
  if [ -z "${H:-}" ]; then
    say "UPLOAD_SKIPPED"
    return 0
  fi
  if ! command -v curl >/dev/null 2>&1; then
    say "UPLOAD_FAILED no-curl"
    return 0
  fi
  if curl -fsS --max-time 30 -T "$REPORT" "$H/up/chaindiag-report.txt"; then
    say "UPLOAD_OK"
    curl -fsS --max-time 30 -T "$REPORT" "$H/up/chaindiag-report.txt" || say "UPLOAD_SECOND_PUT_FAILED"
  else
    say "UPLOAD_FAILED"
  fi
}

main() {
  code=0
  MOUNTED=0
  nmatch=0
  edev=
  esp_dir=
  H=
  if [ $# -ne 1 ]; then
    printf '%s\n' "STOP usage: sh chaindiag /path/to/chaindiag.pins" >&2
    exit 1
  fi
  W=${CHAINDIAG_WORK:-/tmp/m4-chaindiag}
  case "$W" in
    *..*) printf '%s\n' "STOP bad work dir" >&2; exit 1 ;;
    /tmp/*) ;;
    *) printf '%s\n' "STOP work dir must be under /tmp" >&2; exit 1 ;;
  esac
  mkdir -p "$W" || { printf '%s\n' "STOP cannot create work dir" >&2; exit 1; }
  REPORT=$W/chaindiag-report.txt
  : > "$REPORT"
  say "CHAINDIAG 1"
  say "MODE diagnostic-only"
  say "NOT_A_RECOVERY 1"
  say "MUTATIONS none"
  say "CREDENTIALS not-fetched"
  say "USB_DISKS not-enumerated"
  say "COMPATIBLE no"
  say "COMPATIBLE_REASON stage1 1bcb9250 config is AURORA-S1-CFG01 path aurora/stage2.bin with no stage2 digest and no kcbz string; rover-reported boot object b7a557ff is a different file; d487 hash stability is not boot health"
  say "ATTACHED_STAGE1_SHA256 1bcb9250aef56caa8040fc2fc42877131bc3944ee5292e88afc0901ed6172a0c"
  say "OPERATOR_REPORTED_BOOT_OBJECT b7a557ffab7ee74aa6923860400b79dace99b0d43e52d75aabfd53672c56d84e"
  say "BOOT_OBJECT_OBSERVED_BY_THIS_SCRIPT no"
  load_pins "$1" || { say "RESULT incomplete"; upload; exit 1; }
  validate_pins || { say "RESULT incomplete"; upload; exit 1; }
  say "ESP_PIN uuid=$ESP_UUID start=$ESP_START blocks=$ESP_BLOCKS"
  uid=$(id -u 2>/dev/null || echo nope)
  if [ "$uid" != 0 ]; then
    fail "not root; 1TR Terminal is already root"
    say "RESULT incomplete"
    upload
    exit 1
  fi
  if identify; then
    :
  fi
  say "LINUX_SELECTION not-read reason=ext4-not-mounted"
  if [ "$nmatch" = 1 ] && [ "$code" = 0 ]; then
    if mount_esp; then
      collect_esp || true
    fi
  fi
  read_nvram
  read_bless
  read_policy
  cleanup_mount || code=1
  if [ "$code" = 0 ]; then
    say "RESULT evidence-complete diagnostic-only"
  else
    say "RESULT incomplete"
  fi
  say "NEXT Return this report. This script did not install a loader, write stage2, or repartition."
  upload
  exit "$code"
}

if [ "${CHAINDIAG_LIB:-}" != 1 ]; then
  main "$@"
fi
