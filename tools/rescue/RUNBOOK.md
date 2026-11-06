# Rescue V2

`/9` at 21:49 completed (`kmutil` done, loader `91f3490f`). The black screen is after that install. Do not enroll with `--raw`.

PR 66 (`docs/macho-boot-object.md`, aura `13074f00`) appends the initramfs, configuration, and kernel to `aura.bin`, then wraps that raw image with `tools/macho_boot_object.py`. `kmutil configure-boot -c` that Mach-O. `kcbz` must be 0. A Stage 1 that loads Stage 2 from the ESP is not the tested path in that doc.

This build does not run `make`. It starts from the trial stage2 `82781366` (already that layout: `aura_initramfs`, size, cpio, `chosen.bootargs`, gzip Image, `AURALOAD` at `0x808`). The only payload edits are the cpio member `init` and the `init=` bootarg token. `init` is `#!/usr/bin/ash` linked to busybox and does not call `switch_root`. It may mount the command-line root with `-o ro,noload`. `hib=1` and the existing root UUID stay. The ESP is not written.

`python3 tools/rescue/build-rescue-stage2.py STAGE2-82781366.bin rescue-v2.bin`

writes `rescue-v2.bin` and `rescue-v2.macho`. No cross compiler. Pins and binaries are not in git.
