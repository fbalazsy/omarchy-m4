#!/bin/sh
# Fail-closed checks for rechain. No machine pins. Install is not reached on a bad ESP or kcbz.
set -eu
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)
SCRIPT=$HERE/rechain.sh
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/log" "$T/fix/tree/aurora" "$T/sysvol"
export PATH="$T/bin:/usr/bin:/bin"
export FIX=$T/fix LOG=$T/log RECHAIN_WORK=$T/work
export KCBZ=0 FAIL_SIDECAR=0

cat > "$T/bin/id" << 'EOF'
#!/bin/sh
[ "${1:-}" = "-u" ] && printf '%s\n' 0 && exit 0
exit 99
EOF
cat > "$T/bin/curl" << 'EOF'
#!/bin/sh
echo "$*" >> "$LOG/curl"
prev= out= url=
for a in "$@"; do
  case "$prev" in -o|-T) out=$a ;; esac
  case "$a" in http://*) url=$a ;; esac
  prev=$a
done
case "$url" in */c|*/c/*|*c.sh) echo "refusing c" >&2; exit 99 ;; esac
if printf '%s\n' "$*" | grep -q -- ' -T '; then cp "$out" "$LOG/uploaded"; exit 0; fi
case "$url" in
  *.sha256)
    if [ "${FAIL_SIDECAR:-0}" = 1 ]; then
      printf '%s\n' "0000000000000000000000000000000000000000000000000000000000000000" > "$out"
    else
      sha256sum "$FIX/loader.bin" | awk '{print $1}' > "$out"
    fi
    ;;
  *) cp "$FIX/loader.bin" "$out" ;;
esac
exit 0
EOF
cat > "$T/bin/diskutil" << 'EOF'
#!/bin/sh
echo "$*" >> "$LOG/diskutil"
case "$1" in
  list) cat "$FIX/list.txt" ;;
  info) cat "$FIX/info-$2.txt" ;;
  apfs) cat "$FIX/apfs.txt" ;;
  mount) echo "mounted $2" >> "$LOG/diskutil-mount"; exit 0 ;;
  *) echo "blocked $*" >> "$LOG/blocked"; exit 99 ;;
esac
EOF
cat > "$T/bin/mount" << 'EOF'
#!/bin/sh
echo "$*" >> "$LOG/mount"
if [ "${1:-}" = "-t" ]; then
  mp=
  for a in "$@"; do mp=$a; done
  mkdir -p "$mp"
  cp -a "$FIX/tree/." "$mp/"
  exit 0
fi
exit 0
EOF
cat > "$T/bin/umount" << 'EOF'
#!/bin/sh
echo "$*" >> "$LOG/umount"
exit 0
EOF
cat > "$T/bin/kmutil" << 'EOF'
#!/bin/sh
echo "$*" >> "$LOG/kmutil"
case "$1" in
  inspect)
    case "${KCBZ:-0}" in
      missing) printf '%s\n' "no boot properties" ;;
      bad) printf '%s\n' "kcbz : 1" ;;
      *) printf '%s\n' "kcbz : 0" ;;
    esac
    ;;
  configure-boot)
    if [ "${2:-}" = "--help" ]; then printf '%s\n' "configure-boot help"; exit 0; fi
    echo "$*" >> "$LOG/configure"
    if [ ! -t 0 ]; then
      printf '%s\n' "By setting a custom boot object, you will be putting your system into Permissive Security. Are you sure you want to do this? (enter y or n) aborting custom boot object installation."
      exit 1
    fi
    printf '%s\n' "Are you sure you want to do this? (enter y or n)"
    IFS= read -r ans || exit 1
    [ "$ans" = y ] || { printf '%s\n' "aborting custom boot object installation."; exit 1; }
    printf '%s\n' "kmutil username:"
    IFS= read -r kmuser || exit 1
    printf '%s\n' "kmutil password:"
    IFS= read -r kmpass || exit 1
    printf '%s\n' "done."
    ;;
  *) exit 99 ;;
esac
EOF
cat > "$T/bin/bputil" << 'EOF'
#!/bin/sh
echo "$*" >> "$LOG/bputil"
case "$1" in
  -nc) printf '%s\n' "local policy created" ;;
  -d) printf '%s\n' "Permissive" ;;
  *) echo "blocked bputil $*" >&2; exit 99 ;;
esac
EOF
cat > "$T/bin/bless" << 'EOF'
#!/bin/sh
echo "$*" >> "$LOG/bless"
case "$*" in *--setBoot*|*--mount*) exit 99 ;; esac
[ "${1:-}" = "--getBoot" ] && printf '%s\n' "/dev/disk5s3" && exit 0
exit 99
EOF
cat > "$T/bin/sha256sum" << 'EOF'
#!/bin/sh
exec /usr/bin/sha256sum "$@"
EOF
chmod 755 "$T/bin/"*

fail() { echo "FAIL $*"; exit 1; }
dash -n "$SCRIPT"
printf '%s\n' "$(sed 's/#.*//' "$SCRIPT")" | grep -E '^[[:space:]]*reboot' && fail reboot
grep -F '$H/c' "$SCRIPT" && fail 'fetches c'
grep -E '5AF9873D-|192\.168\.1\.25' "$SCRIPT" && fail "private pin in script"
echo "static ok"

printf '%s\n' 'loader-bytes' > "$FIX/loader.bin"
printf '%s\n' 'stage2-bytes' > "$FIX/tree/aurora/stage2.bin"
printf '%s\n' '0123456789abcdef0123456789ab' > "$FIX/tree/aura-try"
S2=$(sha256sum "$FIX/tree/aurora/stage2.bin" | awk '{print $1}')
S2B=$(wc -c < "$FIX/tree/aurora/stage2.bin" | tr -d ' ')
LD=$(sha256sum "$FIX/loader.bin" | awk '{print $1}')
cat > "$FIX/list.txt" << 'EOF'
a disk0s2
b disk0s5
EOF
cat > "$FIX/info-disk0s2.txt" << 'EOF'
Partition Offset: 1 Bytes (2 4096-Byte-Device-Blocks)
APFS Container: disk5
Mount Point: Not applicable (not mounted)
EOF
cat > "$FIX/info-disk0s5.txt" << 'EOF'
Disk / Partition UUID: 00000000-0000-0000-0000-000000000002
Partition Offset: 409600 Bytes (100 4096-Byte-Device-Blocks)
Disk Size: 32768 Bytes (32768 Bytes)
Mount Point: Not applicable (not mounted)
EOF
cat > "$FIX/info-disk5s1.txt" << EOF
APFS Volume Group: 00000000-0000-0000-0000-000000000000
Mount Point: $T/sysvol
EOF
cat > "$FIX/apfs.txt" << 'EOF'
APFS Volume Disk (Role): disk5s1 (System)
EOF
write_pins() {
  cat > "$T/pins" << EOF
H=http://192.0.2.25:8616
LOADER=bchain.macho
LOADER_SHA=$LD
ESP_UUID=00000000-0000-0000-0000-000000000002
ESP_START=100
ESP_BLOCKS=8
STAGE2_SHA=$S2
STAGE2_BYTES=$S2B
STORE_START=2
EOF
}
reset_logs() {
  rm -rf "$LOG" "$T/work"
  mkdir -p "$LOG" "$T/work"
  KCBZ=0; FAIL_SIDECAR=0
  export KCBZ FAIL_SIDECAR
}
runp() {
  reset_logs
  write_pins
  set +e
  dash "$SCRIPT" "$T/pins" >"$T/out" 2>"$T/err"
  echo $? > "$T/ec"
  set -e
  ec=$(cat "$T/ec")
  STATUS=$RECHAIN_WORK/status.txt
}
need() { grep -q -F -- "$1" "$STATUS" || { echo "MISSING $1"; cat "$STATUS" "$T/err"; exit 1; }; }
want() { [ "$ec" = "$1" ] || { echo "exit $ec want $1"; cat "$T/err" "$STATUS"; exit 1; }; }

# bad stage2 does not install
printf '%s\n' 'other-stage2' > "$FIX/tree/aurora/stage2.bin"
runp
want 1
need "STOP stage2 is not the pinned file"
[ ! -e "$LOG/configure" ] || fail "configure-boot after bad stage2"
[ ! -s "$LOG/bputil" ] || fail "bputil after bad stage2"
echo "bad stage2 ok"
printf '%s\n' 'stage2-bytes' > "$FIX/tree/aurora/stage2.bin"
S2=$(sha256sum "$FIX/tree/aurora/stage2.bin" | awk '{print $1}')
S2B=$(wc -c < "$FIX/tree/aurora/stage2.bin" | tr -d ' ')

# identity mismatch
runp
# overwrite info after pins written inside runp — do it by a dedicated run
reset_logs
write_pins
sed -i 's/00000000-0000-0000-0000-000000000002/00000000-0000-0000-0000-000000000000/' "$FIX/info-disk0s5.txt"
set +e
dash "$SCRIPT" "$T/pins" >"$T/out" 2>"$T/err"; echo $? > "$T/ec"; set -e
ec=$(cat "$T/ec"); STATUS=$RECHAIN_WORK/status.txt
want 1
need "STOP ESP identity"
[ ! -e "$LOG/mount" ] || fail "mounted on bad identity"
echo "bad identity ok"
sed -i 's/00000000-0000-0000-0000-000000000000/00000000-0000-0000-0000-000000000002/' "$FIX/info-disk0s5.txt"

# kcbz not zero
reset_logs; write_pins; KCBZ=bad; export KCBZ
printf '%s\n' y morpheus s3cret-test > "$T/answers"
set +e
RECHAIN_ANSWERS=$T/answers dash "$SCRIPT" "$T/pins" >"$T/out" 2>"$T/err"; echo $? > "$T/ec"; set -e
ec=$(cat "$T/ec"); STATUS=$RECHAIN_WORK/status.txt
want 1
need "STOP kcbz is not 0"
[ ! -e "$LOG/configure" ] || fail "configure after bad kcbz"
[ ! -s "$LOG/bputil" ] || fail "bputil after bad kcbz"
echo "bad kcbz ok"

# kcbz absent
reset_logs; write_pins; KCBZ=missing; export KCBZ
set +e
dash "$SCRIPT" "$T/pins" >"$T/out" 2>"$T/err"; echo $? > "$T/ec"; set -e
ec=$(cat "$T/ec"); STATUS=$RECHAIN_WORK/status.txt
want 1
need "STOP kcbz not reported"
echo "missing kcbz ok"

# declined confirm
reset_logs; write_pins; KCBZ=0; export KCBZ
printf '%s\n' n > "$T/answers"
set +e
RECHAIN_ANSWERS=$T/answers dash "$SCRIPT" "$T/pins" >"$T/out" 2>"$T/err"; echo $? > "$T/ec"; set -e
ec=$(cat "$T/ec"); STATUS=$RECHAIN_WORK/status.txt
want 1
need "STOP declined"
[ ! -s "$LOG/bputil" ] || fail "bputil after decline"
echo "decline ok"

# The Recovery fallback: stdin is not a terminal. A piped "y" still aborts.
reset_logs
set +e
printf '%s\n' y | "$T/bin/kmutil" configure-boot -c x -v y > "$T/piped-y.txt" 2>&1
pec=$?
: | "$T/bin/kmutil" configure-boot -c x -v y > "$T/piped-eof.txt" 2>&1
eec=$?
set -e
[ "$pec" != 0 ] && [ "$eec" != 0 ] || fail "non-tty kmutil should abort"
grep -q 'aborting custom boot object installation.' "$T/piped-y.txt" || fail "piped y did not abort"
grep -q 'aborting custom boot object installation.' "$T/piped-eof.txt" || fail "eof stdin did not abort"
echo "nontty abort ok"

# curl|sh: script stdin is exhausted; kmutil must be answered on the terminal.
reset_logs; write_pins
printf '%s\n' y morpheus s3cret-test > "$T/answers"
python3 - "$SCRIPT" "$T/pins" "$T/answers" "$RECHAIN_WORK" << 'PY'
import fcntl, os, select, subprocess, sys, termios, time
script, pins, answers, work = sys.argv[1:5]
master, slave = os.openpty()
env = os.environ.copy()
env["RECHAIN_ANSWERS"] = answers
env["RECHAIN_WORK"] = work
def preexec():
    os.setsid()
    fcntl.ioctl(1, termios.TIOCSCTTY, 0)
proc = subprocess.Popen(
    ["dash", script, pins],
    stdin=subprocess.DEVNULL,
    stdout=slave,
    stderr=slave,
    close_fds=True,
    preexec_fn=preexec,
    env=env,
)
os.close(slave)
buf = b""
sent = set()
deadline = time.time() + 8
while time.time() < deadline:
    r, _, _ = select.select([master], [], [], 0.2)
    if r:
        try:
            chunk = os.read(master, 4096)
        except OSError:
            chunk = b""
        if not chunk:
            break
        buf += chunk
        if b"(enter y or n)" in buf and "y" not in sent:
            os.write(master, b"y\n"); sent.add("y")
        if b"kmutil username:" in buf and "u" not in sent:
            os.write(master, b"morpheus\n"); sent.add("u")
        if b"kmutil password:" in buf and "p" not in sent:
            os.write(master, b"s3cret-test\n"); sent.add("p")
    if proc.poll() is not None:
        break
else:
    proc.kill()
    sys.stderr.buffer.write(buf)
    sys.exit("timed out; sent %s" % ",".join(sorted(sent)))
rc = proc.wait(timeout=5)
os.close(master)
sys.stderr.buffer.write(buf)
sys.exit(rc)
PY
ec=$?
STATUS=$RECHAIN_WORK/status.txt
want 0
need "RECHAIN_DONE"
need "KMUTIL_EXIT 0"
grep -q 'configure-boot -c' "$LOG/configure" || { echo "no configure"; cat "$LOG/kmutil"; exit 1; }
grep -q -- '-p' "$LOG/configure" && fail "password passed on kmutil argv"
grep -q s3cret-test "$STATUS" && fail "password in status"
grep -q s3cret-test "$LOG/uploaded" && fail "password uploaded"
grep -q s3cret-test "$RECHAIN_WORK/kmutil.txt" && fail "password in kmutil log"
grep -q -- '--setBoot' "$LOG/bless" && fail "setBoot"
echo "tty configure ok"
echo "rechain tests: all passed"
