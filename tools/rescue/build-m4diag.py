#!/usr/bin/env python3
"""Build a g55obs2 Mach-O that diagnoses the root, then hands off if it verifies.

Input is the trial stage2 82781366 and the matching module archive
9f456199. The kernel image is not rebuilt. USB gadget is not enabled.
The ESP is not written and nothing is enrolled.
"""
import argparse, hashlib, pathlib, shutil, struct, subprocess, sys, tempfile

WANT = "8278136603f9fbbf8ee64eb83a5f2231e43af084a94612f45326978b676577ef"
MODULES_WANT = "9f4561991f15baa4cca7d82f863766de48283649fe98cabe03489871fec0d9a7"
RELEASE = "7.3.0-m4.222-g55obs2"
SIZE_AT = 4103735
EARLY_LEN = 234816
MID_LEN = 6409337

SEEDS = [
    "kernel/drivers/spi/spi-apple.ko.zst",
    "kernel/drivers/input/misc/macsmc-input.ko.zst",
    "kernel/drivers/soc/apple/apple-dockchannel.ko.zst",
    "kernel/drivers/hid/dockchannel-hid/dockchannel-hid.ko.zst",
    "kernel/drivers/phy/apple/phy-apple-t6040-usb2.ko.zst",
    "kernel/drivers/usb/dwc3/dwc3-apple.ko.zst",
    "kernel/drivers/usb/host/xhci-plat-hcd.ko.zst",
    "kernel/drivers/hid/usbhid/usbhid.ko.zst",
    "kernel/drivers/net/usb/cdc_ether.ko.zst",
    "kernel/drivers/net/usb/cdc_ncm.ko.zst",
    "kernel/drivers/net/usb/r8153_ecm.ko.zst",
    "kernel/drivers/usb/class/cdc-acm.ko.zst",
    "kernel/drivers/nvme/host/nvme.ko.zst",
    "kernel/drivers/nvme/host/nvme-apple.ko.zst",
]


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


def check_url(url):
    if url == "":
        return
    if not url.startswith("http://") or "@" in url:
        raise SystemExit("telemetry url must be http:// without credentials")
    rest = url[len("http://"):]
    hostport, slash, path = rest.partition("/")
    if slash != "/" or not path.startswith("up/"):
        raise SystemExit("telemetry path must be /up/...")
    host, sep, port = hostport.partition(":")
    parts = host.split(".")
    if len(parts) != 4 or any((not p.isdigit()) or int(p) > 255 for p in parts):
        raise SystemExit("telemetry host must be an IPv4 address")
    if sep and (not port.isdigit() or not 1 <= int(port) <= 65535):
        raise SystemExit("telemetry port is not a port")


def patch_bootargs(blob):
    """Neutralise resume and hibernation in the aura config. Keep root=."""
    marker = b"chosen.bootargs="
    if not blob.startswith(marker):
        raise SystemExit("bootargs do not follow the initramfs")
    nl = blob.find(b"\n")
    if nl < 0:
        raise SystemExit("bootargs line has no end")
    line = bytearray(blob[:nl])
    rest = bytearray(blob[nl:])
    key = b" resume="
    at = line.find(key)
    if at < 0 or line.find(key, at + 1) >= 0:
        raise SystemExit("resume= token is not unique on the bootargs line")
    end = line.find(b" ", at + 1)
    if end < 0:
        end = len(line)
    token = b"noresume"
    width = end - (at + 1)
    if width < len(token):
        raise SystemExit("resume= token is shorter than noresume")
    line[at + 1:end] = token + b" " * (width - len(token))
    key2 = b"resume_offset="
    at2 = line.find(key2)
    if at2 < 0 or line.find(key2, at2 + 1) >= 0:
        raise SystemExit("resume_offset= is not unique on the bootargs line")
    end2 = line.find(b" ", at2)
    if end2 < 0:
        end2 = len(line)
    line[at2:end2] = b" " * (end2 - at2)
    key3 = b"initcall_debug"
    at3 = line.find(key3)
    if at3 < 0 or line.find(key3, at3 + 1) >= 0:
        raise SystemExit("initcall_debug is not unique on the bootargs line")
    line[at3:at3 + len(key3)] = b" " * len(key3)
    hib = b"\nhib=1\n\x1f\x8b\x08"
    at_h = rest.find(hib)
    if at_h < 0 or rest.find(hib, at_h + 1) >= 0:
        raise SystemExit("hib=1 before the kernel is not unique")
    rest[at_h:at_h + len(hib)] = b"\nhib=0\n\x1f\x8b\x08"
    if b"chosen.aura,nv2=1" not in rest and b"chosen.aura,nv2=1" not in line:
        raise SystemExit("nv2 config missing")
    out = bytes(line) + bytes(rest)
    if b"init=/usr/local/sbin/rover-init" not in out[:nl]:
        raise SystemExit("configured init= was dropped")
    if b"resume=" in out[:nl]:
        raise SystemExit("resume= still on the bootargs line")
    return out


def strings_with(raw, prefix):
    hits = []
    i = 0
    while True:
        j = raw.find(prefix, i)
        if j < 0:
            return hits
        k = raw.find(b"\0", j)
        hits.append(raw[j:k].decode())
        i = k + 1


def closure(dep):
    seen = []

    def add(mod):
        if mod in seen:
            return
        if mod not in dep:
            raise SystemExit("modules.dep has no %s" % mod)
        for item in dep[mod]:
            add(item)
        seen.append(mod)

    for seed in SEEDS:
        add(seed)
    return seen


def module_name(path):
    base = path.rsplit("/", 1)[-1]
    if not base.endswith(".ko.zst"):
        raise SystemExit("not a zst module: %s" % path)
    return base[: -len(".ko.zst")]


def build_modules(tar_xz, work):
    root = pathlib.Path(work) / "modules"
    subprocess.run(["tar", "-xJf", str(tar_xz), "-C", work], check=True)
    # tar contains lib/modules/...
    src = pathlib.Path(work) / "lib/modules" / RELEASE
    if not src.is_dir():
        raise SystemExit("module archive has no %s" % RELEASE)
    text = (src / "modules.dep").read_text()
    dep = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        dep[name.strip()] = rest.split()
    mods = closure(dep)
    stage = pathlib.Path(work) / "stage"
    rel = stage / "lib/modules" / RELEASE
    limits = []
    for mod in mods:
        blob = subprocess.run(["zstd", "-d", "-c", str(src / mod)], capture_output=True)
        if blob.returncode != 0:
            raise SystemExit("zstd %s" % mod)
        if RELEASE.encode() not in blob.stdout:
            raise SystemExit("vermagic mismatch %s" % mod)
        for hit in strings_with(blob.stdout, b"firmware="):
            value = hit.split("=", 1)[1]
            if "*" in value or "?" in value:
                limits.append("firmware-wildcard %s absent-from-module-archive" % value)
            else:
                raise SystemExit("concrete firmware %s is not in the module archive" % value)
        dest = rel / mod
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / mod, dest)
    shutil.copy2(src / "modules.builtin", rel / "modules.builtin")
    if (src / "modules.builtin.modinfo").is_file():
        shutil.copy2(src / "modules.builtin.modinfo", rel / "modules.builtin.modinfo")
    (rel / "modules.order").write_text("".join(mod + "\n" for mod in mods))
    subprocess.run(["depmod", "-b", str(stage), RELEASE], check=True)
    names = [module_name(mod) for mod in mods]
    return stage, names, limits


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage2")
    parser.add_argument("modules")
    parser.add_argument("out")
    parser.add_argument("--telemetry-url", default="")
    args = parser.parse_args()
    check_url(args.telemetry_url)
    src = pathlib.Path(args.stage2).read_bytes()
    if hashlib.sha256(src).hexdigest() != WANT or len(src) != 18239729:
        raise SystemExit("input is not the trial stage2")
    mod_hash = hashlib.sha256(pathlib.Path(args.modules).read_bytes()).hexdigest()
    if mod_hash != MODULES_WANT:
        raise SystemExit("module archive hash mismatch")
    if struct.unpack_from("<I", src, SIZE_AT)[0] != EARLY_LEN + MID_LEN:
        raise SystemExit("initramfs size field moved")
    early = src[SIZE_AT + 4:SIZE_AT + 4 + EARLY_LEN]
    mid = src[SIZE_AT + 4 + EARLY_LEN:SIZE_AT + 4 + EARLY_LEN + MID_LEN]
    if early[:6] != b"070701" or mid[:4] != b"\x28\xb5\x2f\xfd":
        raise SystemExit("initramfs framing changed")
    decoded = subprocess.run(["zstd", "-d", "-c"], input=mid, capture_output=True)
    if decoded.returncode != 0:
        raise SystemExit("zstd decode failed")
    items = unpack_cpio(decoded.stdout)
    here = pathlib.Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as work:
        stage, names, limits = build_modules(args.modules, work)
        rel = pathlib.Path(stage) / "lib/modules" / RELEASE
        files = {}
        for path in rel.rglob("*"):
            rel_path = "usr/" + str(path.relative_to(stage))
            if path.is_dir():
                files[rel_path] = (0o40755, b"")
            elif path.is_file():
                files[rel_path] = (0o100644, path.read_bytes())
        files["usr/bin/elf-interp"] = (0o100755, (here / "elf-interp.sh").read_bytes())
        files["usr/bin/m4diag-lib.sh"] = (0o100755, (here / "m4diag-lib.sh").read_bytes())
        files["usr/share/udhcpc/m4.script"] = (0o100755, (here / "udhcpc-m4.sh").read_bytes())
        files["etc/m4diag-url"] = (0o100644, (args.telemetry_url + "\n").encode())
        files["etc/m4diag-host"] = (0o100644, b"")
        files["etc/m4diag-modules"] = (0o100644, ("".join(n + "\n" for n in names)).encode())
        limit_text = "\n".join(dict.fromkeys(limits + [
            "no-gadget",
            "no-fsck",
            "handoff-only-if-configured-init-verifies",
            "not-a-root-filesystem-repair",
        ])) + "\n"
        files["etc/m4diag-limits"] = (0o100644, limit_text.encode())
        have = {name for name, _, _ in items}
        merged = []
        replaced = False
        for name, mode, blob in items:
            if name == "TRAILER!!!":
                break
            if name == "init":
                blob = (here / "m4diag-init").read_bytes()
                mode = 0o100755
                replaced = True
            merged.append((name, mode, blob))
        if not replaced:
            raise SystemExit("init missing")

        def ensure_dir(path):
            if path == "" or path in have:
                return
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            ensure_dir(parent)
            merged.append((path, 0o40755, b""))
            have.add(path)

        for name in sorted(files, key=lambda n: (n.count("/"), n)):
            parent = name.rsplit("/", 1)[0] if "/" in name else ""
            ensure_dir(parent)
            if name in have and not name.startswith("usr/lib/modules/" + RELEASE):
                raise SystemExit("refusing to replace %s" % name)
            merged.append((name, files[name][0], files[name][1]))
            have.add(name)
        merged.append(("TRAILER!!!", 0, b""))
    packed = pack_cpio(merged)
    comp = subprocess.run(["zstd", "-3", "--long=23", "-c"], input=packed, capture_output=True)
    if comp.returncode != 0:
        raise SystemExit(comp.stderr.decode())
    zblob = comp.stdout
    with tempfile.NamedTemporaryFile(suffix=".zst") as fh:
        fh.write(zblob)
        fh.flush()
        info = subprocess.run(["zstd", "-l", "-v", fh.name], capture_output=True, text=True)
    if "8.00 MiB" not in info.stdout and "8388608" not in info.stdout:
        raise SystemExit("zstd window is not 8 MiB")
    suffix = patch_bootargs(src[SIZE_AT + 4 + EARLY_LEN + MID_LEN:])
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
    if RELEASE.encode() not in out:
        raise SystemExit("release string missing from the image")
    out_path = pathlib.Path(args.out)
    out_path.write_bytes(out)
    sys.path.insert(0, str(here))
    from macho_boot_object import wrap
    macho = wrap(bytes(out))
    macho_path = out_path.with_suffix(".macho")
    macho_path.write_bytes(macho)
    if b"__TEXT_BOOT_EXEC" in macho:
        raise SystemExit("wrapped object has __TEXT_BOOT_EXEC")
    if macho[0x10000 + 0x808:0x10000 + 0x808 + 8] != b"AURALOAD":
        raise SystemExit("wrapped raw image lost AURALOAD")
    print("MODULES %d" % len(names))
    for line in dict.fromkeys(limits):
        print("LIMIT %s" % line)
    print(hashlib.sha256(out).hexdigest(), len(out), "raw")
    print(hashlib.sha256(macho).hexdigest(), len(macho), "macho")


if __name__ == "__main__":
    main()
