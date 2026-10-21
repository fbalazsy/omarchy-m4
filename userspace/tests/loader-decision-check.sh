#!/bin/sh
# m4-boot -> lane L's production boot policy: write /boot/m4 with m4-boot (a valid Image header, the loader's real board.dtb, a random initramfs) into an ext4
# image made with the loader's feature set, then ask loader-decision.sh what the loader would do: with and without /boot/m4/boot-pending.
# Part 1 runs in m4-n1-alarm (mke2fs -d), part 2 in m4-r41-build (the loader's host build). Expected output:
#   source=entry entry=7.1.13-m4 reason=default detail=ok ...
#   source=rescue entry=rescue reason=previous-boot-incomplete detail=pending ...
set -eu
HERE=$(cd "$(dirname "$0")" && pwd)
W=${W:-/work/n1/userspace}
BOOT=${BOOT:-$HERE/../support/usr/bin/m4-boot}
LOADER=${LOADER:-/reports/n1/loader}
FEAT=has_journal,ext_attr,resize_inode,dir_index,filetype,extent,64bit,flex_bg,metadata_csum,metadata_csum_seed,sparse_super,large_file,huge_file,dir_nlink,extra_isize
T=$(mktemp -d)
U=$(cat /proc/sys/kernel/random/uuid)    # one fresh UUID for the image and its root=
mkdir -p "$T/root" "$T/mod/7.1.13-m4/dtb/apple"
python3 - "$T" <<'PY'
import struct, sys
raw = bytearray(1024 * 1024)
struct.pack_into('<QQQ', raw, 8, 0, len(raw), 4)
raw[56:60] = b'ARMd'
open(sys.argv[1] + '/mod/7.1.13-m4/vmlinuz', 'wb').write(raw)
PY
cp "$LOADER/dts/board.dtb" "$T/mod/7.1.13-m4/dtb/apple/t6040-j616s.dtb"
head -c 300000 /dev/urandom > "$T/initramfs.img"
echo "root=UUID=$U rw quiet" > "$T/cmdline"
python3 -I "$BOOT" --boot "$T/root/boot" --modules "$T/mod" --cmdline "$T/cmdline" install --kver 7.1.13-m4 --initramfs "$T/initramfs.img"
image() {   # dir out
    rm -f "$2"
    truncate -s 64M "$2"
    mke2fs -q -F -t ext4 -b 4096 -I 256 -O "none,$FEAT" -U "$U" -d "$1" "$2"
}
image "$T/root" "$W/ld-disk.ext4"
cp -a "$T/root" "$T/root2"
echo 7.1.13-m4 > "$T/root2/boot/m4/boot-pending"
image "$T/root2" "$W/ld-disk2.ext4"
rm -rf "$T"
echo "images: $W/ld-disk.ext4 $W/ld-disk2.ext4 -> run in m4-r41-build:"
echo "  bash $LOADER/tools/loader-decision.sh $W/ld-disk.ext4"
echo "  bash $LOADER/tools/loader-decision.sh $W/ld-disk2.ext4"
