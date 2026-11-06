#!/bin/sh
# Mocked fail-closed checks for chaindiag. No machine pins. No disk writes.
set -eu
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)
SCRIPT=$HERE/chaindiag.sh
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/log" "$T/fix"
export PATH="$T/bin:/usr/bin:/bin"
export FIX=$T/fix LOG=$T/log
export MOCK_UID=0 MOUNT_FAIL=0 UMOUNT_FAIL=0 CURL_FAIL=0 SHA_FAIL=0
export CHAINDIAG_WORK=$T/work

cat > "$T/bin/id" <<'EOF'
#!/bin/sh
[ "${1:-}" = "-u" ] || exit 99
printf '%s\n' "${MOCK_UID:-0}"
EOF
cat > "$T/bin/diskutil" <<'EOF'
#!/bin/sh
echo "$*" >> "$LOG/diskutil"
case "$1" in
  list)
    [ "${2:-}" = "disk0" ] || exit 99
    cat "$FIX/list.txt"
    ;;
  info)
    [ -f "$FIX/info-$2.txt" ] || { echo "no info $2" >&2; exit 99; }
    cat "$FIX/info-$2.txt"
    ;;
  *) echo "blocked $*" >> "$LOG/diskutil-blocked"; exit 99 ;;
esac
EOF
cat > "$T/bin/mount" <<'EOF'
#!/bin/sh
echo "$*" >> "$LOG/mount-args"
if [ "${1:-}" = "-t" ]; then
  [ "${MOUNT_FAIL:-0}" = 1 ] && exit 1
  mp=
  for a in "$@"; do mp=$a; done
  mkdir -p "$mp"
  cp -a "$FIX/tree/." "$mp/"
  exit 0
fi
if [ -f "$FIX/mount-table" ]; then
  cat "$FIX/mount-table"
fi
exit 0
EOF
cat > "$T/bin/umount" <<'EOF'
#!/bin/sh
echo "$*" >> "$LOG/umount"
[ "${UMOUNT_FAIL:-0}" = 1 ] && exit 1
exit 0
EOF
cat > "$T/bin/curl" <<'EOF'
#!/bin/sh
echo "$*" >> "$LOG/curl"
has_t=0
for a in "$@"; do
  case "$a" in
    -T) has_t=1 ;;
    */c|*/c/|*c.sh) echo "refusing $a" >&2; exit 99 ;;
  esac
done
[ "$has_t" = 1 ] || { echo "curl without -T" >&2; exit 99; }
ok=0
for a in "$@"; do
  case "$a" in
    */up/chaindiag-report.txt) ok=1 ;;
  esac
done
[ "$ok" = 1 ] || { echo "bad url $*" >&2; exit 99; }
[ "${CURL_FAIL:-0}" = 1 ] && exit 22
prev=
for a in "$@"; do
  if [ "$prev" = "-T" ]; then
    cp "$a" "$LOG/uploaded"
  fi
  prev=$a
done
exit 0
EOF
cat > "$T/bin/nvram" <<'EOF'
#!/bin/sh
echo "$*" >> "$LOG/nvram"
[ "${1:-}" = "-p" ] || exit 99
printf '%s\n' "failboot-breadcrumbs	DEADBEEF"
printf '%s\n' "preferred-networks	SECRETWIFI"
printf '%s\n' "boot-args	quiet"
EOF
cat > "$T/bin/bless" <<'EOF'
#!/bin/sh
echo "$*" >> "$LOG/bless"
case "$*" in
  *--setBoot*|*--mount*|*--folder*) echo "blocked bless $*" >&2; exit 99 ;;
esac
case "$1" in
  --getBoot) printf '%s\n' "/dev/disk0s5"; ;;
  --info) printf '%s\n' "finderinfo-mock"; ;;
  *) exit 99 ;;
esac
EOF
cat > "$T/bin/bputil" <<'EOF'
#!/bin/sh
echo "$*" >> "$LOG/bputil"
[ "${1:-}" = "-d" ] && [ "${2:-}" = "-v" ] && [ $# -eq 3 ] || { echo "blocked bputil $*" >&2; exit 99; }
printf '%s\n' "Local policy: display-only"
EOF
cat > "$T/bin/sha256sum" <<'EOF'
#!/bin/sh
if [ "${SHA_FAIL:-0}" = 1 ]; then
  echo "sha256 failed" >&2
  exit 1
fi
exec /usr/bin/sha256sum "$@"
EOF
chmod 755 "$T/bin/"*

fail() { echo "FAIL $*"; exit 1; }
need() {
  if ! grep -q -F -- "$1" "$REPORT"; then
    echo "MISSING [$1]"
    echo '--- report ---'; cat "$REPORT" || true
    echo '--- stderr ---'; cat "$T/stderr" || true
    exit 1
  fi
}
forbid_report() {
  if grep -q -F -- "$1" "$REPORT"; then
    echo "PRESENT [$1]"
    cat "$REPORT"
    exit 1
  fi
}

dash -n "$SCRIPT" || fail "dash -n"
if command -v bash >/dev/null 2>&1; then
  bash -n "$SCRIPT" || fail "bash -n"
fi

code=$(sed 's/#.*//' "$SCRIPT")
printf '%s\n' "$code" | grep -n 'kmutil' && fail "kmutil in code"
printf '%s\n' "$code" | grep -n 'configure-boot' && fail "configure-boot in code"
printf '%s\n' "$code" | grep -n 'reboot' && fail "reboot in code"
printf '%s\n' "$code" | grep -n 'newfs' && fail "newfs in code"
printf '%s\n' "$code" | grep -n 'mkfs' && fail "mkfs in code"
printf '%s\n' "$code" | grep -n -- '--setBoot' && fail "setBoot in code"
printf '%s\n' "$code" | grep -n 'c\.sh' && fail "c.sh in code"
printf '%s\n' "$code" | grep -F '$H/c' && fail 'fetched H/c'
printf '%s\n' "$code" | grep -E '^[[:space:]]*bputil' | grep -v 'bputil -d -v' && fail "bputil not display-only"
printf '%s\n' "$code" | grep -E '^[[:space:]]*curl' | grep -v -- '-T' && fail "curl without -T"
grep -E '5AF9873D-|192\.168\.1\.25' "$SCRIPT" && fail "private pin in script"
echo "static checks ok"

write_stage2() {
  python3 - "$1" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
b = bytearray(b"\x1f\x8b\x08\x00")
b += b"asahi,m1n1-stage2-version=9.9.9-test\n"
b += b"Linux version 7.3.0-m4.220-test\n"
b += b"\0" * 16
p.write_bytes(b)
PY
}

write_infos() {
  mp=$1
  cat > "$FIX/list.txt" <<'EOF'
ignored-prefix disk0s1
container disk0s2
esp disk0s5
linux disk0s6
EOF
  cat > "$FIX/info-disk0s1.txt" <<'EOF'
Device Identifier:        disk0s1
Partition Type:           Apple_APFS_ISC
Partition Offset:         40 Bytes (3 4096-Byte-Device-Blocks)
Disk Size:                16384 Bytes (16384 Bytes)
Mount Point:              Not applicable (not mounted)
EOF
  cat > "$FIX/info-disk0s2.txt" <<'EOF'
Device Identifier:        disk0s2
Volume Name:              Macintosh HD
Name (Role):              Volume (System)
APFS Volume Group:        00000000-0000-0000-0000-000000000000
Partition Offset:         500 Bytes (2 4096-Byte-Device-Blocks)
Disk Size:                8192 Bytes (8192 Bytes)
Mount Point:              Not applicable (not mounted)
EOF
  cat > "$FIX/info-disk0s5.txt" <<EOF
Device Identifier:        disk0s5
Volume Name:              EFI
Partition Type:           EFI
Disk / Partition UUID:    00000000-0000-0000-0000-000000000002
Partition Offset:         409600 Bytes (100 4096-Byte-Device-Blocks)
Disk Size:                32768 Bytes (32768 Bytes)
Mount Point:              $mp
EOF
  cat > "$FIX/info-disk0s6.txt" <<'EOF'
Device Identifier:        disk0s6
Partition Type:           Linux
Disk / Partition UUID:    0FC63DAF-8483-4772-8E79-3D69D8477DE4
Partition Offset:         999 Bytes (1 4096-Byte-Device-Blocks)
Disk Size:                4096 Bytes (4096 Bytes)
Mount Point:              Not applicable (not mounted)
EOF
}

write_pins() {
  cat > "$T/pins" <<'EOF'
# test pins
ESP_UUID=00000000-0000-0000-0000-000000000002
ESP_START=100
ESP_BLOCKS=8
H=http://192.0.2.25:8616
EOF
}

reset_tree() {
  rm -rf "$FIX/tree" "$T/work" "$LOG"
  mkdir -p "$FIX/tree/aurora" "$FIX/tree/trial" "$LOG" "$T/work"
  write_stage2 "$FIX/tree/aurora/stage2.bin"
  printf '%s\n' "aurora-note-visible" > "$FIX/tree/aurora/note.txt"
  printf '%s\n' "trial-bytes" > "$FIX/tree/trial/foo.arm"
  printf '%s\n' "SECRETROOT" > "$FIX/tree/private.txt"
  ln -sf /etc/passwd "$FIX/tree/aurora/evil-link"
  : > "$FIX/mount-table"
  write_infos "Not applicable (not mounted)"
  write_pins
  MOCK_UID=0; MOUNT_FAIL=0; UMOUNT_FAIL=0; CURL_FAIL=0; SHA_FAIL=0
  export MOCK_UID MOUNT_FAIL UMOUNT_FAIL CURL_FAIL SHA_FAIL
}

run_script() {
  set +e
  dash "$SCRIPT" "$T/pins" >"$T/stdout" 2>"$T/stderr"
  echo $? > "$T/ec"
  set -e
  ec=$(cat "$T/ec")
  REPORT=$CHAINDIAG_WORK/chaindiag-report.txt
}

want_ec() {
  if [ "$ec" != "$1" ]; then
    echo "exit $ec want $1"
    echo '--- stderr ---'; cat "$T/stderr" || true
    echo '--- report ---'; cat "$REPORT" 2>/dev/null || true
    exit 1
  fi
}

# catalog labels
got=$(CHAINDIAG_LIB=1 . "$SCRIPT"
  catalog d4877ab1c94d4324d11179dbd5f143cd8454269e61aaa0807dba5a4b8d49b61d)
[ "$got" = "default_stage2.bin prior-m4.220-fallback" ] || fail "catalog d487: $got"
got=$(CHAINDIAG_LIB=1 . "$SCRIPT"
  catalog 850c303096b741049c860b2192943f38f47bb13bdb414800544a1b3786c373ea)
[ "$got" = "bchain-STAGE2_WANT" ] || fail "catalog 850c: $got"
got=$(CHAINDIAG_LIB=1 . "$SCRIPT"; catalog abc)
[ "$got" = "UNLISTED" ] || fail "catalog other: $got"
echo "catalog ok"

# usage
set +e
dash "$SCRIPT" >"$T/stdout" 2>"$T/stderr"
echo $? > "$T/ec"
set -e
ec=$(cat "$T/ec")
[ "$ec" = 1 ] || fail "usage exit $ec"
grep -q 'usage' "$T/stderr" || fail "usage text"
echo "usage ok"

# bad key
reset_tree
printf '%s\n' "ESP_UUID=00000000-0000-0000-0000-000000000002" "PW=nope" > "$T/pins"
run_script
want_ec 1
need "STOP pins key not allowed"
[ ! -s "$LOG/diskutil" ] || fail "diskutil ran on bad pins"
echo "bad key ok"

# placeholder uuid
reset_tree
printf '%s\n' "ESP_UUID=00000000-0000-0000-0000-000000000000" "ESP_START=100" "ESP_BLOCKS=8" > "$T/pins"
run_script
want_ec 1
need "STOP ESP_UUID is the placeholder"
[ ! -s "$LOG/diskutil" ] || fail "diskutil ran on placeholder"
echo "placeholder ok"

# not root
reset_tree
MOCK_UID=1; export MOCK_UID
run_script
want_ec 1
need "STOP not root"
[ ! -s "$LOG/diskutil" ] || fail "diskutil ran when not root"
echo "not root ok"

# happy path
reset_tree
SUM=$(sha256sum "$FIX/tree/aurora/stage2.bin" | awk '{print $1}')
run_script
want_ec 0
need "MODE diagnostic-only"
need "NOT_A_RECOVERY 1"
need "COMPATIBLE no"
need "STAGE2_SHA256 $SUM"
need "STAGE2_CATALOG UNLISTED"
need "STAGE2_EQUALS_D487 no"
need "HEADER_KIND gzip"
need "STRING asahi,m1n1-stage2-version=9.9.9-test"
need "STRING Linux version 7.3.0-m4.220-test"
need "FILE trial/foo.arm"
need "LINUX_PART_HINT disk0s6"
need "LINUX_SELECTION not-read reason=ext4-not-mounted"
need "NVRAM failboot-breadcrumbs	DEADBEEF"
need "NVRAM_FULL_DELETED"
need "BLESS_GET /dev/disk0s5"
need "POLICY Local policy: display-only"
need "UMOUNT_OK"
need "UPLOAD_OK"
need "RESULT evidence-complete diagnostic-only"
need "CREDENTIALS not-fetched"
need "USB_DISKS not-enumerated"
forbid_report "SECRETROOT"
forbid_report "SECRETWIFI"
forbid_report "aurora-note-visible"
forbid_report "root:x:"
grep -q -- '-t' "$LOG/mount-args" || fail "expected ro mount"
grep -E 'erase|unmount|partition|apfs' "$LOG/diskutil" && fail "diskutil mutation"
[ "$(wc -l < "$LOG/bputil")" -eq 1 ] || fail "bputil count"
grep -q -- '-d -v' "$LOG/bputil" || fail "bputil args"
grep -q -- '--setBoot' "$LOG/bless" && fail "bless set"
[ ! -f "$T/work/nvram-full.txt" ] || fail "nvram dump kept"
grep -q 'UPLOAD_OK' "$LOG/uploaded" || fail "upload body"
echo "happy ok"

# uuid mismatch
reset_tree
sed -i 's/00000000-0000-0000-0000-000000000002/00000000-0000-0000-0000-000000000000/' "$FIX/info-disk0s5.txt"
# pin stays ...0002, info becomes placeholder uuid so it does not match
run_script
want_ec 1
need "ESP_MATCHES 0"
need "STOP ESP identity"
[ ! -e "$LOG/mount-args" ] || fail "mounted on mismatch"
echo "mismatch ok"

# two matches
reset_tree
cp "$FIX/info-disk0s5.txt" "$FIX/info-disk0s7.txt"
printf '%s\n' "second disk0s7" >> "$FIX/list.txt"
run_script
want_ec 1
need "ESP_MATCHES 2"
[ ! -e "$LOG/mount-args" ] || fail "mounted on two matches"
echo "two matches ok"

# stage2 missing
reset_tree
rm -f "$FIX/tree/aurora/stage2.bin"
run_script
want_ec 1
need "STOP aurora/stage2.bin missing"
need "FILE trial/foo.arm"
forbid_report "STAGE2_SHA256"
echo "missing stage2 ok"

# hash tool failure
reset_tree
SHA_FAIL=1; export SHA_FAIL
run_script
want_ec 1
need "STOP stage2 hash failed"
forbid_report "STAGE2_SHA256"
echo "hash fail ok"

# mount failure
reset_tree
MOUNT_FAIL=1; export MOUNT_FAIL
run_script
want_ec 1
need "STOP read-only mount failed"
forbid_report "STAGE2_SHA256"
echo "mount fail ok"

# transport failure still keeps the hash
reset_tree
CURL_FAIL=1; export CURL_FAIL
SUM=$(sha256sum "$FIX/tree/aurora/stage2.bin" | awk '{print $1}')
run_script
want_ec 0
need "STAGE2_SHA256 $SUM"
need "UPLOAD_FAILED"
need "RESULT evidence-complete diagnostic-only"
echo "transport fail ok"

# umount failure
reset_tree
UMOUNT_FAIL=1; export UMOUNT_FAIL
SUM=$(sha256sum "$FIX/tree/aurora/stage2.bin" | awk '{print $1}')
run_script
want_ec 1
need "STAGE2_SHA256 $SUM"
need "UMOUNT_FAILED"
echo "umount fail ok"

# already mounted read-write: do not read
reset_tree
mkdir -p "$T/rwesp"
printf '%s\n' "SENTINEL_RW_CONTENT" > "$T/rwesp/sentinel"
write_infos "$T/rwesp"
printf '%s\n' "/dev/disk0s5 on $T/rwesp (msdos, local)" > "$FIX/mount-table"
run_script
want_ec 1
need "not read-only"
forbid_report "SENTINEL_RW_CONTENT"
if [ -f "$LOG/mount-args" ] && grep -q -- '-t' "$LOG/mount-args"; then
  fail "tried to mount over rw"
fi
echo "rw mount ok"

# already mounted read-only
reset_tree
write_infos "$FIX/tree"
printf '%s\n' "/dev/disk0s5 on $FIX/tree (msdos, local, read-only)" > "$FIX/mount-table"
SUM=$(sha256sum "$FIX/tree/aurora/stage2.bin" | awk '{print $1}')
run_script
want_ec 0
need "STAGE2_SHA256 $SUM"
need "ESP_MOUNT existing-ro"
need "UMOUNT_NOT_NEEDED"
if [ -f "$LOG/mount-args" ] && grep -q -- '-t' "$LOG/mount-args"; then
  fail "mounted when already ro"
fi
echo "ro mount ok"

echo "chaindiag tests: all passed"
