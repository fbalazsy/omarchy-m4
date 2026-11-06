#!/bin/sh
# Offline checks for the rescue stage2 and the fail-closed installer.
set -eu
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)
TRIAL=${TRIAL:-/tmp/trial-pack/stage2-m4222-g55obs2.bin}
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
python3 "$HERE/build-rescue-stage2.py" "$TRIAL" "$T/rescue-stage2.bin"
python3 - "$T/rescue-stage2.bin" << 'PY'
import gzip, hashlib, io, pathlib, struct, subprocess, sys
b = pathlib.Path(sys.argv[1]).read_bytes()
size_at = 4103735
total = struct.unpack_from("<I", b, size_at)[0]
early_len = 234816
assert b[size_at + 4:size_at + 10] == b"070701"
suffix = b[size_at + 4 + total:]
assert suffix.startswith(b"chosen.bootargs=")
assert b"rover-init" not in suffix
assert b"init=/usr/bin/ash" in suffix
early = b[size_at + 4:size_at + 4 + early_len]
assert b"7.3.0-m4.222-g55obs2/modules.dep" in early
assert b"nvme-apple.ko.zst" in early
mid = b[size_at + 4 + early_len:size_at + 4 + total]
dec = subprocess.run(["zstd", "-d", "-c"], input=mid, capture_output=True)
assert dec.returncode == 0, dec.stderr
body = dec.stdout
j = 0
init = ash = kmod = None
while j + 110 <= len(body) and body[j:j + 6] == b"070701":
    ns = int(body[j + 94:j + 102], 16)
    fs = int(body[j + 54:j + 62], 16)
    name = body[j + 110:j + 110 + ns].split(b"\0", 1)[0].decode()
    name_end = 110 + ns
    off = j + name_end + (4 - (name_end % 4)) % 4
    blob = body[off:off + fs]
    if name == "init":
        init = blob
    elif name == "usr/bin/ash":
        ash = blob
    elif name == "usr/bin/kmod":
        kmod = blob[:4]
    if name == "TRAILER!!!":
        break
    j = off + ((fs + 3) & ~3)
text = init.decode()
assert text.startswith("#!/usr/bin/ash\n")
assert "switch_root" not in text
assert "ro,noload" in text
assert "rover-init" in text  # inspection path only
assert "/usr/bin/switch_root" not in text
assert ash == b"busybox"
assert kmod is not None
g = suffix.find(b"\x1f\x8b\x08")
img = gzip.GzipFile(fileobj=io.BytesIO(suffix[g:])).read(80)
assert img[56:60] == b"ARMd"
print("image ok", hashlib.sha256(b).hexdigest(), len(b))
PY

SCRIPT=$HERE/rescue.sh
dash -n "$SCRIPT"
grep -F '$H/c' "$SCRIPT" && exit 1
sed 's/#.*//' "$SCRIPT" | grep -n -- '--raw' && exit 1
grep -E '5AF9873D-|192\.168\.1\.25' "$SCRIPT" && exit 1
echo "static ok"

mkdir -p "$T/bin" "$T/log" "$T/fix/tree/aurora" "$T/sysvol"
export PATH="$T/bin:/usr/bin:/bin" FIX=$T/fix LOG=$T/log RESCUE_WORK=$T/work KCBZ=0
printf '%s\n' 'loader-bytes' > "$FIX/loader.bin"
printf '%s\n' 'old-stage2-bytes' > "$FIX/tree/aurora/stage2.bin"
printf '%s\n' 'trial-left-alone' > "$FIX/tree/aurora/stage2-m4222.bin"
printf '%s\n' 'rescue-bytes' > "$FIX/rescue.bin"
LD=$(sha256sum "$FIX/loader.bin" | awk '{print $1}')
OLD=$(sha256sum "$FIX/tree/aurora/stage2.bin" | awk '{print $1}')
RS=$(sha256sum "$FIX/rescue.bin" | awk '{print $1}')
OLDB=$(wc -c < "$FIX/tree/aurora/stage2.bin" | tr -d ' ')
RSB=$(wc -c < "$FIX/rescue.bin" | tr -d ' ')
cat > "$T/bin/id" << 'EOF'
#!/bin/sh
[ "${1:-}" = "-u" ] && echo 0 && exit 0
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
case "$url" in */c|*/c/*) echo refuse >&2; exit 99 ;; esac
printf '%s\n' "$*" | grep -q ' -T ' && cp "$out" "$LOG/uploaded" && exit 0
case "$url" in
  *loader*.sha256|*bchain.macho.sha256) sha256sum "$FIX/loader.bin" | awk '{print $1}' > "$out" ;;
  *stage2.bin.sha256|*rescue-stage2.bin.sha256) sha256sum "$FIX/rescue.bin" | awk '{print $1}' > "$out" ;;
  *bchain.macho) cp "$FIX/loader.bin" "$out" ;;
  *) cp "$FIX/rescue.bin" "$out" ;;
esac
exit 0
EOF
cat > "$T/bin/diskutil" << 'EOF'
#!/bin/sh
echo "$*" >> "$LOG/diskutil"
case "$1" in
  list) printf '%s\n' "a disk0s2" "b disk0s5" ;;
  info)
    case "$2" in
      disk0s5) cat "$FIX/esp-info" ;;
      disk0s2) printf '%s\n' "Partition Offset: 1 Bytes (2 4096-Byte-Device-Blocks)" "APFS Container: disk5" ;;
      disk5s1) printf '%s\n' "APFS Volume Group: 00000000-0000-0000-0000-000000000000" "Mount Point: $FIX/../sysvol" ;;
      *) exit 99 ;;
    esac ;;
  apfs) printf '%s\n' "APFS Volume Disk (Role): disk5s1 (System)" ;;
  mount) exit 0 ;;
  *) echo "blocked $*" >> "$LOG/blocked"; exit 99 ;;
esac
EOF
# fix disk5s1 mount point after T is known
printf '%s\n' "APFS Volume Group: 00000000-0000-0000-0000-000000000000" "Mount Point: $T/sysvol" > "$T/bin/info-sys"
cat > "$T/bin/diskutil" << EOF
#!/bin/sh
echo "\$*" >> "\$LOG/diskutil"
case "\$1" in
  list) printf '%s\n' "a disk0s2" "b disk0s5" ;;
  info)
    case "\$2" in
      disk0s5) cat "\$FIX/esp-info" ;;
      disk0s2) printf '%s\n' "Partition Offset: 1 Bytes (2 4096-Byte-Device-Blocks)" "APFS Container: disk5" ;;
      disk5s1) printf '%s\n' "APFS Volume Group: 00000000-0000-0000-0000-000000000000" "Mount Point: $T/sysvol" ;;
      *) exit 99 ;;
    esac ;;
  apfs) printf '%s\n' "APFS Volume Disk (Role): disk5s1 (System)" ;;
  mount) exit 0 ;;
  *) echo "blocked \$*" >> "\$LOG/blocked"; exit 99 ;;
esac
EOF
cat > "$FIX/esp-info" << 'EOF'
Disk / Partition UUID: 00000000-0000-0000-0000-000000000002
Partition Offset: 409600 Bytes (100 4096-Byte-Device-Blocks)
Disk Size: 32768 Bytes (32768 Bytes)
Mount Point: Not applicable (not mounted)
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
exit 0
EOF
cat > "$T/bin/df" << 'EOF'
#!/bin/sh
echo "Filesystem 1024-blocks Used Available Capacity Mounted"
echo "esp 500000 1000 400000 1% $2"
EOF
cat > "$T/bin/kmutil" << 'EOF'
#!/bin/sh
echo "$*" >> "$LOG/kmutil"
case "$1" in
  inspect)
    case "${KCBZ:-0}" in
      bad) echo "kcbz : 1" ;;
      missing) echo "no property" ;;
      *) echo "kcbz : 0" ;;
    esac ;;
  configure-boot)
    echo "$*" >> "$LOG/configure"
    if [ ! -t 0 ]; then
      echo "aborting custom boot object installation."
      exit 1
    fi
    echo "Are you sure you want to do this? (enter y or n)"
    IFS= read -r ans || exit 1
    [ "$ans" = y ] || { echo aborting; exit 1; }
    echo done.
    ;;
  *) exit 99 ;;
esac
EOF
cat > "$T/bin/bputil" << 'EOF'
#!/bin/sh
echo "$*" >> "$LOG/bputil"
case "$1" in
  -nc) echo ok ;;
  -d) echo Permissive ;;
  *) exit 99 ;;
esac
EOF
cat > "$T/bin/bless" << 'EOF'
#!/bin/sh
echo "$*" >> "$LOG/bless"
[ "${1:-}" = "--getBoot" ] && echo /dev/disk5s3 && exit 0
exit 99
EOF
cat > "$T/bin/sha256sum" << 'EOF'
#!/bin/sh
exec /usr/bin/sha256sum "$@"
EOF
chmod 755 "$T/bin/"*
write_pins() {
  cat > "$T/pins" << EOF
H=http://192.0.2.25:8616
LOADER=bchain.macho
LOADER_SHA=$LD
STAGE2=rescue-stage2.bin
STAGE2_SHA=$RS
STAGE2_BYTES=$RSB
ESP_UUID=00000000-0000-0000-0000-000000000002
ESP_START=100
ESP_BLOCKS=8
OLD_STAGE2_SHA=$OLD
OLD_STAGE2_BYTES=$OLDB
BACKUP=aurora/stage2-d487.bin
STORE_START=2
EOF
}
reset_logs() { rm -rf "$LOG" "$T/work"; mkdir -p "$LOG" "$T/work"; }
fail() { echo "FAIL $*"; exit 1; }

# wrong current stage2
reset_logs; write_pins
printf '%s\n' 'not-the-old' > "$FIX/tree/aurora/stage2.bin"
set +e; dash "$SCRIPT" "$T/pins" >"$T/out" 2>"$T/err"; echo $? > "$T/ec"; set -e
[ "$(cat "$T/ec")" = 1 ] || fail "expected stop"
grep -q 'neither d487 nor the rescue' "$T/work/status.txt" || { cat "$T/work/status.txt" "$T/err"; fail mismatch; }
[ ! -e "$LOG/configure" ] || fail "kmutil ran"
echo "bad stage2 ok"
printf '%s\n' 'old-stage2-bytes' > "$FIX/tree/aurora/stage2.bin"

# kcbz
reset_logs; write_pins; KCBZ=bad; export KCBZ
set +e; dash "$SCRIPT" "$T/pins" >"$T/out" 2>"$T/err"; echo $? > "$T/ec"; set -e
grep -q 'kcbz is not 0' "$T/work/status.txt" || { cat "$T/work/status.txt"; fail kcbz; }
[ ! -e "$LOG/configure" ] || fail "kmutil after bad kcbz"
echo "bad kcbz ok"
KCBZ=0; export KCBZ

# decline
reset_logs; write_pins
printf '%s\n' n > "$T/answers"
set +e; RESCUE_ANSWERS=$T/answers dash "$SCRIPT" "$T/pins" >"$T/out" 2>"$T/err"; echo $? > "$T/ec"; set -e
grep -q 'declined' "$T/work/status.txt" || fail decline
[ ! -s "$LOG/bputil" ] || fail "bputil after decline"
echo "decline ok"

# nontty kmutil aborts
reset_logs
set +e
printf '%s\n' y | "$T/bin/kmutil" configure-boot -c x -v y > "$T/piped.txt"
[ "$?" != 0 ] || fail "pipe should abort"
grep -q aborting "$T/piped.txt" || fail "no abort text"
set -e
echo "nontty ok"

# success through a terminal, stdin exhausted
reset_logs; write_pins
printf '%s\n' y morpheus s3cret-test y > "$T/answers"
python3 - "$SCRIPT" "$T/pins" "$T/answers" "$RESCUE_WORK" << 'PY'
import fcntl, os, select, subprocess, sys, termios, time
script, pins, answers, work = sys.argv[1:5]
master, slave = os.openpty()
env = os.environ.copy()
env["RESCUE_ANSWERS"] = answers
env["RESCUE_WORK"] = work
def preexec():
    os.setsid()
    fcntl.ioctl(1, termios.TIOCSCTTY, 0)
proc = subprocess.Popen(["dash", script, pins], stdin=subprocess.DEVNULL, stdout=slave, stderr=slave,
                        close_fds=True, preexec_fn=preexec, env=env)
os.close(slave)
buf = b""; sent=False
deadline=time.time()+8
while time.time()<deadline:
    r,_,_=select.select([master],[],[],0.2)
    if r:
        try:
            chunk=os.read(master,4096)
        except OSError:
            chunk=b""
        if not chunk: break
        buf += chunk
        if b"(enter y or n)" in buf or b"Are you sure" in buf:
            if not sent:
                os.write(master, b"y\n"); sent=True
    if proc.poll() is not None:
        break
else:
    proc.kill(); sys.exit("timeout")
rc=proc.wait(timeout=5)
os.close(master)
sys.stderr.buffer.write(buf)
sys.exit(rc)
PY
grep -q 'RESCUE_DONE' "$T/work/status.txt" || { echo FAIL success; cat "$T/work/status.txt" "$T/err"; exit 1; }
grep -q 'configure-boot -c' "$LOG/configure" || fail "no configure"
grep -q -- '--raw' "$LOG/configure" && fail "raw kmutil"
grep -q s3cret-test "$T/work/status.txt" && fail "password in status"
# stage2 replaced inside the last mount; trial file must survive in the source tree
grep -q 'trial-left-alone' "$FIX/tree/aurora/stage2-m4222.bin" || fail "trial source changed"
echo "success ok"
echo "rescue tests: all passed"
