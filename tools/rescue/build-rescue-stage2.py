#!/usr/bin/env python3
"""Rebuild the g55obs2 stage2 with a rescue initramfs.

The kernel, early nvme modules and bootargs root stay. /init no longer
calls switch_root, and the embedded init= path is changed to /usr/bin/ash.
Input must be the trial stage2 82781366… (18,239,729 bytes).
"""
import hashlib, pathlib, struct, subprocess, sys, tempfile

WANT = "8278136603f9fbbf8ee64eb83a5f2231e43af084a94612f45326978b676577ef"
SIZE_AT = 4103735
EARLY_AT = 4103739
EARLY_LEN = 234816
MID_AT = 4338555
MID_LEN = 6409337
OLD_INIT = b"init=/usr/local/sbin/rover-init"
NEW_INIT = b"init=/usr/bin/ash"

RESCUE_INIT = """#!/usr/bin/ash
export PATH=/usr/bin:/bin:/sbin
echo "RESCUE shell stays in the initramfs"
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
modprobe nvme_core
modprobe nvme
modprobe nvme_apple
root=
for w in $(cat /proc/cmdline); do
    case $w in
        root=*) root=${w#root=} ;;
    esac
done
echo "ROOT_ARG ${root:-none}"
mkdir -p /mnt
dev=
if [ -n "$root" ]; then
    dev=$(blkid -t "$root" -o device 2>/dev/null | head -n 1)
fi
echo "ROOT_DEV ${dev:-none}"
if [ -n "$dev" ] && mount -t ext4 -o ro,noload "$dev" /mnt; then
    echo "MOUNTED_RO_NOLOAD $dev"
    p=/mnt/usr/local/sbin/rover-init
    if [ -e "$p" ]; then
        ls -l "$p"
        line=$(head -n 1 "$p" 2>/dev/null)
        echo "SHEBANG ${line:-empty}"
        case $line in
            '#!'*)
                interp=$(printf '%s' "$line" | sed 's/^#!//;s/ .*//')
                echo "INTERP $interp"
                if [ -e "/mnt$interp" ]; then ls -l "/mnt$interp"; else echo "INTERP_ABSENT $interp"; fi
                ;;
            *) echo "NO_SHEBANG" ;;
        esac
    else
        echo "ROVER_INIT_ABSENT"
    fi
    if [ -e /mnt/sbin/init ]; then ls -l /mnt/sbin/init; else echo "SBIN_INIT_ABSENT"; fi
    if [ -e /mnt/usr/lib/systemd/systemd ]; then ls -l /mnt/usr/lib/systemd/systemd; else echo "SYSTEMD_ABSENT"; fi
    umount /mnt || echo "UMOUNT_FAILED left read-only"
else
    echo "MOUNT_RO_FAILED"
fi
echo "SHELL power-button off. root init is not executed. no reboot."
exec /usr/bin/ash
"""


def unpack_cpio(buf):
    j = 0
    items = []
    while j + 110 <= len(buf) and buf[j:j + 6] == b"070701":
        ns = int(buf[j + 94:j + 102], 16)
        fs = int(buf[j + 54:j + 62], 16)
        mode = int(buf[j + 14:j + 22], 16)
        name = buf[j + 110:j + 110 + ns].split(b"\0", 1)[0].decode()
        name_end = 110 + ns
        pad = (4 - (name_end % 4)) % 4
        data_off = j + name_end + pad
        items.append((name, mode, buf[data_off:data_off + fs]))
        if name == "TRAILER!!!":
            break
        j = data_off + ((fs + 3) & ~3)
    else:
        raise SystemExit("cpio truncated")
    return items


def pack_cpio(items):
    out = bytearray()
    for ino, (name, mode, blob) in enumerate(items, 1):
        nb = name.encode() + b"\0"
        hdr = (
            f"070701{ino:08X}{mode:08X}{0:08X}{0:08X}{1:08X}{0:08X}"
            f"{len(blob):08X}{0:08X}{0:08X}{0:08X}{0:08X}{len(nb):08X}{0:08X}"
        ).encode()
        if len(hdr) != 110:
            raise SystemExit("bad cpio header")
        out += hdr + nb
        while len(out) % 4:
            out += b"\0"
        out += blob
        while len(out) % 4:
            out += b"\0"
    return bytes(out)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: build-rescue-stage2.py STAGE2 OUT")
    src = pathlib.Path(sys.argv[1]).read_bytes()
    if hashlib.sha256(src).hexdigest() != WANT or len(src) != 18239729:
        raise SystemExit("input is not the trial stage2")
    if struct.unpack_from("<I", src, SIZE_AT)[0] != EARLY_LEN + MID_LEN:
        raise SystemExit("initramfs size field moved")
    if src[MID_AT + MID_LEN:MID_AT + MID_LEN + 16] != b"chosen.bootargs=":
        raise SystemExit("bootargs do not follow the initramfs")
    early = src[EARLY_AT:MID_AT]
    if len(early) != EARLY_LEN:
        raise SystemExit("early cpio length")
    mid = subprocess.run(["zstd", "-d", "-c"], input=src[MID_AT:MID_AT + MID_LEN], capture_output=True)
    if mid.returncode != 0:
        raise SystemExit("zstd decode failed")
    items = unpack_cpio(mid.stdout)
    replaced = False
    new_items = []
    for name, mode, blob in items:
        if name == "init":
            blob = RESCUE_INIT.encode()
            mode = 0o100755
            replaced = True
        new_items.append((name, mode, blob))
    if not replaced:
        raise SystemExit("init missing")
    packed = pack_cpio(new_items)
    comp = subprocess.run(
        ["zstd", "-3", "--long=23", "-c"], input=packed, capture_output=True)
    if comp.returncode != 0:
        raise SystemExit(comp.stderr.decode())
    zblob = comp.stdout
    with tempfile.NamedTemporaryFile(suffix=".zst") as fh:
        fh.write(zblob)
        fh.flush()
        info = subprocess.run(["zstd", "-l", "-v", fh.name], capture_output=True, text=True)
    if "8.00 MiB" not in info.stdout and "8388608" not in info.stdout:
        raise SystemExit("zstd window is not 8 MiB:\n" + info.stdout)
    suffix = bytearray(src[MID_AT + MID_LEN:])
    at = suffix.find(OLD_INIT)
    if at < 0 or suffix.find(OLD_INIT, at + 1) >= 0:
        raise SystemExit("init= token not unique in bootargs")
    pad = b" " * (len(OLD_INIT) - len(NEW_INIT))
    suffix[at:at + len(OLD_INIT)] = NEW_INIT + pad
    if b"rover-init" in suffix:
        raise SystemExit("rover-init remains in bootargs")
    total = EARLY_LEN + len(zblob)
    out = bytearray(src[:SIZE_AT])
    out += struct.pack("<I", total)
    out += early
    out += zblob
    out += suffix
    if out[SIZE_AT + 4:SIZE_AT + 10] != b"070701":
        raise SystemExit("early cpio not after size")
    if out[SIZE_AT + 4 + total:SIZE_AT + 4 + total + 16] != b"chosen.bootargs=":
        raise SystemExit("bootargs shifted wrong")
    out_path = pathlib.Path(sys.argv[2])
    out_path.write_bytes(out)
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from macho_boot_object import wrap
    macho = wrap(bytes(out))
    macho_path = out_path.with_suffix(".macho")
    macho_path.write_bytes(macho)
    if b"__TEXT_BOOT_EXEC" in macho:
        raise SystemExit("wrapped object has __TEXT_BOOT_EXEC")
    image_at = 0x10000
    if macho[image_at + 0x808:image_at + 0x808 + 8] != b"AURALOAD":
        raise SystemExit("wrapped raw image lost AURALOAD")
    print(hashlib.sha256(out).hexdigest(), len(out), "raw")
    print(hashlib.sha256(macho).hexdigest(), len(macho), "macho")


if __name__ == "__main__":
    main()
