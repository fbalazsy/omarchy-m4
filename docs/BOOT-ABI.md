# Boot ABI v1 (loader rev 11, rev 12) — the binding spec

Everything the loader reads is under `/boot/m4` of the Linux root file system (ext4, partition p4 of the internal SSD, the
file system `m4-boot` mounts as `/`; there is no separate boot partition). The loader only reads. Linux writes.

## Files

```
/boot/m4/loader.conf                      ≤ 4096 B, ASCII, key=value lines
/boot/m4/entries/<entry>/entry.conf       ≤ 4096 B, ASCII, key=value lines
/boot/m4/entries/<entry>/Image            the kernel
/boot/m4/entries/<entry>/initramfs.img    the initramfs
/boot/m4/entries/<entry>/board.dtb        the device tree, built from the kernel tree (dts/t6040-j616s.dts)
/boot/m4/boot-pending                     (optional) presence = the previous boot did not complete
```

Text format (both conf files): one `key=value` per line, `\n` or `\r\n`; blank lines and lines starting with `#` are
ignored; keys are `[A-Za-z0-9_.]`; the value runs to the end of the line and is printable ASCII (0x20–0x7e). **A key that
appears twice, or a line that is neither blank, comment nor `key=value`, makes the file invalid** (→ rescue, `bad-conf`).
Unknown keys are ignored (forward compatibility inside `abi=1`).

### loader.conf
```
abi=1
default=<entry>          # [A-Za-z0-9._+-]{1,63}, not starting with '.', no ".."
```

### entry.conf
```
abi=1
kver=<kernel release>            # same token charset as an entry name (e.g. 7.1.13-m4)
cmdline=<kernel command line>    # 1..1024 bytes, one line; copied verbatim to /chosen bootargs
Image_size=<decimal bytes>             Image_crc32=<8 lower-case hex>          Image_sha256=<64 hex>
initramfs.img_size=<decimal bytes>     initramfs.img_crc32=<8 hex>             initramfs.img_sha256=<64 hex>
board.dtb_size=<decimal bytes>         board.dtb_crc32=<8 hex>                 board.dtb_sha256=<64 hex>
```
* `<file>_size` and `<file>_crc32` are **required and verified by the loader**. `<file>_crc32` is the IEEE CRC-32 of the file
  (what `zlib.crc32(data) & 0xffffffff` returns, formatted `%08x`).
* `<file>_sha256` is **not read by the loader** (see "Digest decision"); `m4-boot` writes it and Linux-side tools verify it.
* The loader never writes `cmdline`, never appends to it for an entry, and has no compiled-in command line.

## Decision (all in the loader, in this order)

1. The ANS/NVMe session is brought up from the embedded rescue `board.dtb` addresses (cross-checked against the ADT); GPT p4;
   ext4 mount. A file system that is not clean (`needs_recovery`, state not `VALID` or `ERROR`) → rescue, `ext4-dirty`.
2. `/boot/m4/boot-pending` exists (**any** type, any content, empty included) → rescue, `previous-boot-incomplete`.
3. `loader.conf`: missing → `no-loader-conf`; `abi` ≠ 1 → `bad-abi`; no/invalid `abi`/`default` → `bad-conf`.
4. `entry.conf` of `default`: missing → `no-entry`; `abi` ≠ 1 → `bad-abi`; `kver`/`cmdline`/`*_size`/`*_crc32` missing or
   malformed, `cmdline` > 1024 B → `bad-conf`.
5. Files, `board.dtb` first, then `Image`, then `initramfs.img`: missing/not regular → `missing-file`; size ≠ `_size` →
   `size-mismatch`; CRC ≠ `_crc32` → `digest-mismatch`. `Image`: ARM64 header (`ARM\x64`, text_offset 0, 16 KiB pages,
   image_size ≥ file, multiple of 4 KiB) else `bad-image`; image_size > the kernel window → `too-large`. `board.dtb`:
   a complete flattened tree whose totalsize equals its size (libfdt `fdt_check_full`), else `bad-dtb`.
6. All good → boot the entry: `source=entry`. Any refusal, or an abort anywhere in the attempt → the **embedded rescue set**.

If the entry's `board.dtb` passes (5) but the loader's fix-ups refuse it (a placeholder is missing, the DT does not fit),
the rescue set boots with `m4,boot-reason=bad-board-dtb` (`m4,boot-detail=fixup-step-N`).

Sizes need no alignment: any byte length is accepted up to the limits, and `linux,initrd-end` is start + the true `_size`.
Limits: `Image` memory (header image_size) ≤ 29,949,952 B; `initramfs.img` ≤ 16,777,216 B; `board.dtb` ≤ 36,864 B on rev 11
(the final tree must fit 64 KiB after the loader adds ~27 KB of per-unit blobs), ≤ 98,304 B on rev 12 (128 KiB slot; a
98,304 B board.dtb becomes a 124,908 B tree). A larger `board.dtb` → rescue, `too-large`. rev 12 publishes its limit as
`/chosen/m4,board-dtb-max`; rev 11 has no such property. `m4-boot install` refuses a `board.dtb` above the limit the
running loader publishes, or above 36,864 B when it publishes none; a `dtb_max` line in `/usr/share/m4/abi.conf` overrides both.

## What the loader publishes (`/chosen`)

| property | value |
|---|---|
| `m4,loader-abi` | `<1>` (u32) |
| `m4,loader-version` | release id string (`rev11`, `rev12`, or what the carrier build stamped; N1's rev 11 says `N1-20261014`) |
| `m4,board-dtb-max` | (rev 12) `<98304>` (u32): the largest `board.dtb` this loader accepts. Absent on rev 11 (= 36,864) |
| `m4,boot-source` | `entry` \| `rescue` |
| `m4,boot-entry` | the entry name, or `rescue` |
| `m4,boot-reason` | `default` \| `previous-boot-incomplete` \| `ext4-dirty` \| `no-loader-conf` \| `bad-abi` \| `bad-conf` \| `no-entry` \| `missing-file` \| `size-mismatch` \| `digest-mismatch` \| `bad-image` \| `too-large` \| `bad-dtb` \| `bad-board-dtb` \| `ssd-unreadable` \| `ssd-unavailable` \| `loader-disabled` |
| `m4,boot-detail` | (rescue only) the status word, `:`file or step, e.g. `size:board.dtb`, `abort:probe`, `guard-refused-3` |
| `m4,ans-live`, `m4,ans-syslog` | the live ANS/RTKit session nvme-apple adopts (unchanged from rev 10) |
| `m4,cpu-idle-regs`, `m4,direct-peers-v2`, `lab,direct-desktop-capture-v2`, `m4,direct-desktop-contract`, `m4,usb2-right-contract`, `rng-seed` | unchanged from rev 10 |

Gone: `m4,carrier-*`, `m4,level1-*`, `m4,payload-*`, the `m4,*-fdt-status`/`m4,media-probe*`/`m4,bt-boot-*`/`m4,boot-profile`/
`m4,boot-timing` diagnostic records, the `m4-display-handoff` and `m4-media-handoff` nodes/blobs. No carrier-id interlock,
no export/sync.

## Rescue set

Embedded in the carrier (loader image): the N1 release's `Image` + `board.dtb` + lane U's rescue initramfs + a command line
text. **The rescue command line always contains `m4.safe=1`**: the carrier build adds it when the supplied text lacks it, and
the loader appends it at boot if the embedded text still lacks it (a whole-word match). Every hardware-loading unit has
`ConditionKernelCommandLine=!m4.safe`.

## Boot counting (the loader cannot write)

* `m4-boot-pending.service` writes `/boot/m4/boot-pending` (entry name inside) and fsyncs the file and `/boot/m4`, before any
  hardware-loading unit.
* `m4-boot-ok.service` removes it once `display-manager.service` has been active for 20 s.
* The next boot with the file present boots the rescue set with `previous-boot-incomplete`.
* `m4-boot select|install` clears it when the owner switches or installs an entry.

## Digest decision (sha256 vs a cheaper digest)

Measured on this Mac's P core (the loader's `level1-sha256.c`, `-Os`, 45 MiB = Image + initramfs): **173–430 ms** (245–285 ms
on a quiet core); the hardware SHA-2 instructions would be 63 ms but need FP/SIMD registers inside a `-mgeneral-regs-only`
blob whose FP trap state at EL2 is unproven (so: off). The loader runs from iBoot's CPU state, not a boosted core, so the
native cost is at or above the 300 ms threshold. The ARMv8 `CRC32` instructions (general registers only) take **4.7 ms** for
45 MiB. Decision: the loader verifies **size + CRC-32**. This detects truncation, partial writes, bit rot and wrong files
(a wrong file passes only with probability 2⁻³²); it is not collision resistant and not an authenticity check (the root
file system is trusted: whoever can write `entry.conf` can write the hash too). `_sha256` stays in `entry.conf` as the
record Linux-side tools verify.
