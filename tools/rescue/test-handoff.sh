#!/bin/sh
# Prove the packed aarch64 switch_root with a real mount namespace.
# No mock mount and no mock exec.
#
# Needs: python3, zstd, sudo, unshare, overlayfs, qemu-user-static
#   (qemu-aarch64-static) and binfmt_misc. Install on the review box:
#   sudo apt-get install -y zstd qemu-user-static
# The script registers qemu-aarch64 with the F flag if it is missing.
set -eu
IMAGE=${1:?usage: test-handoff.sh MACHO-or-raw}
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)
command -v zstd >/dev/null || { echo "install zstd" >&2; exit 1; }
command -v qemu-aarch64-static >/dev/null || { echo "install qemu-user-static" >&2; exit 1; }
command -v python3 >/dev/null || { echo "install python3" >&2; exit 1; }
sudo -n true || { echo "sudo is required for the mount namespace" >&2; exit 1; }

if ! mountpoint -q /proc/sys/fs/binfmt_misc 2>/dev/null; then
  sudo mount -t binfmt_misc binfmt_misc /proc/sys/fs/binfmt_misc
fi
if [ ! -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ]; then
  printf '%s' ':qemu-aarch64:M::\x7fELF\x02\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x00\x02\x00\xb7\x00:\xff\xff\xff\xff\xff\xff\xff\x00\xff\xff\xff\xff\xff\xff\xff\xff\xfe\xff\xff\xff:/usr/bin/qemu-aarch64-static:F' \
    | sudo tee /proc/sys/fs/binfmt_misc/register >/dev/null
fi

T=$(mktemp -d)
cleanup() {
  if findmnt -R -n "$T" 2>/dev/null | grep -q .; then
    echo "refusing to remove mounted $T" >&2
    return
  fi
  sudo rm -rf "$T"
  rm -f /tmp/NOT-THE-NEW-ROOT
}
trap cleanup EXIT
python3 - "$IMAGE" "$T/sys" << 'PY'
import pathlib, struct, subprocess, sys
image, dest = sys.argv[1:]
blob = pathlib.Path(image).read_bytes()
base = 0
if blob[0x10000 + 0x808:0x10000 + 0x808 + 8] == b"AURALOAD":
    base = 0x10000
size_at = base + 4103735
if blob[size_at + 4:size_at + 10] != b"070701":
    raise SystemExit("initramfs not found in the image")
total = struct.unpack_from("<I", blob, size_at)[0]
early = 234816
mid = blob[size_at + 4 + early:size_at + 4 + total]
dec = subprocess.run(["zstd", "-d", "-c"], input=mid, capture_output=True)
if dec.returncode != 0:
    raise SystemExit("zstd decode failed")
want = {
    "usr/bin/busybox",
    "usr/bin/switch_root",
    "usr/lib/ld-linux-aarch64.so.1",
    "usr/lib/libc.so.6",
    "usr/lib/libcrypt.so.2",
    "usr/lib/libcrypt.so.2.0.0",
}
body = dec.stdout
j = 0
got = set()
while j + 110 <= len(body) and body[j:j + 6] == b"070701":
    ns = int(body[j + 94:j + 102], 16)
    fs = int(body[j + 54:j + 62], 16)
    name = body[j + 110:j + 110 + ns].split(b"\0", 1)[0].decode()
    name_end = 110 + ns
    off = j + name_end + (4 - (name_end % 4)) % 4
    blob = body[off:off + fs]
    if name in want:
        path = pathlib.Path(dest) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name == "usr/lib/libcrypt.so.2":
            path.write_bytes(blob)
            # replaced below if it is a symlink target stored as text
            if fs < 64 and b"\0" not in blob and blob.startswith(b"libcrypt"):
                path.unlink()
                path.symlink_to(blob.split(b"\0", 1)[0].decode())
        else:
            path.write_bytes(blob)
            path.chmod(0o755)
        got.add(name)
    if name == "TRAILER!!!":
        break
    j = off + ((fs + 3) & ~3)
missing = want - got
if missing:
    raise SystemExit("missing from image: %s" % " ".join(sorted(missing)))
print("extracted", len(got))
PY
ln -sfn libcrypt.so.2.0.0 "$T/sys/usr/lib/libcrypt.so.2"

NR=$T/nr
PROOF=$T/proof
mkdir -p "$NR/bin" "$NR/lib" "$NR/usr/lib" "$NR/dev" "$NR/proc" "$NR/sys" "$NR/run" "$NR/proof" "$PROOF"
cp -a "$T/sys/usr/bin/busybox" "$NR/bin/busybox"
ln -s busybox "$NR/bin/sh"
cp -a "$T/sys/usr/lib/ld-linux-aarch64.so.1" "$NR/lib/"
cp -a "$T/sys/usr/lib/ld-linux-aarch64.so.1" "$NR/usr/lib/"
cp -a "$T/sys/usr/lib/libc.so.6" "$NR/usr/lib/"
cp -a "$T/sys/usr/lib/libcrypt.so.2.0.0" "$NR/usr/lib/"
ln -sfn libcrypt.so.2.0.0 "$NR/usr/lib/libcrypt.so.2"
printf '%s\n' SENTINEL > "$NR/MARK"
cat > "$NR/init" << 'EOF'
#!/bin/sh
{
  read -r pid rest < /proc/self/stat
  printf 'PID %s\n' "$pid"
  read -r mark < /MARK
  printf 'MARK %s\n' "$mark"
  if [ -e /tmp/NOT-THE-NEW-ROOT ]; then printf 'LEAK yes\n'; else printf 'LEAK no\n'; fi
  if [ -d /proc/1 ]; then printf 'PROC yes\n'; else printf 'PROC no\n'; fi
  if [ -d /sys/class ]; then printf 'SYS yes\n'; else printf 'SYS no\n'; fi
  if [ -d /dev ]; then printf 'DEV yes\n'; else printf 'DEV no\n'; fi
  if [ -c /dev/console ]; then printf 'CONSOLE char\n'; else printf 'CONSOLE no\n'; fi
  if [ -x /bin/busybox ]; then printf 'BUSYBOX packed\n'; else printf 'BUSYBOX missing\n'; fi
  while read -r src mnt fst rest; do
    case $mnt in
      /proc) printf 'MOUNTPROC %s %s\n' "$src" "$fst" ;;
      /sys) printf 'MOUNTSYS %s %s\n' "$src" "$fst" ;;
      /dev) printf 'MOUNTDEV %s %s\n' "$src" "$fst" ;;
    esac
  done < /proc/mounts
  tr '\0' ' ' < /proc/1/cmdline
  printf '\nARGV %s\n' "$0"
} > /proof/result
exit 0
EOF
chmod 755 "$NR/init"
printf '%s\n' ONLY-OLD-ROOT > /tmp/NOT-THE-NEW-ROOT

INNER=$T/inner.sh
cat > "$INNER" << EOF
#!/bin/bash
set -eu
mount --make-rprivate /
mkdir -p /tmp/libup /tmp/libwork /tmp/usrlibup /tmp/usrlibwork
mount -t overlay overlay -o lowerdir=/lib,upperdir=/tmp/libup,workdir=/tmp/libwork /lib
mount -t overlay overlay -o lowerdir=/usr/lib,upperdir=/tmp/usrlibup,workdir=/tmp/usrlibwork /usr/lib
cp -a "$T/sys/usr/lib/ld-linux-aarch64.so.1" /lib/ld-linux-aarch64.so.1
cp -a "$T/sys/usr/lib/libc.so.6" /usr/lib/libc.so.6
cp -a "$T/sys/usr/lib/libcrypt.so.2.0.0" /usr/lib/libcrypt.so.2.0.0
ln -sfn libcrypt.so.2.0.0 /usr/lib/libcrypt.so.2
cp -a "$T/sys/usr/lib/ld-linux-aarch64.so.1" /usr/lib/ld-linux-aarch64.so.1
mount --bind "$NR" "$NR"
mount --bind "$PROOF" "$NR/proof"
exec "$T/sys/usr/bin/switch_root" "$NR" /init
EOF
chmod 755 "$INNER"
set +e
sudo unshare --mount --pid --fork --mount-proc --propagation private "$INNER" > "$T/unshare.out" 2>&1
rc=$?
set -e
rm -f /tmp/NOT-THE-NEW-ROOT
if [ "$rc" -ne 0 ]; then
  echo "unshare/switch_root exited $rc" >&2
  cat "$T/unshare.out" >&2
  exit 1
fi
result=$PROOF/result
[ -f "$result" ] || { echo "no proof file" >&2; cat "$T/unshare.out" >&2; exit 1; }
cat "$result"
grep -qx 'PID 1' "$result"
grep -qx 'MARK SENTINEL' "$result"
grep -qx 'LEAK no' "$result"
grep -qx 'PROC yes' "$result"
grep -qx 'SYS yes' "$result"
grep -qx 'DEV yes' "$result"
grep -qx 'CONSOLE char' "$result"
grep -qx 'BUSYBOX packed' "$result"
grep -qx 'MOUNTPROC proc proc' "$result"
grep -qx 'MOUNTSYS sysfs sysfs' "$result"
grep -qx 'MOUNTDEV devtmpfs devtmpfs' "$result"
grep -qx 'ARGV /init' "$result"
echo "handoff proof ok"
