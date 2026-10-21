# Installing Omarchy on an M4 Pro MacBook Pro (J616s)

> **Read this first.** No second Mac has been installed this way yet. This guide describes how the one machine running this project was set up, and what another owner needs. Each step says how sure we are.
> - The boot loader has to be built for **your** Mac: it contains per-machine data (Wi-Fi calibration, your disk's partitions, your root file system), so nobody can give you a ready-made image. It builds on Linux (section 4.3).
> - The kernel's disk-write guard takes its ranges from the loader (loader rev 15 or later, plus a `linux-m4` with that change), after the loader checked them against your GPT. With an older loader the kernel only writes to the owner's disk layout.
>
> If you only want Linux on Apple Silicon and your Mac is supported there, use [Asahi Linux](https://asahilinux.org) instead.

Each step below says how sure we are: **done** (this is how the owner's Mac was set up), **designed** (written, never run on a second Mac), or **unknown** (not recorded; you'll have to work it out).

## 1. Requirements

- **Mac:** MacBook Pro 16" M4 Pro, model Mac16,7 (board J616s). Other T6040/T6041 Macs (the 14" M4 Pro J614s, the M4 Max J614c/J616c) are **not supported**: the loader checks for J616s and the kernel has only the J616s device tree.
- **macOS:** exactly **macOS 26.6.2 (25G83)**. The loader checks the system firmware version and stops on any other. **Do not update macOS** after installing, unless a loader for the new version exists.
- **Disk space:** about 110 GB taken from macOS: a second small macOS install (M4-Dev, 50 GB) that holds the boot policy, and the Linux root (64 GB or more; the owner's 56 GB is 95 % full).
- **Another computer is not needed,** but you need an aarch64 Linux system (or Docker) to build your loader, and a USB stick (FAT32 or exFAT) to carry it to macOS Recovery.
- **A full backup of your Mac.** Partitioning mistakes can destroy macOS.

## 2. What works, what doesn't

From [STATUS.md](STATUS.md) (2026-10-21):

| Works | Doesn't work yet |
|---|---|
| GPU desktop (GL 4.6, Vulkan 1.4), internal display at 120 Hz, H.264/VP9/AV1 video decode | real suspend, camera, external displays, SD card, Touch ID |
| Wi-Fi, Bluetooth, speakers, microphones, headphone jack, USB and Bluetooth audio | USB3 replug (USB2 works on all three ports; SuperSpeed only on the first plug) |
| keyboard, trackpad, keyboard light, ambient light sensor, battery, fans/sensors, RTC | VRR (turning it on blanks the panel until a reboot) |
| cpufreq with Apple's power manager, CPU idle, swap, rescue mode | GPU: ray tracing off, one queue, no compression; no HEVC; no hardware decode in Chromium |
| | no boot console before the display driver starts |

## 3. How it boots

```
power on → iBoot → (boot policy of the M4-Dev macOS) → our loader → /boot/m4/entries/<kernel> on the Linux root
                                                                   → or its built-in rescue system
```

- **The loader** is based on m1n1 (via GravityLinux's bootloader). It is installed once from macOS Recovery with `kmutil`, attached to the boot policy of a second, small macOS install called M4-Dev. This is the same idea as Asahi's "stub macOS".
- It reads `/boot/m4` on the Linux root and boots the default entry. The format is in [BOOT-ABI.md](BOOT-ABI.md). It only reads the disk, never writes.
- **Rescue:** if the entry is broken, or the previous boot did not finish, it boots a rescue kernel and initramfs built into the loader image (`m4.safe=1`: hardware units off, root mounted).
- After the first install, everything else is pacman packages: `linux-m4` installs a kernel and `m4-boot` writes its entry.

## 4. Install

### 4.1 On macOS: make room

1. **Second macOS install, M4-Dev (unknown).** It lives in its own APFS container of exactly 50 GB, has one System volume named `M4-Dev`, and its boot policy must be **Paired** and **Permissive** (`bputil -d` shows `Permissive (smb0 && smb1): 1`). The installer checks all of this. How the owner's M4-Dev was created and set to Permissive was not recorded. Asahi's installer does the same job: it makes a stub macOS and sets its policy with `bputil` from the paired recoveryOS.
2. **The Linux partition (done for the owner's second partition).** From macOS, shrink the main container and add a partition, for example:
   ```
   diskutil apfs resizeContainer disk0s2 <new size of macOS> %0FC63DAF-8483-4772-8E79-3D69D8477DE4% Linux 64G
   ```
   Write down its first and last block (`diskutil info`, or `gpt -r show /dev/disk0` in Recovery) and its partition UUID: they go into your loader.
3. **Collect firmware (designed).** Apple's firmware can't be redistributed, so each owner extracts their own. The `all_firmware.tar.gz` and kernelcache that the Asahi installer saves from macOS are the input of `asahi-fwextract`, which produces exactly the file names our `m4-firmware` package needs. Keep that folder on the USB stick.

### 4.2 The root file system (unknown on macOS, done from Linux)

The owner's root was an Arch Linux ARM (asahi-alarm) image copied onto the partition and then grown. The copy itself was not recorded. The other recipe on record runs on a working Linux system: `pacman --root` into the mounted partition with this repo's packages, as the owner did for a second install (its script is not in this repo yet). That install has only run as a container, never natively.

Whatever you use, the root must be ext4. Note its file system UUID.

### 4.3 Build your loader (done for the owner's Mac, on Linux; designed for yours)

You need: the loader source (the `m4-loader` repository), the toolchain of `m4/docker/Dockerfile` (gcc 16.2.1-2 and binutils 2.46.1-1 from Fedora 44; Docker or a Fedora 44 root under `systemd-nspawn` both work), and from your Mac:
- an ADT dump: `ioreg -a -p IODeviceTree -l -w0 > adt.plist` in macOS, converted with the loader's `m4/tests/tools/make-adt.py`;
- the first 1 MiB of the internal disk (its GPT) for `m4/tools/level1-install.py --disk GPT.img --partuuid <root> [--extra <other Linux partition>]`, which writes the private header with your root partition (and any other Linux partition Linux may write);
- the root file system UUID from 4.2, and M4-Dev's volume group UUID (`diskutil apfs list`).

Then follow the loader's `m4/BUILD.md`:
1. `m4/tools/gates-unit.py make` for your four per-unit records.
2. For any macOS other than 26.6.2 (25G83), or another J616s: `m4/tools/gates-unit.py regen` rewrites the shared records from your ADT. Five `AAPL,unit-string` records need an ADT as the loader sees it, not an ioreg dump.
3. `m4/build.sh`, then `m4/check.sh`: 9 test steps, and all must pass.

The rescue system inside the image needs a kernel `Image`, `board.dtb` and an initramfs (`m4-boot rescue --out`). The owner's rescue kernel is `linux-m4` tag `linux-m4-7.1.13-4`.

**The image contains your Wi-Fi calibration, partition and file system UUIDs. Never publish it.**

### 4.4 Install the loader from Recovery (designed; the owner used the same command from a macOS staging folder)

1. On Linux: `stage-usb.sh` writes the image to the stick and reads it back by checksum.
2. Shut down. Hold the power button until "Loading startup options", choose **Options**, then **Utilities → Terminal**.
3. Check first, then install:
   ```
   sh /Volumes/<stick>/m4-install /Volumes/<stick>/m4-loader.bin --check
   sh /Volumes/<stick>/m4-install /Volumes/<stick>/m4-loader.bin
   ```
   It runs `kmutil configure-boot -v <M4-Dev> -c <image> --raw --entry-point 2048 --lowest-virtual-address 0` after checking the M4-Dev identity, the boot policy and the free space. kmutil asks for your password.
4. Restart. Choose M4-Dev in the startup picker (hold power) or set it as the startup disk: that boots the loader.

### 4.5 First boot and packages (done on the owner's Mac)

1. Without a valid `/boot/m4` entry the loader boots its rescue system. Log in as root.
2. Add the `[m4]` repository above the others in `/etc/pacman.conf`, and trust its key:
   ```
   [m4]
   SigLevel = Required DatabaseRequired
   Server = https://github.com/jgoyvaerts/omarchy-m4/releases/download/repo-aarch64
   ```
   ```
   curl -LO https://github.com/jgoyvaerts/omarchy-m4/releases/download/repo-aarch64/m4-repo.asc
   pacman-key --add m4-repo.asc && pacman-key --lsign-key 583960F916C6E753CFC8A62E54400FA71AF1C721
   ```
3. Install: `pacman -Syu linux-m4 m4-support m4-tools m4-aurora-mesa` plus Asahi's `asahi-audio speakersafetyd alsa-ucm-conf-asahi avd-fw` and Omarchy's `libva-v4l2_request-avd`.
4. Build your own firmware package (it is never in the repository):
   ```
   git clone https://github.com/jgoyvaerts/omarchy-m4 && cd m4-omarchy
   tools/firmware-from-macos.sh /path/to/asahi-firmware-folder
   cd packages/m4-firmware && makepkg -f && sudo pacman -U m4-firmware-*.pkg.tar.*
   ```
5. `linux-m4` writes its entry through `m4-boot` and makes it the default. Reboot.
6. Install Omarchy as usual (the owner used Omarchy's installer, then the `omarchy` repository).

## 5. Updates

- `sudo pacman -Syu`. Every kernel package gets its own release name (`7.1.12-m4.<pkgrel>`) and its own entry in `/boot/m4/entries`.
- `m4-boot` keeps only the default entry and one rollback. **Before installing a new kernel, select the one you want to keep as rollback:** `sudo m4-boot select <entry>`.
- **macOS updates break the loader** (it stops on a firmware version it doesn't know). Only update macOS when a loader for the new version exists.

## 6. Rollback and rescue

| Problem | What to do |
|---|---|
| New kernel misbehaves | `sudo m4-boot select <previous entry>`, reboot. `m4-boot list` shows the entries. |
| A boot didn't finish | The next boot uses the rescue system on its own (`m4-boot status` says why). Fix, then `sudo m4-boot clear`. |
| Linux doesn't start at all | Hold power at start-up and choose macOS. |
| New loader doesn't work | Recovery → Terminal → reinstall the previous loader image with `m4-install` (keep it on the stick). A loader built for other firmware shows a stop page; it does not fall back to rescue. |

The boot counter: `m4-boot-pending.service` marks each boot, `m4-boot-ok.service` clears the mark once the desktop has run for 20 s. A mark left behind sends the next boot to rescue.

## 7. Risks

- **Data loss** while partitioning (4.1). Back up.
- **macOS updates** leave you without Linux until a matching loader exists.
- **Unproven install path:** no second Mac has ever been installed this way. The USB-stick installer has only been tested on a disk image.
- **GPU hangs** can still take down the desktop (STATUS G-02, G-03).
- **Never re-pair** a Bluetooth device on Linux that macOS also uses: it breaks the pairing on macOS.
