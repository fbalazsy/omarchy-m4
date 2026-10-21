# m4-omarchy

Arch Linux ARM + [Omarchy](https://omarchy.org) on an Apple **MacBook Pro M4 Pro (T6040, board J616s)**, which Asahi Linux doesn't support yet. This repo holds everything that turns a stock Omarchy install into a working system on that Mac, as ordinary pacman packages.

Current state, gaps and to-do: [docs/STATUS.md](docs/STATUS.md).

## How it boots

A small m1n1-derived loader, installed once from macOS Recovery, boots `/boot/m4/entries/<kernel>` following [docs/BOOT-ABI.md](docs/BOOT-ABI.md). It also carries an embedded rescue system. After that, everything is packages:

| Package | What it is | Built from |
|---|---|---|
| `linux-m4` | the kernel: every M4 driver in-tree, including the GPU driver and the board device tree | [jgoyvaerts/linux `m4/aurora`](https://github.com/jgoyvaerts/linux/tree/m4/aurora) |
| `linux-m4-rescue` | the module tree of the loader's embedded rescue kernel | `packages/linux-m4-rescue` |
| `m4-support` | `m4-boot` (writes `/boot/m4`), mkinitcpio hooks, systemd units, modprobe.d, the greeter and session setup | `userspace/support` |
| `m4-tools` | `m4-test`: attended hardware tests | `userspace/tools` |
| `m4-firmware` | Wi-Fi/Bluetooth firmware and the light-sensor calibration (files not in git) | `userspace/firmware` |
| `m4-aurora-mesa` | aurora's Mesa (GL, GLES, Vulkan) for the GPU driver, in `/opt/aurora-mesa` | [jgoyvaerts/mesa `m4-t6040-v2`](https://github.com/jgoyvaerts/mesa/tree/m4-t6040-v2) |
| `quickshell`, `m4-omarchy-qt612-compat` | Omarchy shell build and a Qt 6.12 fix | `packages/` |

## Sources

The public copy of this repository is [jgoyvaerts/omarchy-m4](https://github.com/jgoyvaerts/omarchy-m4), a series of snapshots
(`tools/publish-snapshot.sh`). The sources the packages build from are public: the kernel
[jgoyvaerts/linux](https://github.com/jgoyvaerts/linux/tree/m4/aurora) (branch `m4/aurora`, on aurora-silicon's aurora-wip), Mesa
[jgoyvaerts/mesa](https://github.com/jgoyvaerts/mesa/tree/m4-t6040-v2) and the boot loader
[jgoyvaerts/m4-loader](https://github.com/jgoyvaerts/m4-loader). Signed packages: see [docs/INSTALL.md](docs/INSTALL.md).

## Building and updating (on the M4 itself)

```sh
git clone https://github.com/jgoyvaerts/omarchy-m4 && cd m4-omarchy
git config core.hooksPath hooks          # the privacy check runs before every commit
sh tools/make-local-conf.sh              # this machine's values -> local.conf (never committed)
sh tools/collect-firmware.sh             # only to rebuild m4-firmware: copies the installed firmware files
cd packages/m4-support && makepkg -f && sudo pacman -U m4-support-*.pkg.tar.zst
```

- **Release names:** bump `pkgrel` for every change. Every kernel build gets its own release name (`7.1.13-m4.<pkgrel>`).
- **Rollback:** `sudo m4-boot select <entry>`. If a boot fails, the next one uses the rescue set; `m4-boot status` says why.
- **Tests:** `userspace/tests` (unittest). Fill a copy first: `tools/template.py --conf <your-test.conf> --copy . /tmp/t`.

## Privacy

This repo is meant to be publishable, so machine values live only in `local.conf`. The sources hold `@M4_*@` placeholders, which `tools/template.py` fills at package build time. Firmware files, keys, journals and raw logs are never committed. `tools/privacy-check.py` (the pre-commit hook) refuses MAC addresses, UUIDs, home paths, LAN addresses, emails and the values in your `local.conf`.

## Not here yet

- The loader sources. The loader's device-tree inputs come from each Mac's own ADT, which must not be published.
- A working fresh install: [docs/INSTALL.md](docs/INSTALL.md) has the steps and what still blocks them (firmware from macOS: `tools/firmware-from-macos.sh`; the package repository: `tools/repo-build.sh`).
