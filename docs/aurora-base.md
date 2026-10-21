# The aurora-based kernel: userspace notes

`linux-m4 7.1.12-1` (release `7.1.12-m4.1`) is built on aurora-silicon's `aurora-wip` instead of the Gravity tree that ended with
`7.1.13-m4.45`. `m4-support 1.0-21` and `m4-tools 1.0-15` are the userspace for it, and they keep working on `7.1.13-m4.45`.

## m4-support (next release): swap on zram

- **zram swap (STATUS G-29).** There was none: zram-generator found no configuration (Omarchy's `90-omarchy.conf` only sits in
  `/usr/share/omarchy/default`). `/usr/lib/systemd/zram-generator.conf.d/95-m4.conf` makes `zram0` a swap of `min(ram / 4, 8192)`
  MiB (8 GiB here), lz4, priority 100. lz4 because the kernel builds only that backend; the name sorts after Omarchy's drop-in, so
  its zstd cannot win once it lands in `/usr/lib`. `/usr/lib/sysctl.d/99-m4-zram.conf` sets the usual zram reclaim values
  (swappiness 180, page-cluster 0, watermark boost 0, scale 125).
- **GPU idle hold (STATUS G-13).** The firmware powers the GPU off 2 ms after its last job and each wake-up costs about 1.1 ms.
  `/usr/lib/udev/rules.d/90-m4-gpu-idle-power.rules` runs `/usr/lib/m4/gpu-idle-power` at every charger change and GPU bind; it
  writes the kernel's `idle_off_delay_ms` (linux-m4 7.1.12-12): 20 ms on AC, the firmware's 2 ms on battery. Optional
  `/etc/m4/gpu-idle.conf` (`AC_MS=`, `BATTERY_MS=`, 1-1000). A kernel without the knob is left alone. **Migration:** the
  hand-installed copies `/etc/udev/rules.d/90-m4-gpu-idle-power.rules` (same name, so it shadows the packaged rule) and
  `/usr/local/lib/m4/gpu-idle-power` must be deleted after the install; `/etc/m4/gpu-idle.conf` stays and is still read.

## m4-support 1.0-26: AOP follow-ups

- **Microphone names.** `90-m4-aop-mic.conf` matched the ALSA driver name `aop_audio`, but the card's is `snd_soc_aop`, so it never
  applied (the source showed as "Built-in Audio Multichannel"). The source now matches `snd_soc_aop` and the card its sysfs path
  under the AOP (WirePlumber matches card rules before the card sets `alsa.driver_name`); both show as "Built-in Microphones".
- **Ambient light on by default.** `m4-als-brightness` (screen brightness, keyboard light on in the dark) is enabled for every user
  through `graphical-session.target.wants`. Off: `systemctl --user mask --now m4-als-brightness`. A manual brightness change still
  pauses it for 10 minutes. `als-auto.py` (keyboard light only, started from `hyprland.lua`) is gone: with the sensor present at
  boot, the two would fight over the keyboard light.

## m4-support 1.0-24 / m4-tools 1.0-16: the kernel does it now

From `7.1.12-m4.10` the kernel handles what the package used to work around. 1.0-24 only has to work on the aurora kernels
(`7.1.12-m4.7` and newer) and on the rescue kernel `7.1.13-m4`.

- **Idle.** The kernel idles with the register-saving WFI from the first idle (`7.1.12-m4.7` already does). Gone: `m4-idle.service`,
  `/usr/lib/m4/idle-enable`, `idle=nop` in `/usr/share/m4/cmdline`, and linux-m4's `cmdline-drop` file. **The owner's
  `/etc/m4/cmdline` still has `idle=nop`** and wins over the packaged file: drop the word there before `m4-boot install` writes the
  m4.10 entry, or that kernel boots spinning. The rescue set's command line is embedded in the loader and is not affected.
- **GPU.** The GPU node is enabled: `asahi` autoloads at coldplug and binds without parameters. `gpu-load` sees the GPU platform device
  (`of_node/compatible` = `apple,agx-t6040`) and only runs `modprobe -b asahi` (no unload, no `t6040_attach=1`, which fails with
  `-EBUSY` there). Without that device (the rollbacks m4.7/m4.9, node disabled) it unloads and loads `asahi t6040_attach=1` as before.
  The rest of `m4-gpu.service` stays (Mesa files check, sddm link, `ok aurora`). The `m4_gpu_driver` check is gone (no aurora kernel
  has that module). **GPU kill switch:** `blacklist asahi` in a file under `/etc/modprobe.d`, then reboot (`gpu-load` loads with `-b`, so
  the blacklist holds for its load too; the greeter then stays in software).
- **Display.** `appledrm` takes over by default. **SMC.** `macsmc_input` and `macsmc_hwmon` autoload like any SMC child: gone are
  `m4-smc-input.service` and `m4-sensors.service` (`m4_smc_hwmon` does not exist on the aurora kernels).
- **Bluetooth.** `hci_bcm4377` defers its BCM4388 probe until the Wi-Fi function is bound to brcmfmac, so it autoloads. Gone:
  `m4-bluetooth-load.service`, `/usr/lib/m4/bluetooth-load`, the blacklist and `options hci_bcm4377 allow_bcm4388=1`.
  `m4-bt-keyshare` (before bluetoothd) and `m4-bt-connect` (after bluetoothd) stay.
- **modprobe.d.** `asahi`, `appledrm`, `macsmc_input`, `macsmc_hwmon` and `hci_bcm4377` get the same `install` rule as `apple_avd` and
  `pmp`: they autoload on normal boots and never on a rescue boot (`m4.safe`). The blacklists of modules no aurora kernel has
  (`m4_idle`, `m4_smc_hwmon`, `m4_cpufreq_read`, the codec probes, `m4_bt_otp`, `m4_smc_telemetry`, `m4_usb_contract`) and the options
  for `m4_idle`, `m4_smc_hwmon` and `hci_bcm4377` are gone. `blacklist m4_gpu_driver` stays: the rescue kernel ships it with the T6040
  GPU alias. `options appledrm takeover=1`, `nvme_core default_ps_max_latency_us=0` and `nvme_apple flush_interval=0` are redundant on
  m4.10 (takeover default, the J616s no-APST quirk, no deferred flush) but stay for the rollbacks m4.7/m4.9 (takeover defaults to 0;
  m4.7's NVMe driver defers flushes) and the rescue kernel; drop them with those entries. The AOP stack gets the rule too (below). Every other blacklist (speakers,
  cpufreq, watchdog) is unchanged.
- **AOP** (light sensor, microphones). The kernel enables the AOP, its mailbox, its DART and both AOP ADMACs at boot; apple-dart
  programs the AOP DART's address filter and aop maps the firmware's `__OS_LOG` (the two fixes `m4_aop_attach` used to apply).
  `aop`, `aop_als`, `aop_las`, `snd_soc_aop` and `apple_admac` autoload with the `m4.safe` install rule. Gone: `m4-aop.service`,
  `/usr/lib/m4/aop/attach.py`, the `aop → als → mic` ladder and its markers in `/var/lib/m4` (leftovers are harmless), and the
  `m4_aop_attach` blacklist. The microphones' WirePlumber names ship as
  `/usr/share/wireplumber/wireplumber.conf.d/90-m4-aop-mic.conf` (the copy `m4-test mic` put in `/etc` is the same rule). The rollbacks
  m4.7/m4.9 keep their AOP nodes disabled, so they run without the AOP. **AOP kill switch:** `blacklist aop` in a file under
  `/etc/modprobe.d`, then reboot. `snd_soc_aop speakers=1` stays opt-in.
- **Rescue boots** stay hardware-free: every autoloading hardware module has the `m4.safe` install rule, every hardware unit keeps
  `ConditionKernelCommandLine=!m4.safe`.
- **m4-tools.** `m4-test boot` no longer lists the removed units; `m4-sensors` names `macsmc_hwmon`. Gone: `m4-test aop|als|mic`
  (the AOP starts at boot), `m4-test jack` and `m4-test cpufreq-read` (their probe modules `m4_codec_jack_probe` and `m4_cpufreq_read`
  are not in any aurora kernel; both used `attach.py` as a library), and the GPU rows' refusal while an AOP module is loaded.

## What changed (1.0-21)

- **GPU.** `m4-gpu.service` (`/usr/lib/m4/gpu-load`) loads aurora's `asahi` driver with `t6040_attach=1`, waits for its render node,
  links `/opt/aurora-mesa/sddm-gpu-aurora.conf` as `/etc/sddm.conf.d/zz-m4-gpu.conf` and writes `ok aurora` to `/run/m4/gpu-ready`
  (m4-boot's boot-ok check keys on that word). It refuses while `m4_gpu_driver` is loaded, and refuses before loading anything when
  m4-aurora-mesa's files are missing. As before, a failure only leaves the software greeter on simpledrm.
- **Mesa.** The session and the greeter use aurora's Mesa from the `m4-aurora-mesa` package (`/opt/aurora-mesa`; its PKGBUILD is not in this
  repo yet: `$HOME/M4-Outbox/claude-aurora-port/aurora-mesa-pkg`). `/usr/lib/m4/session-env` and `greeter-env` are symlinks to that
  package's files, so the environment has one source. m4-support depends on `m4-aurora-mesa` instead of `m4-mesa`.
- **Retired:** `m4-mesa` (see [its README](../packages/m4-mesa/README.md)), `/usr/lib/modprobe.d/m4-gpu.conf` (the G16 driver options),
  `/usr/share/m4/sddm-gpu.conf`, and the G16 keys in `env-keys`. `blacklist m4_gpu_driver` stays: the Gravity kernels still ship that
  driver, and it must never load next to asahi.
- **Idle.** The aurora kernel boots with `idle=nop`, as the Gravity kernels do, and saves registers around its own WFI.
  `m4-idle.service` switches it to `wfi` through `/sys/module/kernel/parameters/idle` once the system is up (`/etc/m4/idle-off`
  still keeps it spinning). On the Gravity kernels it loads `m4_idle` as before.
- **Bluetooth.** Same load order (`m4-bluetooth-load.service` loads `hci_bcm4377` after brcmfmac binds). The aurora driver has no
  `allow_bcm4388` parameter and no Wi-Fi-bound check; it ignores the option line with a warning.
- **Lab modules** (`m4_idle`, `m4_smc_hwmon`, `m4_smc_telemetry`, `m4_bt_otp`, `m4_usb_contract`, the codec probes) are not in the aurora
  kernel. Their blacklists and options lines stay for the Gravity kernels; no unit fails when they are missing.

## Installing

1. The aurora-mode loader bind-mounts `/opt/aurora-mesa/{session,greeter}-env` over `/usr/lib/m4/` on every boot, and pacman cannot
   replace a mounted file. Unmount them first (the running session already has its environment):
   `sudo umount /usr/lib/m4/session-env /usr/lib/m4/greeter-env`
2. `sudo pacman -U` m4-support 1.0-21, m4-tools 1.0-15 and linux-m4 7.1.12-1 (+ headers). pacman calls the kernel a downgrade
   because 7.1.12 sorts before 7.1.13; m4-boot keeps `7.1.13-m4.45` as the rollback entry.
3. Check: `ls -l /usr/lib/m4/session-env` shows a link to `/opt/aurora-mesa/session-env`, and `pacman -Qkk m4-support` is clean.
4. Remove the aurora-mode drop-in (below), then `sudo pacman -R m4-mesa`.
5. Stay at the machine for the first boot of the new kernel.

## The leftover aurora-mode drop-in

`/etc/systemd/system/m4-gpu.service.d/zz-aurora.conf` still replaces `m4-gpu.service`'s command with
`/usr/local/lib/m4/gpu-load-aurora`. It keeps working: it does what the packaged `gpu-load` does, and its bind mounts now land on the
links' targets (each file bound onto itself). While it stays, an `m4-aurora-mesa` upgrade cannot replace those two files either. It is
yours to remove:

```sh
sudo rm -f /etc/systemd/system/m4-gpu.service.d/zz-aurora.conf /etc/systemd/system/m4-gpu.service.d/zz-aurora-once.conf
sudo rm -f /usr/local/lib/m4/gpu-load-aurora
sudo systemctl daemon-reload
```

The `aurora-mode` script is retired with it: `aurora-mode off` no longer brings back `m4_gpu_driver`, it only removes the drop-in.

## Rolling back

From m4-support 1.0-24 the rollbacks are the aurora kernels `7.1.12-m4.7`/`m4.9` (the GPU via `t6040_attach=1`, see above); the
Gravity kernels (`7.1.13-m4.N`) need 1.0-23 or older (no `m4-idle.service`, no `idle=nop`).

- `sudo m4-boot select <entry>` makes that entry the default; `sudo m4-boot rollback` selects the previous entry. Then reboot.
- `m4-boot list` shows the entries; `m4-boot status` says which one boots next and why a boot was a rescue boot.
- A failed boot sends the next one to the rescue set by itself.

## Known losses on the aurora kernel

- **Full SMC sensor readout.** `m4_smc_hwmon` is gone. The TCMz CPU-die zone comes from the standard `macsmc_hwmon` (it autoloads
  since 1.0-24), and `m4-sensors` prints whatever hwmon chips exist.
- **The G16 test rows** (`gpu-copy`, `gpu-smoke`, `gpu-features`, `gpu-shape`, `msaa`) and **`m4-test idle`** tested the retired
  `m4_gpu_driver` and `m4_idle`; m4-tools 1.0-19 removes them. Check the aurora GPU with glmark2 or vkcube under the GPU test guard
  (`run-guarded.sh`).
- Rows and stages that load a module by file (`m4-test jack`, the AOP stages) refuse cleanly when that module is not in the running
  kernel's tree.
