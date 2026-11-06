#!/bin/sh
set -eu
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)
TRIAL=${TRIAL:-/tmp/trial-pack/stage2-m4222-g55obs2.bin}
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
python3 "$HERE/build-rescue-stage2.py" "$TRIAL" "$T/rescue.bin" >/tmp/rescue-v2-build.txt
python3 - "$T/rescue.bin" "$T/rescue.macho" << 'PY'
import gzip, io, pathlib, struct, subprocess, sys
raw = pathlib.Path(sys.argv[1]).read_bytes()
macho = pathlib.Path(sys.argv[2]).read_bytes()
assert macho[:4] == b"\xcf\xfa\xed\xfe"
assert b"__TEXT_BOOT_EXEC" not in macho
assert macho[0x10000 + 0x808:0x10000 + 0x810] == b"AURALOAD"
assert raw[0x808:0x810] == b"AURALOAD"
size_at = 4103735
total = struct.unpack_from("<I", raw, size_at)[0]
assert raw[size_at:size_at + 14] != b"aura_initramfs"  # size sits after the magic
assert raw[size_at - 14:size_at] == b"aura_initramfs"
suffix = raw[size_at + 4 + total:]
assert suffix.startswith(b"chosen.bootargs=")
assert b"rover-init" not in suffix
assert b"init=/usr/bin/ash" in suffix
assert b"hib=1" in suffix
early = raw[size_at + 4:size_at + 4 + 234816]
assert b"7.3.0-m4.222-g55obs2/modules.dep" in early
assert b"nvme-apple.ko.zst" in early
mid = raw[size_at + 4 + 234816:size_at + 4 + total]
body = subprocess.run(["zstd", "-d", "-c"], input=mid, capture_output=True)
assert body.returncode == 0
buf = body.stdout
j = 0
while j + 110 <= len(buf) and buf[j:j + 6] == b"070701":
    ns = int(buf[j + 94:j + 102], 16)
    fs = int(buf[j + 54:j + 62], 16)
    name = buf[j + 110:j + 110 + ns].split(b"\0", 1)[0].decode()
    name_end = 110 + ns
    off = j + name_end + (4 - (name_end % 4)) % 4
    blob = buf[off:off + fs]
    if name == "init":
        text = blob.decode()
        assert text.startswith("#!/usr/bin/ash\n")
        assert "switch_root" not in text
        assert "ro,noload" in text
    elif name == "usr/bin/ash":
        assert blob == b"busybox"
    if name == "TRAILER!!!":
        break
    j = off + ((fs + 3) & ~3)
g = suffix.find(b"\x1f\x8b\x08")
img = gzip.GzipFile(fileobj=io.BytesIO(suffix[g:])).read(80)
assert img[56:60] == b"ARMd"
print("layout ok")
PY

SCRIPT=$HERE/enroll-v2.sh
dash -n "$SCRIPT"
sed 's/#.*//' "$SCRIPT" | grep -n -- '--raw' && exit 1
grep -F '$H/c' "$SCRIPT" && exit 1
grep -E '5AF9873D-|192\.168\.1\.25' "$SCRIPT" && exit 1
echo "static ok"

mkdir -p "$T/bin" "$T/log" "$T/sysvol"
export PATH="$T/bin:/usr/bin:/bin" LOG=$T/log RESCUE_WORK=$T/work KCBZ=0
printf '%s\n' 'macho-bytes' > "$T/boot.macho"
BOOT=$(sha256sum "$T/boot.macho" | awk '{print $1}')
BOOTB=$(wc -c < "$T/boot.macho" | tr -d ' ')
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
  *.sha256) sha256sum "$RESCUE_FIXTURE" | awk '{print $1}' > "$out" ;;
  *) cp "$RESCUE_FIXTURE" "$out" ;;
esac
exit 0
EOF
export RESCUE_FIXTURE=$T/boot.macho
cat > "$T/bin/diskutil" << EOF
#!/bin/sh
echo "\$*" >> "\$LOG/diskutil"
case "\$1" in
  list) printf '%s\n' "a disk0s2" "b disk0s5" ;;
  info)
    case "\$2" in
      disk0s5)
        if [ "\${BAD_ESP:-0}" = 1 ]; then echo "no match"; exit 0; fi
        printf '%s\n' "Disk / Partition UUID: 00000000-0000-0000-0000-000000000002" "Partition Offset: 409600 Bytes (100 4096-Byte-Device-Blocks)" "Disk Size: 32768 Bytes (32768 Bytes)"
        ;;
      disk0s2) printf '%s\n' "Partition Offset: 1 Bytes (2 4096-Byte-Device-Blocks)" "APFS Container: disk5" ;;
      disk5s1) printf '%s\n' "APFS Volume Group: 00000000-0000-0000-0000-000000000000" "Mount Point: $T/sysvol" ;;
      *) exit 99 ;;
    esac ;;
  apfs) printf '%s\n' "APFS Volume Disk (Role): disk5s1 (System)" ;;
  mount) exit 0 ;;
  *) echo blocked >> "\$LOG/blocked"; exit 99 ;;
esac
EOF
cat > "$T/bin/kmutil" << 'EOF'
#!/bin/sh
echo "$*" >> "$LOG/kmutil"
case "$1" in
  inspect)
    case "${KCBZ:-0}" in
      bad) echo "kcbz : 1" ;;
      *) echo "kcbz : 0" ;;
    esac ;;
  configure-boot)
    echo "$*" >> "$LOG/configure"
    if [ ! -t 0 ]; then echo "aborting custom boot object installation."; exit 1; fi
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
case "$1" in -nc) echo ok ;; -d) echo Permissive ;; *) exit 99 ;; esac
EOF
cat > "$T/bin/sha256sum" << 'EOF'
#!/bin/sh
exec /usr/bin/sha256sum "$@"
EOF
chmod 755 "$T/bin/"*
cat > "$T/pins" << EOF
H=http://192.0.2.25:8616
BOOT=rescue-v2.macho
BOOT_SHA=$BOOT
BOOT_BYTES=$BOOTB
ESP_UUID=00000000-0000-0000-0000-000000000002
ESP_START=100
ESP_BLOCKS=8
STORE_START=2
EOF
reset_logs() { rm -rf "$LOG" "$T/work"; mkdir -p "$LOG" "$T/work"; }
fail() { echo "FAIL $*"; exit 1; }

reset_logs; BAD_ESP=1; export BAD_ESP
set +e; dash "$SCRIPT" "$T/pins" >"$T/out" 2>"$T/err"; echo $? >"$T/ec"; set -e
grep -q 'ESP identity' "$T/work/status.txt" || { cat "$T/work/status.txt"; fail esp; }
[ ! -e "$LOG/configure" ] || fail "enrolled on bad esp"
echo "bad esp ok"
unset BAD_ESP

reset_logs; KCBZ=bad; export KCBZ
set +e; dash "$SCRIPT" "$T/pins" >"$T/out" 2>"$T/err"; echo $? >"$T/ec"; set -e
grep -q 'kcbz is not 0' "$T/work/status.txt" || fail kcbz
[ ! -e "$LOG/configure" ] || fail "enrolled on bad kcbz"
echo "bad kcbz ok"
KCBZ=0; export KCBZ

reset_logs
printf '%s\n' n > "$T/answers"
set +e; RESCUE_ANSWERS=$T/answers dash "$SCRIPT" "$T/pins" >"$T/out" 2>"$T/err"; echo $? >"$T/ec"; set -e
grep -q declined "$T/work/status.txt" || fail decline
[ ! -s "$LOG/bputil" ] || fail "policy changed after decline"
echo "decline ok"

reset_logs
printf '%s\n' y | "$T/bin/kmutil" configure-boot -c x -v y >"$T/piped.txt" || true
grep -q aborting "$T/piped.txt" || fail nontty
echo "nontty ok"

reset_logs
printf '%s\n' y morpheus s3cret-test > "$T/answers"
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
proc = subprocess.Popen(["dash", script, pins], stdin=subprocess.DEVNULL, stdout=slave, stderr=slave, close_fds=True, preexec_fn=preexec, env=env)
os.close(slave)
buf=b""; sent=False
deadline=time.time()+8
while time.time()<deadline:
    r,_,_=select.select([master],[],[],0.2)
    if r:
        try: chunk=os.read(master,4096)
        except OSError: chunk=b""
        if not chunk: break
        buf += chunk
        if (b"(enter y or n)" in buf or b"Are you sure" in buf) and not sent:
            os.write(master, b"y\n"); sent=True
    if proc.poll() is not None: break
else:
    proc.kill(); sys.exit("timeout")
rc=proc.wait(timeout=5)
os.close(master)
sys.stderr.buffer.write(buf)
sys.exit(rc)
PY
grep -q RESCUE_V2_DONE "$T/work/status.txt" || { cat "$T/work/status.txt"; fail success; }
grep -q 'configure-boot -c' "$LOG/configure" || fail "no configure"
grep -q -- '--raw' "$LOG/configure" && fail raw
grep -q s3cret-test "$T/work/status.txt" && fail "password logged"
echo "enroll-v2 tests: all passed"
