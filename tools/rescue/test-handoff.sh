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
mkdir -p "$NR/run" "$NR/tmp"
printf '%s\n' NEW-RUN > "$NR/run/from-new-root"
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
  runmount=no
  while read -r src mnt fst rest; do
    case $mnt in
      /proc) printf 'MOUNTPROC %s %s\n' "$src" "$fst" ;;
      /sys) printf 'MOUNTSYS %s %s\n' "$src" "$fst" ;;
      /dev) printf 'MOUNTDEV %s %s\n' "$src" "$fst" ;;
      /run) runmount=yes; printf 'MOUNTRUN %s %s\n' "$src" "$fst" ;;
    esac
  done < /proc/mounts
  printf 'RUNMOUNT %s\n' "$runmount"
  if [ -f /run/from-new-root ]; then
    read -r runfile < /run/from-new-root
    printf 'RUNFILE %s\n' "$runfile"
  else
    printf 'RUNFILE missing\n'
  fi
  if [ -e /run/from-old-root ]; then printf 'RUNLEAK yes\n'; else printf 'RUNLEAK no\n'; fi
  printf 'MOUNTS-BEGIN\n'
  while read -r line; do
    printf '%s\n' "$line"
  done < /proc/mounts
  printf 'MOUNTS-END\n'
  printf 'ARGV %s\n' "$0"
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
{
  echo "packed busybox ash, failed exec"
  set +e
  "$T/sys/usr/bin/busybox" ash -c 'set +e; exec /no/such/switch_root; echo SURVIVED'
  echo ASH_RC:$?
  set -e
} > "$PROOF/ash-exec.txt" 2>&1
# The packed switch_root refuses a non-ramfs root. Build a tmpfs initramfs,
# the same shape as the boot: proc, sys and dev are mounts, /run is not.
IR=/tmp/ir-m4
mkdir -p "\$IR"
mount -t tmpfs tmpfs "\$IR"
mkdir -p "\$IR/bin" "\$IR/lib" "\$IR/usr/bin" "\$IR/usr/lib" "\$IR/proc" "\$IR/sys" "\$IR/dev" "\$IR/run" "\$IR/newroot"
cp -a "$T/sys/usr/bin/switch_root" "\$IR/usr/bin/switch_root"
cp -a "$T/sys/usr/lib/ld-linux-aarch64.so.1" "\$IR/lib/ld-linux-aarch64.so.1"
cp -a "$T/sys/usr/lib/ld-linux-aarch64.so.1" "\$IR/usr/lib/ld-linux-aarch64.so.1"
cp -a "$T/sys/usr/lib/libc.so.6" "\$IR/usr/lib/libc.so.6"
cp -a "$T/sys/usr/lib/libcrypt.so.2.0.0" "\$IR/usr/lib/libcrypt.so.2.0.0"
ln -sfn libcrypt.so.2.0.0 "\$IR/usr/lib/libcrypt.so.2"
mount --bind /proc "\$IR/proc"
mount --bind /sys "\$IR/sys"
mount --bind /dev "\$IR/dev"
printf '%s\n' OLD-RUN > "\$IR/run/from-old-root"
mount --bind "$NR" "\$IR/newroot"
echo 'RUN_BEFORE not-mount' > "$PROOF/console.txt"
mountpoint -q "\$IR/run" && echo 'unexpected run mount' >> "$PROOF/console.txt"
exec chroot "\$IR" /usr/bin/switch_root /newroot /init
EOF
chmod 755 "$INNER"
set +e
sudo unshare --mount --pid --fork --mount-proc --propagation private "$INNER" > "$T/unshare.out" 2>&1
rc=$?
cp "$T/unshare.out" /tmp/m4-unshare.txt 2>/dev/null || true
set -e
rm -f /tmp/NOT-THE-NEW-ROOT
if [ "$rc" -ne 0 ]; then
  echo "unshare/switch_root exited $rc" >&2
  cat "$T/unshare.out" >&2
  exit 1
fi
result=$NR/proof/result
[ -f "$result" ] || { echo "no proof file" >&2; cat "$T/unshare.out" >&2; exit 1; }
echo "----- switch_root console -----"
cat "$T/unshare.out"
echo "----- proof -----"
cat "$result"
echo "----- ash exec -----"
cat "$PROOF/ash-exec.txt"
echo "----- before -----"
cat "$PROOF/console.txt"
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
grep -qx 'RUN_BEFORE not-mount' "$PROOF/console.txt"
grep -qx 'RUNMOUNT no' "$result"
grep -qx 'RUNFILE NEW-RUN' "$result"
grep -qx 'RUNLEAK no' "$result"
grep -q 'SURVIVED' "$PROOF/ash-exec.txt" && { echo "ash continued after failed exec" >&2; exit 1; }
grep -q 'not found' "$PROOF/ash-exec.txt"
echo "handoff proof ok"
