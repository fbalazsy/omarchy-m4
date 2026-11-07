#!/usr/bin/env python3
"""Stamp a read-only Wi-Fi SSH hold onto the proven m4diag raw image.

The kernel and bootargs stay byte for byte. The 23-module closure and the
trackpad firmware stay. Six matched wifi modules and uncompressed hostomel
firmware are added. Credentials are not packed.
"""
import hashlib, importlib.util, os, pathlib, shutil, stat, struct, subprocess, sys, tempfile, zipfile

BASE_SHA = "eb9256b93d99b14eeaf5f8ebe0fa89cd29189169d81dc01ab39c0f4581964082"
BASE_LEN = 20370063
SIZE_AT = 4103735
EARLY_LEN = 234816
RELEASE = "7.3.0-m4.222-g55obs2"
MOD_PREFIX = f"usr/lib/modules/{RELEASE}/"
FW_DIR = "usr/lib/firmware/brcm"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"

FW_SHA = {
    "brcmfmac4388c0-pcie.apple,hostomel.bin":
        "8b18acb6116622c6161de80ef510dfa9afc47dd963440f090770e57d14b3e928",
    "brcmfmac4388c0-pcie.apple,hostomel.clm_blob":
        "fe969bc12cabe622ec493f0903dff1ea3d6c9ba3d1e6f433615c26e9eba8b491",
    "brcmfmac4388c0-pcie.apple,hostomel.sig":
        "a772cc7ae1d7893b71476c7f79b6746a2a504b1c3a9daa14502c2aab74759710",
    "brcmfmac4388c0-pcie.apple,hostomel.txcap_blob":
        "8797b79bafc5936468c33d2b3f02edc245c8b2f782da64324984b6dc72ef0a5f",
    "brcmfmac4388c0-pcie.apple,hostomel-WLMT-a.txt":
        "5774de6da1e0331ec269f656752d0c2cb9363e3f12d281b9a517dd203fb05f31",
    "brcmfmac4388c0-pcie.apple,hostomel-WLMT-u.txt":
        "d665bb8474c13afd39bad3bd0e0752e72dafcc0ea291f7dee330cc85468eea1e",
}
FW_LINK = {
    "brcmfmac4388c2-pcie.apple,hostomel.bin":
        "brcmfmac4388c0-pcie.apple,hostomel.bin",
    "brcmfmac4388c2-pcie.apple,hostomel.clm_blob":
        "brcmfmac4388c0-pcie.apple,hostomel.clm_blob",
    "brcmfmac4388c2-pcie.apple,hostomel.sig":
        "brcmfmac4388c0-pcie.apple,hostomel.sig",
    "brcmfmac4388c2-pcie.apple,hostomel.txcap_blob":
        "brcmfmac4388c0-pcie.apple,hostomel.txcap_blob",
    "brcmfmac4388c2-pcie.apple,hostomel-WLMT-a.txt":
        "brcmfmac4388c0-pcie.apple,hostomel-WLMT-a.txt",
    "brcmfmac4388c2-pcie.apple,hostomel-WLMT-u.txt":
        "brcmfmac4388c0-pcie.apple,hostomel-WLMT-u.txt",
}
KO_SIZE = {
    "kernel/net/wireless/cfg80211.ko.zst": 386338,
    "kernel/drivers/net/wireless/broadcom/brcm80211/brcmutil/brcmutil.ko.zst": 5181,
    "kernel/drivers/net/wireless/broadcom/brcm80211/brcmfmac/brcmfmac.ko.zst": 141820,
    "kernel/drivers/net/wireless/broadcom/brcm80211/brcmfmac/cyw/brcmfmac-cyw.ko.zst": 5161,
    "kernel/drivers/net/wireless/broadcom/brcm80211/brcmfmac/wcc/brcmfmac-wcc.ko.zst": 1611,
    "kernel/drivers/net/wireless/broadcom/brcm80211/brcmfmac/bca/brcmfmac-bca.ko.zst": 1591,
}
WIFI_LIST = b"cfg80211\nbrcmutil\nbrcmfmac\nbrcmfmac-cyw\nbrcmfmac-wcc\nbrcmfmac-bca\n"


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


def is_link(info):
    return stat.S_ISLNK(info.external_attr >> 16)


def read_fw(zpath):
    z = zipfile.ZipFile(zpath)
    listed = {}
    for line in z.read("SYMLINKS.txt").decode().splitlines():
        if not line.startswith("LINKOK "):
            raise SystemExit("symlink list is not LINKOK")
        left, _, target = line[len("LINKOK "):].partition(" -> ")
        name = left.rsplit("/", 1)[-1]
        listed[name] = target
    if listed != FW_LINK:
        raise SystemExit("c2 symlink list does not match the hostomel set")
    files = {}
    for info in z.infolist():
        base = info.filename.rsplit("/", 1)[-1]
        if base not in FW_SHA and base not in FW_LINK:
            continue
        if "/firmware/brcm/" not in info.filename:
            continue
        blob = z.read(info)
        if is_link(info):
            if blob.decode() != FW_LINK[base]:
                raise SystemExit(f"symlink target mismatch for {base}")
            files[base] = ("link", blob)
        else:
            if base not in FW_SHA:
                raise SystemExit(f"unexpected regular firmware {base}")
            if blob.startswith(ZSTD_MAGIC) or blob.startswith(b"\x1f\x8b"):
                raise SystemExit(f"{base} is compressed")
            if hashlib.sha256(blob).hexdigest() != FW_SHA[base]:
                raise SystemExit(f"firmware hash mismatch for {base}")
            files[base] = ("file", blob)
    if set(files) != set(FW_SHA) | set(FW_LINK):
        raise SystemExit("firmware archive is missing a hostomel file")
    return files


def check_module_zip(zpath):
    z = zipfile.ZipFile(zpath)
    manifest = z.read("MANIFEST.sha256").decode().splitlines()
    for line in manifest:
        if not line.strip():
            continue
        digest, _, name = line.partition("  ")
        name = name[2:] if name.startswith("./") else name
        if name == "MANIFEST.sha256":
            continue
        blob = z.read(name)
        if hashlib.sha256(blob).hexdigest() != digest:
            raise SystemExit(f"module archive hash mismatch for {name}")
    rel = z.read("kernel.release")
    if rel != f"{RELEASE}\n".encode():
        raise SystemExit("module archive release mismatch")
    kos = {}
    for rel_name, size in KO_SIZE.items():
        arc = f"lib/modules/{RELEASE}/{rel_name}"
        blob = z.read(arc)
        if len(blob) != size or not blob.startswith(ZSTD_MAGIC):
            raise SystemExit(f"wifi module size or compression mismatch for {rel_name}")
        kos[rel_name] = blob
    builtin = z.read(f"lib/modules/{RELEASE}/modules.builtin")
    modinfo = z.read(f"lib/modules/{RELEASE}/modules.builtin.modinfo")
    return kos, builtin, modinfo


def depmod_tree(items, kos, builtin, modinfo):
    root = pathlib.Path(tempfile.mkdtemp(prefix="m4wifi-depmod-"))
    try:
        tree = root / "lib" / "modules" / RELEASE
        for name, _mode, blob in items:
            if not name.startswith(MOD_PREFIX + "kernel/") or not name.endswith(".ko.zst"):
                continue
            rel = name[len(MOD_PREFIX):]
            dest = tree / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
        for rel, blob in kos.items():
            dest = tree / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
        (tree / "modules.builtin").write_bytes(builtin)
        (tree / "modules.builtin.modinfo").write_bytes(modinfo)
        run = subprocess.run(["depmod", "-b", str(root), RELEASE], capture_output=True, text=True)
        if run.returncode != 0:
            raise SystemExit(f"depmod failed: {run.stderr}")
        dep = (tree / "modules.dep").read_text()
        if "brcmfmac.ko" not in dep or "cfg80211.ko" not in dep or "brcmutil.ko" not in dep:
            raise SystemExit("depmod did not record the wifi closure")
        for vend in ("brcmfmac-cyw", "brcmfmac-wcc", "brcmfmac-bca"):
            line = next((ln for ln in dep.splitlines() if ln.startswith(f"kernel/") and f"/{vend}.ko" in ln), "")
            if "brcmfmac.ko" not in line.split(":", 1)[-1]:
                raise SystemExit(f"{vend} does not depend on brcmfmac")
        out = {}
        for leaf in ("modules.alias", "modules.alias.bin", "modules.builtin",
                     "modules.builtin.bin", "modules.builtin.modinfo", "modules.dep",
                     "modules.dep.bin", "modules.devname", "modules.softdep",
                     "modules.symbols", "modules.symbols.bin"):
            path = tree / leaf
            if path.is_file():
                out[MOD_PREFIX + leaf] = path.read_bytes()
        mounted = subprocess.run(["findmnt", "-R", "-n", str(root)], capture_output=True, text=True)
        if mounted.stdout.strip():
            raise SystemExit("depmod tree is mounted; not removing it")
        return out
    finally:
        mounted = subprocess.run(["findmnt", "-R", "-n", str(root)], capture_output=True, text=True)
        if mounted.stdout.strip():
            raise SystemExit(f"refusing to remove mounted {root}")
        shutil.rmtree(root)


def main():
    if len(sys.argv) != 9:
        raise SystemExit("usage: stamp-wifi-hold.py RAW KEY DROPBEAR WPA WPA_CLI FW_ZIP MOD_ZIP OUT")
    raw_path, key_path, dropbear_path, wpa_path, wpa_cli_path, fw_zip, mod_zip, out_path = sys.argv[1:]
    raw = pathlib.Path(raw_path).read_bytes()
    if len(raw) != BASE_LEN or hashlib.sha256(raw).hexdigest() != BASE_SHA:
        raise SystemExit("raw is not the proven m4diag image")
    authorized = load_authorized_key(key_path)
    dropbear = pathlib.Path(dropbear_path).read_bytes()
    wpa = pathlib.Path(wpa_path).read_bytes()
    wpa_cli = pathlib.Path(wpa_cli_path).read_bytes()
    for label, blob in (("dropbear", dropbear), ("wpa_supplicant", wpa), ("wpa_cli", wpa_cli)):
        if not blob.startswith(b"\x7fELF"):
            raise SystemExit(f"{label} is not an ELF")
    if b"fixture-psk" in wpa or b"PRIVATE KEY" in authorized:
        raise SystemExit("refusing to pack a fixture secret or a private key")
    fw = read_fw(fw_zip)
    kos, builtin, modinfo = check_module_zip(mod_zip)
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
    indexes = depmod_tree(items, kos, builtin, modinfo)
    here = pathlib.Path(__file__).resolve().parent
    files = {
        "usr/bin/m4diag-lib.sh": (0o100755, (here / "m4diag-lib.sh").read_bytes()),
        "usr/bin/nm-wifi.awk": (0o100644, (here / "nm-wifi.awk").read_bytes()),
        "usr/bin/nm-wifi.sh": (0o100755, (here / "nm-wifi.sh").read_bytes()),
        "usr/share/udhcpc/m4.script": (0o100755, (here / "udhcpc-m4.sh").read_bytes()),
        "etc/m4diag-ssh": (0o100644, b"ro-hold\n"),
        "etc/m4diag-authorized_keys": (0o100600, authorized),
        "etc/m4diag-wifi-modules": (0o100644, WIFI_LIST),
        "usr/sbin/dropbearmulti": (0o100755, dropbear),
        "usr/sbin/wpa_supplicant": (0o100755, wpa),
        "usr/sbin/wpa_cli": (0o100755, wpa_cli),
    }
    for name, blob in indexes.items():
        files[name] = (0o100644, blob)
    for rel, blob in kos.items():
        files[MOD_PREFIX + rel] = (0o100644, blob)
    for name, (kind, blob) in fw.items():
        mode = 0o120777 if kind == "link" else 0o100644
        files[f"{FW_DIR}/{name}"] = (mode, blob)
    have = {name for name, _, _ in items}
    if "etc/passwd" not in have:
        files["etc/passwd"] = (0o100644, b"root:x:0:0:root:/root:/bin/sh\n")
    if "etc/group" not in have:
        files["etc/group"] = (0o100644, b"root:x:0:\n")
    limits = next((blob for name, _, blob in items if name == "etc/m4diag-limits"), b"")
    extra = b"wifi-fw-uncompressed-hostomel\nwifi-credentials-runtime-nm-only\nno-psk-in-logs\n"
    if extra not in limits:
        files["etc/m4diag-limits"] = (0o100644, limits + extra)
    merged = []
    for name, mode, blob in items:
        if name == "TRAILER!!!":
            continue
        if name in files:
            mode, blob = files.pop(name)
        merged.append((name, mode, blob))
        have.add(name)

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
    for name in ("usr/bin/m4diag-lib.sh", "usr/bin/nm-wifi.awk", "usr/bin/nm-wifi.sh",
                 "etc/m4diag-limits", "etc/m4diag-wifi-modules", "etc/m4diag-authorized_keys"):
        blob = next(item[2] for item in merged if item[0] == name)
        if b"fixture-psk" in blob or b"PRIVATE KEY" in blob:
            raise SystemExit(f"secret marker in {name}")
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
