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
ln -s "$(command -v busybox)" "$T/bin/awk"
run_dhcp() {
  : > "$T/log/ip"
  # shellcheck disable=SC2086
  env LOG=$T/log PATH="$T/bin:$PATH" "$@" \
    sh "$HERE/udhcpc-m4.sh" bound
}
no_host_route() {
  if grep -q 'route ' "$T/log/ip" || grep -q default "$T/log/ip"; then
    echo "on-link or no-router lease installed a route: $(cat "$T/log/ip")" >&2
    exit 1
  fi
}
# 192.0.2.10/24 and 192.0.2.25 are one prefix. The connected route is enough.
run_dhcp interface=eth0 ip=192.0.2.10 subnet=24 router=192.0.2.1 M4_TELEMETRY_HOST=192.0.2.25
grep -q 'addr add 192.0.2.10/24 dev eth0' "$T/log/ip"
no_host_route
# BusyBox 1.36.1: subnet is the dotted mask, mask is the prefix.
run_dhcp interface=eth0 ip=192.0.2.10 subnet=255.255.255.0 mask=24 router=192.0.2.1 M4_TELEMETRY_HOST=192.0.2.25
grep -q 'addr flush dev eth0' "$T/log/ip"
grep -q 'addr add 192.0.2.10/24 dev eth0' "$T/log/ip"
no_host_route
run_dhcp interface=eth0 ip=192.0.2.10 subnet=255.255.255.0 router=192.0.2.1 M4_TELEMETRY_HOST=192.0.2.25
grep -q 'addr add 192.0.2.10/24 dev eth0' "$T/log/ip"
no_host_route
# /25: .20 is inside, .200 is outside. The router .1 is inside, so only .200 is via.
run_dhcp interface=eth0 ip=192.0.2.10 mask=255.255.255.128 router=192.0.2.1 M4_TELEMETRY_HOST=192.0.2.20
grep -q 'addr add 192.0.2.10/25 dev eth0' "$T/log/ip"
no_host_route
run_dhcp interface=eth0 ip=192.0.2.10 mask=255.255.255.128 router=192.0.2.1 M4_TELEMETRY_HOST=192.0.2.200
grep -q 'route replace 192.0.2.200/32 via 192.0.2.1 dev eth0' "$T/log/ip"
grep -q default "$T/log/ip" && exit 1
# /16 stays on-link across the third octet. /23 covers .2.0 through .3.255.
run_dhcp interface=eth0 ip=192.0.2.10 subnet=16 router=192.0.2.1 M4_TELEMETRY_HOST=192.0.9.9
grep -q 'addr add 192.0.2.10/16 dev eth0' "$T/log/ip"
no_host_route
run_dhcp interface=eth0 ip=192.0.2.10 subnet=23 router=192.0.2.1 M4_TELEMETRY_HOST=192.0.3.10
no_host_route
run_dhcp interface=eth0 ip=192.0.2.10 subnet=23 router=192.0.2.1 M4_TELEMETRY_HOST=192.0.4.10
grep -q 'route replace 192.0.4.10/32 via 192.0.2.1 dev eth0' "$T/log/ip"
grep -q default "$T/log/ip" && exit 1
# Off-prefix peer, on-prefix router.
run_dhcp interface=eth0 ip=192.0.2.10 subnet=24 router=192.0.2.1 M4_TELEMETRY_HOST=198.51.100.8
grep -q 'route replace 198.51.100.8/32 via 192.0.2.1 dev eth0' "$T/log/ip"
grep -q default "$T/log/ip" && exit 1
# No router option: address only, whether or not the peer is on-link.
run_dhcp interface=eth0 ip=192.0.2.10 subnet=24 M4_TELEMETRY_HOST=192.0.2.25
grep -q 'addr add 192.0.2.10/24 dev eth0' "$T/log/ip"
no_host_route
run_dhcp interface=eth0 ip=192.0.2.10 subnet=24 M4_TELEMETRY_HOST=198.51.100.8
grep -q 'addr add 192.0.2.10/24 dev eth0' "$T/log/ip"
no_host_route
# A router outside the prefix is not a usable next hop. /31 only contains .10 and .11.
run_dhcp interface=eth0 ip=192.0.2.10 subnet=24 router=198.51.100.1 M4_TELEMETRY_HOST=198.51.100.8
no_host_route
run_dhcp interface=eth0 ip=192.0.2.10 subnet=32 router=192.0.2.1 M4_TELEMETRY_HOST=198.51.100.8
no_host_route
run_dhcp interface=eth0 ip=192.0.2.10 subnet=31 router=192.0.2.11 M4_TELEMETRY_HOST=192.0.2.11
no_host_route
run_dhcp interface=eth0 ip=192.0.2.10 subnet=31 router=192.0.2.11 M4_TELEMETRY_HOST=192.0.2.12
grep -q 'route replace 192.0.2.12/32 via 192.0.2.11 dev eth0' "$T/log/ip"
grep -q default "$T/log/ip" && exit 1
grep -q 'route add default' "$HERE/udhcpc-m4.sh" && exit 1
grep -q '0.0.0.0/0' "$HERE/udhcpc-m4.sh" && exit 1
reject_dhcp() {
  : > "$T/log/ip"
  if env LOG=$T/log PATH="$T/bin:$PATH" M4_TELEMETRY_HOST=192.0.2.25 \
      "$@" sh "$HERE/udhcpc-m4.sh" bound; then
    echo "dhcp should reject $*" >&2
    exit 1
  fi
  [ ! -s "$T/log/ip" ]
}
reject_dhcp interface=eth0 ip=192.0.2.10 subnet=255.0.255.0 mask=24 router=192.0.2.1
reject_dhcp interface=eth0 ip=192.0.2.10 subnet=255.255.255.0 mask=23 router=192.0.2.1
reject_dhcp interface=eth0 ip=192.0.2.10 subnet=33 router=192.0.2.1
reject_dhcp interface=eth0 ip=192.0.2.10 subnet=0 router=192.0.2.1
reject_dhcp interface=eth0 ip=192.0.2.10 subnet=0.0.0.0 router=192.0.2.1
reject_dhcp interface='eth0;ip' ip=192.0.2.10 subnet=24 router=192.0.2.1
: > "$T/log/ip"
env LOG=$T/log PATH="$T/bin:$PATH" interface=eth0 ip=192.0.2.10 subnet=255.255.255.0 mask=24 \
  router=192.0.2.1 M4_TELEMETRY_HOST=192.0.2.25 sh "$HERE/udhcpc-m4.sh" deconfig
[ ! -s "$T/log/ip" ]
echo "dhcp routes ok"
if [ -n "${DHCP_ONLY:-}" ]; then
  exit 0
fi
# same dotted-mask case under the embedded client's shell
python3 - "$TRIAL" << 'PY'
import pathlib, subprocess, sys
b = pathlib.Path(sys.argv[1]).read_bytes()
size_at, early = 4103735, 234816
total = int.from_bytes(b[size_at:size_at+4], "little")
mid = b[size_at+4+early:size_at+4+total]
dec = subprocess.run(["zstd","-d","-c"], input=mid, capture_output=True).stdout
j = 0
bb = None
while j+110 <= len(dec) and dec[j:j+6] == b"070701":
    ns = int(dec[j+94:j+102], 16); fs = int(dec[j+54:j+62], 16)
    name = dec[j+110:j+110+ns].split(b"\0", 1)[0].decode()
    name_end = 110+ns
    off = j+name_end+(4-(name_end%4))%4
    blob = dec[off:off+fs]
    if name == "usr/bin/busybox":
        bb = blob
    if name == "TRAILER!!!":
        break
    j = off+((fs+3)&~3)
text = bb.decode("latin1", "replace")
assert "udhcp 1.36.1" in text
assert "mask=%u" in text
assert "subnet\x00" in text
print("embedded udhcpc 1.36.1")
PY
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
# Absolute symlink must stay inside the target root, not the host's /usr/bin/bash.
mkdir -p "$T/root2/usr/bin" "$T/root2/bin" "$T/root2/usr/local/sbin"
printf '%s\n' 'INSIDE-ROOT' > "$T/root2/usr/bin/bash"
chmod 755 "$T/root2/usr/bin/bash"
ln -s /usr/bin/bash "$T/root2/bin/bash"
printf '%s\n' '#!/bin/bash' 'echo not-run' > "$T/root2/usr/local/sbin/rover-init"
chmod 755 "$T/root2/usr/local/sbin/rover-init"
: > "$T/chroot.log"
out=$(MOCK_RC=0 MOCK_OUT= MOCK_LOG=$T/chroot.log LOG=$T/verify.log M4_RUN=$T/run PATH="$T/bin:$PATH" \
  sh -c '
    set -eu
    . "$1"
    if verify_root_init "$2" /usr/local/sbin/rover-init; then echo DECISION handoff; else echo DECISION hold; fi
  ' sh "$HERE/m4diag-lib.sh" "$T/root2")
printf '%s\n' "$out" | grep -qx 'DECISION handoff'
printf '%s\n' "$out" | grep -q 'INTERP_FILE /usr/local/sbin/rover-init /usr/bin/bash'
printf '%s\n' "$out" | grep -q 'RESOLVED /usr/local/sbin/rover-init /usr/local/sbin/rover-init'
# The opened interpreter is the file inside the fixture. Host bash is an ELF.
python3 - "$T/root2/usr/bin/bash" << 'PY'
import pathlib, sys
text = pathlib.Path(sys.argv[1]).read_text()
assert text.startswith("INSIDE-ROOT")
PY
grep -q '/bin/bash -n usr/local/sbin/rover-init' "$T/chroot.log"
sh -c '
  set -eu
  . "$1"
  child_ok 1 && exit 1
  child_ok 0 && exit 1
  child_ok "" && exit 1
  child_ok -1 && exit 1
  child_ok 4321
' sh "$HERE/m4diag-lib.sh"
# malformed ELF and a failing elf-interp must not kill the caller
printf 'not-elf' > "$T/bad.elf"
out=$(PATH="$T/bb:$PATH" busybox ash "$HERE/elf-interp.sh" "$T/bad.elf")
printf '%s\n' "$out" | grep -qx 'KIND other'
printf '\177ELF' > "$T/short.elf"
out=$(PATH="$T/bb:$PATH" busybox ash "$HERE/elf-interp.sh" "$T/short.elf"; echo EXIT:$?)
printf '%s\n' "$out" | grep -q '^EXIT:0$'
cat > "$T/bin/elf-interp" << 'EOF'
#!/bin/sh
echo KIND other
echo INTERP none
exit 9
EOF
chmod 755 "$T/bin/elf-interp"
mkdir -p "$T/root3/bin"
printf 'x' > "$T/root3/bin/bash"
out=$(LOG=$T/verify.log M4_RUN=$T/run PATH="$T/bin:$PATH" sh -c '
  set -eu
  . "$1"
  report_extra "$2"
  echo STILL-ALIVE
' sh "$HERE/m4diag-lib.sh" "$T/root3")
printf '%s\n' "$out" | grep -q 'elf-interp-failed'
printf '%s\n' "$out" | grep -qx 'STILL-ALIVE'
# script text is reported and not executed
cat > "$T/bin/elf-interp" << EOF
#!/bin/sh
exec busybox ash "$HERE/elf-interp.sh" "\$@"
EOF
chmod 755 "$T/bin/elf-interp"
printf '%s\n' '#!/bin/bash' 'exec /lib/systemd/systemd' > "$T/root/usr/local/sbin/rover-init"
chmod 755 "$T/root/usr/local/sbin/rover-init"
printf '%s\n' 'interpreter' > "$T/root/bin/bash"
chmod 755 "$T/root/bin/bash"
: > "$T/chroot.log"
out=$(verify 0 "" /usr/local/sbin/rover-init)
printf '%s\n' "$out" | grep -q 'SCRIPT_DECL'
printf '%s\n' "$out" | grep -q 'exec /lib/systemd/systemd'
printf '%s\n' "$out" | grep -q 'syntax-only'
grep -q -- '-n usr/local/sbin/rover-init' "$T/chroot.log"
grep -q 'systemd' "$T/chroot.log" && exit 1
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
  --firmware /tmp/fwm4/usr/lib/firmware/apple/tpmtfw-j616s.bin \
  --telemetry-url 'http://192.0.2.25:8616/up' > "$T/build.out"
cat "$T/build.out"
grep -q 'LIMIT module-firmware-glob apple/tpmtfw-\*.bin' "$T/build.out"
grep -q 'readlink -f' "$HERE/m4diag-lib.sh" && exit 1
grep -q 'm4diag-start.txt' "$HERE/m4diag-lib.sh"
grep -q 'm4diag-checks.txt' "$HERE/m4diag-lib.sh"
grep -q 'm4diag-handoff.txt' "$HERE/m4diag-lib.sh"
grep -q 'route add default' "$HERE/udhcpc-m4.sh" && exit 1
grep -q '0.0.0.0/0' "$HERE/udhcpc-m4.sh" && exit 1
grep -q 'exec /usr/bin/switch_root' "$HERE/m4diag-lib.sh"
awk '
  /mode" = "ro-hold"/ { ro = NR }
  /exec \/usr\/bin\/switch_root/ { sw = NR }
  /FALLBACK_WIFI pre-exec-only/ { fb = NR }
  END { if (!(ro && sw && fb && ro < sw && sw < fb)) exit 1 }
' "$HERE/m4diag-lib.sh"
grep -q 'no fallback after a successful exec' "$HERE/m4diag-lib.sh"
grep -q 'ROOT_STAYS ro,noload' "$HERE/m4diag-lib.sh" && exit 1
grep -q 'M4_EXEC_INIT' "$HERE/m4diag-lib.sh" && exit 1
grep -q 'handoff_root' "$HERE/m4diag-lib.sh" && exit 1
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
assert "exec /usr/bin/switch_root" in lib
assert "M4_EXEC_INIT" not in lib
assert 'mount --move "$root"' not in lib
url = (root / "etc/m4diag-url").read_text().strip()
assert url == "http://192.0.2.25:8616/up"
fw = (root / "usr/lib/firmware/apple/tpmtfw-j616s.bin").read_bytes()
assert len(fw) == 81424
assert __import__("hashlib").sha256(fw).hexdigest() == "7eb84312cb4ea74833ce9567ba8c92bc42a0c26c5c34d6bdfaa5cb65abfe4b26"
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
echo "m4diag image built"
"$HERE/test-handoff.sh" "$T/m4diag.macho"
echo "m4diag tests: all passed"
