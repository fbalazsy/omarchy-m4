# Rescue V2

`/9` at 21:49 completed (`kmutil` done, loader `91f3490f`). The black screen is after that install. Do not enroll with `--raw`.

PR 66 (`docs/macho-boot-object.md`, aura `13074f00`) appends the initramfs, configuration, and kernel to `aura.bin`, then wraps that raw image with `tools/macho_boot_object.py`. `kmutil configure-boot -c` that Mach-O. `kcbz` must be 0. A Stage 1 that loads Stage 2 from the ESP is not the tested path in that doc.

This build does not run `make`. It starts from the trial stage2 `82781366` (already that layout: `aura_initramfs`, size, cpio, `chosen.bootargs`, gzip Image, `AURALOAD` at `0x808`). The only payload edits are the cpio member `init` and the `init=` bootarg token. `init` is `#!/usr/bin/ash` linked to busybox and does not call `switch_root`. It may mount the command-line root with `-o ro,noload`. `hib=1` and the existing root UUID stay. The ESP is not written.

`python3 tools/rescue/build-rescue-stage2.py STAGE2-82781366.bin rescue-v2.bin`

writes `rescue-v2.bin` and `rescue-v2.macho`. No cross compiler. Pins and binaries are not in git.

## Diagnostic handoff

`build-m4diag.py` uses the same trial stage2 and the module archive `9f456199` (`7.3.0-m4.222-g55obs2`). It does not rebuild the kernel. The packed closure is the modules those drivers depend on: SPI and dockchannel input, DWC3 host, xHCI, usbhid, and `cdc_ether` / `cdc_ncm` / `r8153_ecm`, plus the nvme modules. `depmod` writes the index that goes into the initramfs. `CONFIG_USB_GADGET` is unset in the matched config, so this image does not try a gadget.

PID 1 loads that list with a per-module timeout, brings up an interface only when its driver is one of those three ethernet drivers, and sends the log with HTTP PUT to the `--telemetry-url` when that URL is set. The URL is not in git. The root is mounted `ro,noload` and is not fsck'd. `init=` from the command line is checked before anything from the root runs: a shell script must have an executable interpreter (`bash`/`sh`/`ash` is syntax-checked), and an ELF must have its `PT_INTERP` present and `--list` must not report a missing library. Only then does it mount the root read-write and `switch_root`. A failed check prints `CLASS diagnostic-hold` and does not start the root init. That hold is not a repair. `dockchannel-hid` asks for `apple/tpmtfw-*.bin`, and that file is not in the module archive.

`hib=1` on this image is changed to `hib=0`, and `resume=` / `resume_offset=` / `initcall_debug` on the bootargs line are cleared. `chosen.aura,nv2=1` and `init=/usr/local/sbin/rover-init` stay. Enrollment is `enroll-v2.sh` with a pins file that names the new Mach-O. The build does not enroll and does not reboot.
