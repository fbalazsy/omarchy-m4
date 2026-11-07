#!/usr/bin/env python3
"""Stamp the SSH read-only hold onto one exact m4diag raw image.

The kernel, module closure, firmware and bootargs stay byte for byte.
The result does not remount read-write and does not call switch_root.
"""
import hashlib, importlib.util, pathlib, struct, subprocess, sys, tempfile

BASE_SHA = "eb9256b93d99b14eeaf5f8ebe0fa89cd29189169d81dc01ab39c0f4581964082"
BASE_LEN = 20370063
SIZE_AT = 4103735
EARLY_LEN = 234816

def _load(name, filename):
    path = pathlib.Path(__file__).resolve().parent / filename
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

build = _load("build_m4diag", "build-m4diag.py")
macho_mod = _load("macho_boot_object", "macho_boot_object.py")
load_authorized_key = build.load_authorized_key
pack_cpio = build.pack_cpio
unpack_cpio = build.unpack_cpio
wrap = macho_mod.wrap


def member(items, name):
    for item in items:
        if item[0] == name:
            return item
    return None


def main():
    if len(sys.argv) != 5:
        raise SystemExit("usage: stamp-ssh-hold.py RAW KEY DROPBEAR OUT")
    raw_path, key_path, dropbear_path, out_path = sys.argv[1:]
    raw = pathlib.Path(raw_path).read_bytes()
    if len(raw) != BASE_LEN or hashlib.sha256(raw).hexdigest() != BASE_SHA:
        raise SystemExit("raw is not the proven m4diag image")
    authorized = load_authorized_key(key_path)
    dropbear = pathlib.Path(dropbear_path).read_bytes()
    if dropbear[:4] != b"\x7fELF":
        raise SystemExit("dropbear is not an ELF")
    total = struct.unpack_from("<I", raw, SIZE_AT)[0]
    early = raw[SIZE_AT + 4:SIZE_AT + 4 + EARLY_LEN]
    mid = raw[SIZE_AT + 4 + EARLY_LEN:SIZE_AT + 4 + total]
    suffix = raw[SIZE_AT + 4 + total:]
    if not suffix.startswith(b"chosen.bootargs="):
        raise SystemExit("bootargs missing")
    dec = subprocess.run(["zstd", "-d", "-c"], input=mid, capture_output=True)
    if dec.returncode != 0:
        raise SystemExit("zstd decode failed")
    items = unpack_cpio(dec.stdout)
    here = pathlib.Path(__file__).resolve().parent
    files = {
        "usr/bin/m4diag-lib.sh": (0o100755, (here / "m4diag-lib.sh").read_bytes()),
        "usr/share/udhcpc/m4.script": (0o100755, (here / "udhcpc-m4.sh").read_bytes()),
        "etc/m4diag-ssh": (0o100644, b"ro-hold\n"),
        "etc/m4diag-authorized_keys": (0o100600, authorized),
        "usr/sbin/dropbearmulti": (0o100755, dropbear),
    }
    have = {name for name, _, _ in items}
    if "etc/passwd" not in have:
        files["etc/passwd"] = (0o100644, b"root:x:0:0:root:/root:/bin/sh\n")
    if "etc/group" not in have:
        files["etc/group"] = (0o100644, b"root:x:0:\n")
    merged = []
    for name, mode, blob in items:
        if name == "TRAILER!!!":
            continue
        if name in files:
            mode, blob = files.pop(name)
        merged.append((name, mode, blob))

    def ensure_dir(path):
        if path == "" or path in have:
            return
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        ensure_dir(parent)
        merged.append((path, 0o040755, b""))
        have.add(path)

    for name in sorted(files):
        parent = name.rsplit("/", 1)[0] if "/" in name else ""
        ensure_dir(parent)
        merged.append((name, files[name][0], files[name][1]))
        have.add(name)
    merged.append(("TRAILER!!!", 0, b""))
    packed = pack_cpio(merged)
    comp = subprocess.run(["zstd", "-3", "--long=23", "-c"], input=packed, capture_output=True)
    if comp.returncode != 0:
        raise SystemExit("zstd encode failed")
    zblob = comp.stdout
    with tempfile.NamedTemporaryFile(suffix=".zst") as fh:
        fh.write(zblob)
        fh.flush()
        info = subprocess.run(["zstd", "-l", "-v", fh.name], capture_output=True, text=True)
    if "8.00 MiB" not in info.stdout and "8388608" not in info.stdout:
        raise SystemExit("zstd window is not 8 MiB")
    out = bytearray(raw[:SIZE_AT])
    out += struct.pack("<I", EARLY_LEN + len(zblob))
    out += early
    out += zblob
    out += suffix
    if out[SIZE_AT + 4 + EARLY_LEN + len(zblob):] != suffix:
        raise SystemExit("kernel suffix changed")
    outp = pathlib.Path(out_path)
    outp.write_bytes(out)
    macho = wrap(bytes(out))
    if b"__TEXT_BOOT_EXEC" in macho:
        raise SystemExit("boot exec segment present")
    machop = outp.with_suffix(".macho")
    machop.write_bytes(macho)
    print(hashlib.sha256(out).hexdigest(), len(out), "raw")
    print(hashlib.sha256(macho).hexdigest(), len(macho), "macho")


if __name__ == "__main__":
    main()
