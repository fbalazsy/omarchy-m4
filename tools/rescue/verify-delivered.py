#!/usr/bin/env python3
"""Check one delivered m4diag Mach-O. Does not rebuild it.

  verify-delivered.py MACHO --expect-url http://HOST:PORT/up [--scripts DIR]
"""
import hashlib, os, pathlib, struct, subprocess, sys, tempfile

IMAGE_OFFSET = 0x10000
SIZE_AT = 4103735
EARLY_LEN = 234816
FW = "7eb84312cb4ea74833ce9567ba8c92bc42a0c26c5c34d6bdfaa5cb65abfe4b26"
RELEASE = "7.3.0-m4.222-g55obs2"
MODULES = """
spi-apple
macsmc-input
apple-dockchannel
apple-rtkit-helper
dockchannel-hid
phy-apple-t6040-usb2
usb-common
usbcore
dwc3
roles
dwc3-apple
xhci-hcd
xhci-plat-hcd
usbhid
mii
usbnet
cdc_ether
cdc_ncm
r8153_ecm
cdc-acm
nvme-core
nvme
nvme-apple
""".split()


def unpack_cpio(buf):
    j = 0
    items = []
    while j + 110 <= len(buf) and buf[j:j + 6] == b"070701":
        ns = int(buf[j + 94:j + 102], 16)
        fs = int(buf[j + 54:j + 62], 16)
        name = buf[j + 110:j + 110 + ns].split(b"\0", 1)[0].decode()
        name_end = 110 + ns
        off = j + name_end + (4 - (name_end % 4)) % 4
        items.append((name, buf[off:off + fs]))
        if name == "TRAILER!!!":
            break
        j = off + ((fs + 3) & ~3)
    else:
        raise SystemExit("cpio truncated")
    return items


def members(blob):
    base = IMAGE_OFFSET
    if blob[base + SIZE_AT + 4:base + SIZE_AT + 10] != b"070701":
        raise SystemExit("initramfs framing missing")
    total = struct.unpack_from("<I", blob, base + SIZE_AT)[0]
    mid = blob[base + SIZE_AT + 4 + EARLY_LEN:base + SIZE_AT + 4 + total]
    dec = subprocess.run(["zstd", "-d", "-c"], input=mid, capture_output=True)
    if dec.returncode != 0:
        raise SystemExit("zstd decode failed")
    found = {name: blob for name, blob in unpack_cpio(dec.stdout)}
    suffix = blob[base + SIZE_AT + 4 + total:base + SIZE_AT + 4 + total + 4096]
    return found, suffix


def dhcp_case(script, env, expect_ok, expect_line):
    with tempfile.TemporaryDirectory() as td:
        td = pathlib.Path(td)
        mock = td / "ip"
        log = td / "ip.log"
        mock.write_text("#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$LOG/ip.log\"\nexit 0\n")
        mock.chmod(0o755)
        (td / "bin").mkdir()
        (td / "bin" / "ip").write_text(mock.read_text())
        (td / "bin" / "ip").chmod(0o755)
        e = os.environ.copy()
        e.update(env)
        e["PATH"] = str(td / "bin") + ":/usr/bin:/bin"
        e["LOG"] = str(td)
        r = subprocess.run(["sh", str(script), "bound"], env=e, capture_output=True, text=True)
        text = (td / "ip.log").read_text() if (td / "ip.log").exists() else ""
        if expect_ok and r.returncode != 0:
            raise SystemExit("dhcp case failed %s %s" % (env, r.stderr))
        if not expect_ok and r.returncode == 0:
            raise SystemExit("dhcp case should reject %s" % env)
        if expect_line and expect_line not in text:
            raise SystemExit("dhcp missing %r in %r" % (expect_line, text))
        if "default" in text:
            raise SystemExit("dhcp installed a default route")
        if not expect_ok and text:
            raise SystemExit("rejected dhcp still ran ip: %r" % text)


def main():
    if subprocess.run(["sh", "-c", "command -v zstd"], capture_output=True).returncode != 0:
        raise SystemExit("zstd is required (apt install zstd)")
    args = sys.argv[1:]
    if len(args) < 3 or args[1] != "--expect-url":
        raise SystemExit("usage: verify-delivered.py MACHO --expect-url URL [--scripts DIR]")
    macho_path = pathlib.Path(args[0])
    expect = args[2]
    scripts = None
    if "--scripts" in args:
        scripts = pathlib.Path(args[args.index("--scripts") + 1])
    blob = macho_path.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    side = macho_path.with_name(macho_path.name + ".sha256")
    if side.is_file():
        want = side.read_text().split()[0]
        if want != digest:
            raise SystemExit("sidecar does not match")
    if b"__TEXT_BOOT_EXEC" in blob:
        raise SystemExit("boot exec segment present")
    if blob[IMAGE_OFFSET + 0x808:IMAGE_OFFSET + 0x808 + 8] != b"AURALOAD":
        raise SystemExit("AURALOAD missing")
    found, suffix = members(blob)
    url = found["etc/m4diag-url"].decode().strip()
    if url != expect:
        raise SystemExit("url is not the expected production base")
    fw = found["usr/lib/firmware/apple/tpmtfw-j616s.bin"]
    if len(fw) != 81424 or hashlib.sha256(fw).hexdigest() != FW:
        raise SystemExit("firmware bytes differ")
    mods = found["etc/m4diag-modules"].decode().split()
    if mods != MODULES:
        raise SystemExit("module list differs")
    lib = found["usr/bin/m4diag-lib.sh"].decode()
    if "exec /usr/bin/switch_root" not in lib:
        raise SystemExit("lib does not exec the packed switch_root")
    if "M4_EXEC_INIT" in lib or "handoff_root" in lib or 'mount --move "$root"' in lib:
        raise SystemExit("lib still contains the shell mount-move handoff")
    if "resolve_under" not in lib:
        raise SystemExit("in-root symlink resolver missing")
    if "etc/m4diag-ssh" in found:
        ssh_mode = found["etc/m4diag-ssh"].strip()
        if ssh_mode not in (b"ro-hold", b"boot"):
            raise SystemExit("ssh mode is not ro-hold or boot")
        if b"USB_GADGET_UNSUPPORTED" not in lib.encode():
            raise SystemExit("gadget refusal is not logged")
        key = found["etc/m4diag-authorized_keys"]
        if b"PRIVATE KEY" in key or key.count(b"\n") > 1:
            raise SystemExit("authorized keys file is not one public line")
        if not found.get("usr/sbin/dropbearmulti", b"").startswith(b"\x7fELF"):
            raise SystemExit("dropbear missing")
        if ssh_mode == b"ro-hold":
            print("SSH_HOLD ro-hold")
        else:
            if "FALLBACK_WIFI pre-exec-only" not in lib or "no fallback after this exec" not in lib:
                raise SystemExit("boot mode does not keep the pre-exec fallback")
            hook = found.get("usr/share/udhcpc/m4.script", b"")
            if b"same_prefix" not in hook or b"0.0.0.0/0" in hook:
                raise SystemExit("boot mode dhcp hook is not the on-link route fix")
            print("BOOT_NORMAL")
    wifi_bin = "usr/lib/firmware/brcm/brcmfmac4388c0-pcie.apple,hostomel.bin"
    if wifi_bin in found:
        wifi_sha = {
            wifi_bin: "8b18acb6116622c6161de80ef510dfa9afc47dd963440f090770e57d14b3e928",
            "usr/lib/firmware/brcm/brcmfmac4388c0-pcie.apple,hostomel.clm_blob":
                "fe969bc12cabe622ec493f0903dff1ea3d6c9ba3d1e6f433615c26e9eba8b491",
            "usr/lib/firmware/brcm/brcmfmac4388c0-pcie.apple,hostomel.sig":
                "a772cc7ae1d7893b71476c7f79b6746a2a504b1c3a9daa14502c2aab74759710",
            "usr/lib/firmware/brcm/brcmfmac4388c0-pcie.apple,hostomel.txcap_blob":
                "8797b79bafc5936468c33d2b3f02edc245c8b2f782da64324984b6dc72ef0a5f",
            "usr/lib/firmware/brcm/brcmfmac4388c0-pcie.apple,hostomel-WLMT-a.txt":
                "5774de6da1e0331ec269f656752d0c2cb9363e3f12d281b9a517dd203fb05f31",
            "usr/lib/firmware/brcm/brcmfmac4388c0-pcie.apple,hostomel-WLMT-u.txt":
                "d665bb8474c13afd39bad3bd0e0752e72dafcc0ea291f7dee330cc85468eea1e",
        }
        for fw_name, fw_digest in wifi_sha.items():
            fw_blob = found[fw_name]
            if fw_blob.startswith(b"\x28\xb5\x2f\xfd") or hashlib.sha256(fw_blob).hexdigest() != fw_digest:
                raise SystemExit("wifi firmware missing or compressed")
        link = found.get("usr/lib/firmware/brcm/brcmfmac4388c2-pcie.apple,hostomel.bin", b"")
        if link != b"brcmfmac4388c0-pcie.apple,hostomel.bin":
            raise SystemExit("c2 firmware is not a relative symlink to c0")
        for leaf in ("cfg80211.ko.zst", "brcmutil.ko.zst", "brcmfmac.ko.zst",
                     "brcmfmac-cyw.ko.zst", "brcmfmac-wcc.ko.zst", "brcmfmac-bca.ko.zst"):
            if not any(name.endswith("/" + leaf) for name in found):
                raise SystemExit(f"wifi module {leaf} missing")
        dep = found[f"usr/lib/modules/{RELEASE}/modules.dep"].decode()
        if "brcmfmac.ko" not in dep or "cfg80211.ko" not in dep:
            raise SystemExit("modules.dep does not include wifi")
        if not found.get("usr/sbin/wpa_supplicant", b"").startswith(b"\x7fELF"):
            raise SystemExit("wpa_supplicant missing")
        if b"wifi_setup" not in lib.encode() or b"WIFI_FIRMWARE_ABSENT" not in lib.encode():
            raise SystemExit("wifi hold is not in the init")
        for name in ("usr/bin/m4diag-lib.sh", "usr/bin/nm-wifi.awk", "usr/bin/nm-wifi.sh"):
            if b"fixture-psk" in found.get(name, b"") or b"PRIVATE KEY" in found.get(name, b""):
                raise SystemExit("wifi script contains a secret marker")
        print("WIFI_HOLD hostomel")
    line = suffix.split(b"\n", 1)[0]
    if b"init=/usr/local/sbin/rover-init" not in line or b"noresume" not in line:
        raise SystemExit("bootargs init/noresume missing")
    if b"resume=" in line or b"initcall_debug" in line:
        raise SystemExit("resume or initcall_debug still on the bootargs line")
    if b"\nhib=0\n" not in suffix or b"chosen.aura,nv2=1" not in suffix:
        raise SystemExit("aura hib/nv2 config missing")
    hook = pathlib.Path(tempfile.mkdtemp()) / "udhcpc-m4.sh"
    hook.write_bytes(found["usr/share/udhcpc/m4.script"])
    hook.chmod(0o755)
    base = dict(interface="eth0", ip="192.0.2.10", router="192.0.2.1",
                M4_TELEMETRY_HOST="192.0.2.25")
    dhcp_case(hook, dict(base, subnet="255.255.255.0", mask="24"), True,
              "addr add 192.0.2.10/24 dev eth0")
    dhcp_case(hook, dict(base, subnet="255.0.255.0", mask="24"), False, "")
    if scripts is not None:
        here = pathlib.Path(__file__).resolve().parent
        checker = here / "privacy-check.py"
        if not checker.is_file():
            checker = here.parent / "privacy-check.py"
        if not checker.is_file():
            checker = scripts / "privacy-check.py"
        if not checker.is_file():
            raise SystemExit("privacy-check.py is not next to this script or --scripts")
        r = subprocess.run([sys.executable, str(checker), str(scripts)], capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit(r.stdout + r.stderr)
        print(r.stdout.strip())
    print("DELIVERED", digest, len(blob))
    print("URL", url)
    print("FIRMWARE", FW)
    print("MODULES", len(mods))
    print("RELEASE", RELEASE)
    print("verify-delivered: ok")


if __name__ == "__main__":
    main()
