#!/bin/sh
# Offline checks for the g55obs2 diagnostic Mach-O. No enroll, no reboot.
set -eu
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)
TRIAL=${TRIAL:-/tmp/trial-pack/stage2-m4222-g55obs2.bin}
MODULES=${MODULES:-/tmp/g55in/modules-7.3.0-m4.222-g55obs2.tar.xz}
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
mkdir -p "$T/bin" "$T/root" "$T/run" "$T/bb"

ln -s "$(command -v busybox)" "$T/bb/awk"
ln -s "$(command -v busybox)" "$T/bb/xxd"
ln -s "$(command -v busybox)" "$T/bb/head"
ln -s "$(command -v busybox)" "$T/bb/tr"

# Interpreter reader, against busybox awk/xxd and against the host ones.
python3 - "$T/demo.elf" "$T/demo.sh" << 'PY'
import pathlib, struct, sys
elf, script = sys.argv[1:]
interp = b"/lib/ld-linux-aarch64.so.1\0"
ehdr = bytearray(64)
ehdr[0:4] = b"\x7fELF"
ehdr[4] = 2
ehdr[5] = 1
struct.pack_into("<H", ehdr, 16, 2)
struct.pack_into("<H", ehdr, 18, 183)
struct.pack_into("<I", ehdr, 20, 1)
struct.pack_into("<Q", ehdr, 32, 64)
struct.pack_into("<H", ehdr, 52, 64)
struct.pack_into("<H", ehdr, 54, 56)
struct.pack_into("<H", ehdr, 56, 1)
phdr = bytearray(56)
struct.pack_into("<I", phdr, 0, 3)
struct.pack_into("<Q", phdr, 8, 120)
struct.pack_into("<Q", phdr, 32, len(interp))
pathlib.Path(elf).write_bytes(bytes(ehdr) + bytes(phdr) + interp)
pathlib.Path(script).write_text("#!/bin/bash -e\necho hi\n")
PY
run_interp() {
  # shellcheck disable=SC2086
  PATH="$1:$PATH" busybox ash "$HERE/elf-interp.sh" "$2"
}
out=$(run_interp "$T/bb" "$T/demo.elf")
printf '%s\n' "$out" | grep -qx 'KIND elf'
printf '%s\n' "$out" | grep -qx 'INTERP /lib/ld-linux-aarch64.so.1'
out=$(run_interp "$T/bb" "$T/demo.sh")
printf '%s\n' "$out" | grep -qx 'KIND script'
printf '%s\n' "$out" | grep -qx 'INTERP /bin/bash'
out=$(PATH="$T/bb:$PATH" busybox ash "$HERE/elf-interp.sh" "$T/missing")
printf '%s\n' "$out" | grep -qx 'KIND absent'
if command -v readelf >/dev/null 2>&1; then
  host=$(run_interp /usr/bin /bin/ls)
  want=$(readelf -l /bin/ls | awk '/Requesting program interpreter/ {print $NF}' | tr -d ']')
  printf '%s\n' "$host" | grep -qx "INTERP $want"
fi
echo "elf-interp ok"

# udhcpc callback: one host route, never a default route.
cat > "$T/bin/ip" << 'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$LOG/ip"
exit 0
EOF
chmod 755 "$T/bin/ip"
mkdir -p "$T/log"
LOG=$T/log PATH="$T/bin:$PATH" interface=eth0 ip=192.0.2.10 subnet=24 router=192.0.2.1 \
  M4_TELEMETRY_HOST=192.0.2.25 sh "$HERE/udhcpc-m4.sh" bound
grep -q 'addr add 192.0.2.10/24 dev eth0' "$T/log/ip"
grep -q 'route replace 192.0.2.25/32 via 192.0.2.1 dev eth0' "$T/log/ip"
grep -q default "$T/log/ip" && exit 1
: > "$T/log/ip"
LOG=$T/log PATH="$T/bin:$PATH" interface=eth0 ip=192.0.2.10 subnet=24 router=192.0.2.1 \
  M4_TELEMETRY_HOST=192.0.2.25 sh "$HERE/udhcpc-m4.sh" deconfig
[ ! -s "$T/log/ip" ]
echo "udhcpc ok"

# verify_root_init: shebang, missing interpreter, ELF with a missing library.
mkdir -p "$T/root/usr/local/sbin" "$T/root/bin" "$T/root/lib" "$T/bin"
printf '%s\n' '#!/bin/bash' 'echo ok' > "$T/root/usr/local/sbin/rover-init"
chmod 755 "$T/root/usr/local/sbin/rover-init"
cp "$T/demo.elf" "$T/root/sbin-init-body"
mkdir -p "$T/root/sbin"
# configured init for the elf case is installed per case
printf '%s\n' 'interpreter' > "$T/root/bin/bash"
chmod 755 "$T/root/bin/bash"
printf '%s\n' 'linker' > "$T/root/lib/ld-linux-aarch64.so.1"
chmod 755 "$T/root/lib/ld-linux-aarch64.so.1"
cat > "$T/bin/chroot" << 'EOF'
#!/bin/sh
printf '%s\n' "$*" >> "$MOCK_LOG"
if [ -n "${MOCK_OUT:-}" ]; then
  printf '%s\n' "$MOCK_OUT"
fi
exit "${MOCK_RC:-0}"
EOF
cat > "$T/bin/elf-interp" << EOF
#!/bin/sh
exec busybox ash "$HERE/elf-interp.sh" "\$@"
EOF
chmod 755 "$T/bin/chroot" "$T/bin/elf-interp"
verify() {
  MOCK_RC=${1:-0} MOCK_OUT=${2:-} MOCK_LOG=$T/chroot.log LOG=$T/verify.log \
    M4_RUN=$T/run PATH="$T/bin:$PATH" \
    sh -c '
      set -eu
      . "$1"
      root=$2
      rel=$3
      if verify_root_init "$root" "$rel"; then
        echo DECISION handoff
      else
        echo DECISION hold
      fi
    ' sh "$HERE/m4diag-lib.sh" "$T/root" "$3"
}
: > "$T/chroot.log"
out=$(verify 0 "" /usr/local/sbin/rover-init)
printf '%s\n' "$out" | grep -qx 'DECISION handoff'
grep -q '/bin/bash -n usr/local/sbin/rover-init' "$T/chroot.log"
rm -f "$T/root/bin/bash"
: > "$T/chroot.log"
out=$(verify 0 "" /usr/local/sbin/rover-init)
printf '%s\n' "$out" | grep -qx 'DECISION hold'
printf '%s\n' "$out" | grep -q 'interpreter-absent'
# ELF whose --list reports a missing library
cp "$T/demo.elf" "$T/root/usr/local/sbin/rover-init"
chmod 755 "$T/root/usr/local/sbin/rover-init"
: > "$T/chroot.log"
out=$(verify 0 "libc.so not found" /usr/local/sbin/rover-init)
printf '%s\n' "$out" | grep -qx 'DECISION hold'
printf '%s\n' "$out" | grep -q 'missing-lib'
grep -q -- '--list usr/local/sbin/rover-init' "$T/chroot.log"
out=$(verify 0 "" /usr/local/sbin/rover-init)
printf '%s\n' "$out" | grep -qx 'DECISION handoff'
echo "verify ok"

# The source tree carries neither the root identity nor a LAN address.
python3 - "$HERE" "$TRIAL" << 'PY'
import pathlib, sys
here, trial = sys.argv[1:]
blob = pathlib.Path(trial).read_bytes()
start = blob.find(b"root=UUID=")
token = blob[start:blob.find(b" ", start)]
for path in pathlib.Path(here).iterdir():
    if not path.is_file():
        continue
    data = path.read_bytes()
    lan = b"192." + b"168."
    if token in data or lan in data:
        raise SystemExit("private value in %s" % path.name)
print("source privacy ok")
PY

grep -q fsck "$HERE/m4diag-lib.sh" && exit 1
grep -q 'ro,noload' "$HERE/m4diag-lib.sh"
grep -q 'switch_root' "$HERE/m4diag-lib.sh"
grep -q 'CLASS handoff' "$HERE/m4diag-lib.sh"
grep -q 'CLASS diagnostic-hold' "$HERE/m4diag-lib.sh"
grep -q 'cdc_ether|cdc_ncm|r8153_ecm' "$HERE/m4diag-lib.sh"
echo "static ok"

python3 "$HERE/build-m4diag.py" "$TRIAL" "$MODULES" "$T/m4diag.bin" \
  --telemetry-url 'http://192.0.2.25:8616/up/m4diag.txt' > "$T/build.out"
cat "$T/build.out"
grep -q 'LIMIT firmware-wildcard apple/tpmtfw-\*.bin absent-from-module-archive' "$T/build.out"
python3 - "$T/m4diag.bin" "$T/rootfs" << 'PY'
import gzip, io, os, pathlib, shutil, struct, subprocess, sys
image, rootfs = sys.argv[1:]
b = pathlib.Path(image).read_bytes()
size_at = 4103735
early_len = 234816
total = struct.unpack_from("<I", b, size_at)[0]
assert b[size_at + 4:size_at + 10] == b"070701"
suffix = b[size_at + 4 + total:]
assert suffix.startswith(b"chosen.bootargs=")
line, _, rest = suffix.partition(b"\n")
assert b"noresume" in line
assert b"resume=" not in line
assert b"resume_offset=" not in line
assert b"initcall_debug" not in line
assert b"init=/usr/local/sbin/rover-init" in line
assert b"root=" in line
assert b"chosen.aura,nv2=1" in rest
assert b"\nhib=0\n\x1f\x8b\x08" in rest
assert b"\nhib=1\n" not in rest
g = rest.find(b"\x1f\x8b\x08")
img = gzip.GzipFile(fileobj=io.BytesIO(rest[g:])).read(4096)
assert img[56:60] == b"ARMd"
mid = b[size_at + 4 + early_len:size_at + 4 + total]
dec = subprocess.run(["zstd", "-d", "-c"], input=mid, capture_output=True)
assert dec.returncode == 0, dec.stderr
# early then mid, later path wins
early = b[size_at + 4:size_at + 4 + early_len]
root = pathlib.Path(rootfs)
shutil.rmtree(root, ignore_errors=True)
root.mkdir()
(root / "lib").symlink_to("usr/lib")

def extract(buf):
    j = 0
    while j + 110 <= len(buf) and buf[j:j + 6] == b"070701":
        ns = int(buf[j + 94:j + 102], 16)
        fs = int(buf[j + 54:j + 62], 16)
        mode = int(buf[j + 14:j + 22], 16)
        name = buf[j + 110:j + 110 + ns].split(b"\0", 1)[0].decode()
        name_end = 110 + ns
        off = j + name_end + (4 - (name_end % 4)) % 4
        blob = buf[off:off + fs]
        if name == "TRAILER!!!":
            break
        dest = root / name
        if mode & 0o170000 == 0o120000:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.is_symlink() or dest.exists():
                dest.unlink()
            dest.symlink_to(blob.decode())
        elif mode & 0o170000 == 0o040000:
            dest.mkdir(parents=True, exist_ok=True)
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
            os.chmod(dest, mode & 0o777)
        j = off + ((fs + 3) & ~3)

extract(early)
extract(dec.stdout)
rel = root / "usr/lib/modules/7.3.0-m4.222-g55obs2"
for leaf in ("r8153_ecm", "cdc_ncm", "cdc_ether", "dwc3-apple", "xhci-plat-hcd",
             "usbhid", "dockchannel-hid", "spi-apple", "macsmc-input", "nvme-apple"):
    hits = list(rel.rglob(leaf + ".ko.zst"))
    assert hits, leaf
dep = (rel / "modules.dep").read_text()
assert "r8153_ecm.ko.zst" in dep
assert "usbcore.ko.zst" in dep
init = (root / "init").read_text()
assert "m4diag_main" in init
assert "switch_root" not in init  # the decision lives in the library
lib = (root / "usr/bin/m4diag-lib.sh").read_text()
assert "ro,noload" in lib and "fsck" not in lib
url = (root / "etc/m4diag-url").read_text().strip()
assert url == "http://192.0.2.25:8616/up/m4diag.txt"
mods = (root / "etc/m4diag-modules").read_text().split()
assert mods.index("spi-apple") < mods.index("nvme-apple")
assert mods.index("usbcore") < mods.index("r8153_ecm")
print("image layout ok", len(b))
PY
# depmod's index resolves the dock ethernet chain from the packed modules
modprobe -S 7.3.0-m4.222-g55obs2 -d "$T/rootfs" -n -v r8153_ecm > "$T/probe.txt"
modprobe -S 7.3.0-m4.222-g55obs2 -d "$T/rootfs" -n -v dwc3-apple > "$T/probe-dwc.txt"
modprobe -S 7.3.0-m4.222-g55obs2 -d "$T/rootfs" -n -v dockchannel-hid > "$T/probe-dock.txt"
modprobe -S 7.3.0-m4.222-g55obs2 -d "$T/rootfs" -n -v nvme-apple > "$T/probe-nvme.txt"
grep -q usbcore.ko.zst "$T/probe.txt"
grep -q cdc_ether.ko.zst "$T/probe.txt"
grep -q r8153_ecm.ko.zst "$T/probe.txt"
grep -q dwc3-apple.ko.zst "$T/probe-dwc.txt"
grep -q phy-apple-t6040-usb2.ko.zst "$T/probe-dwc.txt"
grep -q dockchannel-hid.ko.zst "$T/probe-dock.txt"
grep -q nvme-apple.ko.zst "$T/probe-nvme.txt"
echo "modprobe ok"
python3 "$HERE/../privacy-check.py" "$HERE"
# macho has no boot-exec segment
python3 - "$T/m4diag.macho" << 'PY'
import pathlib, sys
b = pathlib.Path(sys.argv[1]).read_bytes()
assert b[0x10000 + 0x808:0x10000 + 0x808 + 8] == b"AURALOAD"
assert b"__TEXT_BOOT_EXEC" not in b
print("macho ok", len(b))
PY
echo "m4diag tests: all passed"
