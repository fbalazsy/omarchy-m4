#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Wrap a raw Aura image in a Mach-O boot object without __TEXT_BOOT_EXEC.

`kmutil configure-boot` without `--raw` records the boot object's segment
layout in its boot policy, including the size of __TEXT_BOOT_EXEC (kcbz).
macOS 27.2 iBoot loads and enters SPTM only when kcbz is nonzero; with no
such segment it enters the object directly, as it enters a raw boot object.

The raw image is carried byte for byte and file offsets equal virtual
offsets, so iBoot places it exactly as in raw mode, only after two header
pages. Entry is the raw image's own entry point (`--entry-point 2048` in raw
mode). A Stage 1 configuration block filled into the raw image before
wrapping, or found by its magic afterwards, is unchanged.
"""

import argparse
import hashlib
import plistlib
import struct
from pathlib import Path


PAGE = 0x4000
VA_BASE = 0xFFFFFE0007004000
HEADER_SIZE = 0x8000          # __TEXT: this fileset header
KERNEL_HEADER_SIZE = 0x8000   # __PRELINK_TEXT: the primary member's header
IMAGE_OFFSET = HEADER_SIZE + KERNEL_HEADER_SIZE
ENTRY_OFFSET = 0x800          # start.S: the vector table, then _start
LOAD_HEADER_OFFSET = 0x808
LOAD_HEADER_MAGIC = b"AURALOAD"

MH_MAGIC_64 = 0xFEEDFACF
CPU_TYPE_ARM64 = 0x0100000C
CPU_SUBTYPE_ARM64E_KERNEL = 0xC0000002
MH_EXECUTE = 2
MH_FILESET = 12
MH_FILESET_MEMBER_FLAGS = 0x80200001  # NOUNDEFS | PIE | DYLIB_IN_CACHE

LC_SYMTAB = 0x2
LC_UNIXTHREAD = 0x5
LC_DYSYMTAB = 0xB
LC_SEGMENT_64 = 0x19
LC_UUID = 0x1B
LC_BUILD_VERSION = 0x32
LC_DYLD_CHAINED_FIXUPS = 0x80000034
LC_FILESET_ENTRY = 0x80000035

PROT_R, PROT_W, PROT_X = 1, 2, 4
S_ATTR_INSTRUCTIONS = 0x80000400  # S_ATTR_PURE_INSTRUCTIONS | S_ATTR_SOME_INSTRUCTIONS
ARM_THREAD_STATE64 = 6
ARM_THREAD_STATE64_COUNT = 0x44
PRIMARY_MEMBER = b"com.apple.kernel"  # kmutil requires this fileset member


def check_raw_image(raw):
    """Raise unless this is a raw Aura image (entered at its 0x800 offset)."""
    if raw[:4] == struct.pack("<I", MH_MAGIC_64):
        raise ValueError("input is already a Mach-O image")
    if raw[LOAD_HEADER_OFFSET:LOAD_HEADER_OFFSET + 8] != LOAD_HEADER_MAGIC:
        raise ValueError("raw image lacks Aura's load header after its entry point")


def segment(name, vmaddr, size, prot, sections=()):
    """A fully file-backed segment whose file offset equals its virtual offset."""
    command = struct.pack("<II16sQQQQIIII", LC_SEGMENT_64, 72 + 80 * len(sections),
                          name.encode(), vmaddr, size, vmaddr - VA_BASE, size,
                          prot, prot, len(sections), 0)
    for section, address, length, flags in sections:
        command += struct.pack("<16s16sQQIIIIIIII", section.encode(), name.encode(),
                               address, length, address - VA_BASE, 0, 0, 0, flags, 0, 0, 0)
    return command


def thread(entry, vectors):
    registers = [0] * 34
    # kmutil 27.2 records x16 as kcev; the stock BootKC points it into __TEXT_EXEC.
    registers[16] = vectors
    registers[32] = entry    # pc
    return struct.pack("<4I34Q", LC_UNIXTHREAD, 16 + 34 * 8, ARM_THREAD_STATE64,
                       ARM_THREAD_STATE64_COUNT, *registers)


def header(filetype, flags, commands):
    payload = b"".join(commands)
    return struct.pack("<8I", MH_MAGIC_64, CPU_TYPE_ARM64, CPU_SUBTYPE_ARM64E_KERNEL,
                       filetype, len(commands), len(payload), flags, 0) + payload


def chained_fixups(segment_count):
    """A valid fixup table with no fixups: Aura relocates itself at entry."""
    starts = struct.pack(f"<I{segment_count}I", segment_count, *([0] * segment_count))
    starts_offset = 28
    imports_offset = starts_offset + len(starts)
    return struct.pack("<7I", 0, starts_offset, imports_offset, imports_offset, 0, 1, 0) + starts


def wrap(raw):
    check_raw_image(raw)
    # Zero padding keeps every segment page-sized; it lies after the image's end.
    size = (len(raw) + PAGE - 1) & ~(PAGE - 1)
    identity = hashlib.sha256(raw).digest()[:16]

    text = VA_BASE
    kernel = VA_BASE + HEADER_SIZE
    image = VA_BASE + IMAGE_OFFSET
    info = image + size
    data = info + PAGE
    linkedit = data + PAGE
    end = linkedit + PAGE
    entry = image + ENTRY_OFFSET

    prelink = plistlib.dumps({
        "KMBuildVersion": "Aura",
        "ManagerVersion": 1,
        "OSPrelinkInfoContainsUUIDs": True,
        "_PrelinkInfoDictionary": [],
        "_PrelinkKCID": identity,
    }, fmt=plistlib.FMT_XML, sort_keys=True)
    if len(prelink) > PAGE:
        raise ValueError("prelink information does not fit its page")

    segments = [
        segment("__TEXT", text, HEADER_SIZE, PROT_R),
        segment("__PRELINK_TEXT", kernel, KERNEL_HEADER_SIZE, PROT_R,
                [("__text", kernel, KERNEL_HEADER_SIZE, S_ATTR_INSTRUCTIONS)]),
        segment("__TEXT_EXEC", image, size, PROT_R | PROT_X),
        segment("__PRELINK_INFO", info, PAGE, PROT_R | PROT_W,
                [("__info", info, len(prelink), 0)]),
        segment("__DATA", data, PAGE, PROT_R | PROT_W),
        segment("__LINKEDIT", linkedit, PAGE, PROT_R),
    ]
    fixups = chained_fixups(len(segments))
    member_size = (32 + len(PRIMARY_MEMBER) + 1 + 7) & ~7
    member = struct.pack("<IIQQII", LC_FILESET_ENTRY, member_size, kernel,
                         kernel - VA_BASE, 32, 0) + PRIMARY_MEMBER.ljust(member_size - 32, b"\0")
    common = [
        struct.pack("<II16s", LC_UUID, 24, identity),
        struct.pack("<6I", LC_BUILD_VERSION, 24, 0, 0, 0, 0),
        thread(entry, image),
    ]
    outer = header(MH_FILESET, 0, common + [
        struct.pack("<4I", LC_DYLD_CHAINED_FIXUPS, 16, linkedit - VA_BASE, len(fixups)),
        *segments, member,
    ])
    primary = header(MH_EXECUTE, MH_FILESET_MEMBER_FLAGS, [
        segment("__TEXT", kernel, KERNEL_HEADER_SIZE, PROT_R),
        segments[2], segments[4], segments[5],
        struct.pack("<6I", LC_SYMTAB, 24, 0, 0, 0, 0),
        struct.pack("<20I", LC_DYSYMTAB, 80, *([0] * 18)),
        *common,
    ])
    if len(outer) > HEADER_SIZE or len(primary) > KERNEL_HEADER_SIZE:
        raise ValueError("Mach-O headers do not fit their pages")

    result = bytearray(end - VA_BASE)
    result[:len(outer)] = outer
    result[HEADER_SIZE:HEADER_SIZE + len(primary)] = primary
    result[IMAGE_OFFSET:IMAGE_OFFSET + len(raw)] = raw
    result[info - VA_BASE:info - VA_BASE + len(prelink)] = prelink
    result[linkedit - VA_BASE:linkedit - VA_BASE + len(fixups)] = fixups
    return bytes(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("raw", type=Path, help="raw Aura image, e.g. build/aura.bin")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.raw.resolve() == args.output.resolve():
        parser.error("use a separate output path")
    result = wrap(args.raw.read_bytes())
    args.output.write_bytes(result)
    print(f"  BOOT  {args.output}: {len(result)} bytes, entry at file offset "
          f"{IMAGE_OFFSET + ENTRY_OFFSET:#x}, no __TEXT_BOOT_EXEC")


if __name__ == "__main__":
    main()
