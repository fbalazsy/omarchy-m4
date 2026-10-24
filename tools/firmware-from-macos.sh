#!/bin/sh
# Fill userspace/firmware from your own Mac's macOS firmware, to build m4-firmware on a machine that has none installed yet.
#
#   tools/firmware-from-macos.sh DIR      DIR = what asahi-installer saves from macOS: all_firmware.tar.gz and the kernelcache,
#                                         plus macOS's /usr/sbin/appleh16camerad (the camera setfiles; the installer only saves h13)
#
# asahi-fwextract (package asahi-fwextract) turns the macOS files into Linux names; this takes the ones m4-firmware ships
# (FIRMWARE.sha256 lists them) and records their sums in FIRMWARE.local.sha256 (never committed), which the package check uses.
# Files that differ from the reference (the owner's Mac on macOS 26.6.2) are listed: per-device calibration always differs.
# The result is Apple/Broadcom firmware and your Mac's calibration: never commit or share it.
set -eu
[ $# -eq 1 ] || { sed -n '2,10p' "$0"; exit 2; }
src=$(realpath "$1")
cd "$(dirname "$0")/../userspace/firmware"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
asahi-fwextract "$src" "$tmp"
mkdir "$tmp/x"
tar -xf "$tmp/firmware.tar" -C "$tmp/x"
# The IMX958 setfile 0x22240106, a 4 KiB aligned block in appleh16camerad; asahi-fwextract only reads appleh13camerad.
if [ -f "$src/appleh16camerad" ]; then
    mkdir -p "$tmp/x/apple"
    python3 -I - "$src/appleh16camerad" "$tmp/x/apple/isp_2224_01XX.dat" <<'EOF'
import struct, sys
data = open(sys.argv[1], 'rb').read()
for off in range(0, len(data) - 32, 0x1000):
    h = struct.unpack_from('>8L', data, off)
    if h[0] == 0x22240106 and h[2] == h[3] == h[6] == 0 and h[7] == 0x3c00000:
        open(sys.argv[2], 'wb').write(data[off:off + 0x6ff4])  # the end of its section chain, as linux-m4 loads it
        break
else:
    sys.exit('appleh16camerad: setfile 0x22240106 not found')
EOF
else
    echo "no appleh16camerad in $src: copy /usr/sbin/appleh16camerad from macOS there for the camera setfile"
fi
: > "$tmp/sums"
missing=0
while read -r sum path; do
    name=${path#usr/lib/firmware/}
    if [ ! -f "$tmp/x/$name" ]; then
        echo "missing: $name"
        missing=1
        continue
    fi
    install -Dm644 "$tmp/x/$name" "$path"
    sha256sum "$path" >> "$tmp/sums"
    grep -q "^$sum " "$tmp/sums" || echo "differs from the reference: $name"
done < FIRMWARE.sha256
[ "$missing" = 0 ] || { echo "firmware-from-macos: not every file was found; m4-firmware cannot be built" >&2; exit 1; }
cp "$tmp/sums" FIRMWARE.local.sha256
echo "firmware-from-macos: done; now build packages/m4-firmware"
