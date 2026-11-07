# Rescue V2

`/9` at 21:49 completed (`kmutil` done, loader `91f3490f`). The black screen is after that install. Do not enroll with `--raw`.

PR 66 (`docs/macho-boot-object.md`, aura `13074f00`) appends the initramfs, configuration, and kernel to `aura.bin`, then wraps that raw image with `tools/macho_boot_object.py`. `kmutil configure-boot -c` that Mach-O. `kcbz` must be 0. A Stage 1 that loads Stage 2 from the ESP is not the tested path in that doc.

This build does not run `make`. It starts from the trial stage2 `82781366` (already that layout: `aura_initramfs`, size, cpio, `chosen.bootargs`, gzip Image, `AURALOAD` at `0x808`). The only payload edits are the cpio member `init` and the `init=` bootarg token. `init` is `#!/usr/bin/ash` linked to busybox and does not call `switch_root`. It may mount the command-line root with `-o ro,noload`. `hib=1` and the existing root UUID stay. The ESP is not written.

`python3 tools/rescue/build-rescue-stage2.py STAGE2-82781366.bin rescue-v2.bin`

writes `rescue-v2.bin` and `rescue-v2.macho`. No cross compiler. Pins and binaries are not in git.

## Diagnostic handoff

`build-m4diag.py` uses the same trial stage2 and the module archive `9f456199` (`7.3.0-m4.222-g55obs2`). It does not rebuild the kernel. The packed closure is the modules those drivers depend on: SPI and dockchannel input, DWC3 host, xHCI, usbhid, and `cdc_ether` / `cdc_ncm` / `r8153_ecm`, plus the nvme modules. `depmod` writes the index that goes into the initramfs. `CONFIG_USB_GADGET` is unset in the matched config, so this image does not try a gadget.

PID 1 loads nvme, mounts the root `ro,noload`, and checks `init=` before any root program runs. A script interpreter is syntax-checked with `-n`. An ELF interpreter is `--list`ed. Symlinks are resolved inside that root. A failed check prints `CLASS diagnostic-hold` and does not remount read-write. A passing check remounts read-write and `switch_root`s to that same `init=`. It does not substitute `/sbin/init`. The hold is not a repair.

The stage2 device tree gives `multi-touch` the property `firmware-name` = `apple/tpmtfw-j616s.bin`. The `keyboard` node under the same dockchannel-hid device has no `firmware-name`. `dchid_get_firmware` reads that property and calls `request_firmware`. Pass `--firmware` the 81,424-byte `tpmtfw-j616s.bin` (`7eb84312…`). Hardware input is not proven by the build.

Dock ethernet is brought up only for `cdc_ether`, `cdc_ncm`, or `r8153_ecm`. BusyBox 1.36.1 exports the subnet as a dotted mask and the prefix as `mask`. The DHCP script accepts either a prefix or a contiguous dotted mask, installs one host route, and does not install a default route. A rejected lease does not flush the address. Logs are PUT to `/up/m4diag-start.txt` and `/up/m4diag-checks.txt`. `/up/m4diag-handoff.txt` is sent before that exec, not after it. The host is the `--telemetry-url` (`http://address:port/up`). It is not in git. A network failure does not change the check result. `bash -n` does not prove what a script later starts; those lines are printed and not run. After the check passes, the root is remounted read-write and the shell execs the packed util-linux `switch_root`, which does the chdir, mount move, and chroot before execing `init=`. BusyBox ash does not continue after a failed exec, and `switch_root` itself exits if its exec of init fails. That final exec is irreversible. There is no post-exec recovery. `test-handoff.sh` runs that packed aarch64 binary under qemu in a private mount namespace. The verifier needs `zstd` (`sudo apt-get install -y zstd qemu-user-static`).

## SSH hold

`--ssh-hold` does not remount read-write and does not call `switch_root`. It stays at `CLASS diagnostic-hold`. Dropbear is static, password login is compiled out, and the only authorized key is the one passed to `--authorized-key`. The host key is generated at boot under `/run`. Dropbear listens only on an address written by the DHCP hook. An image that adds Dropbear but not a new transport is not a boot candidate: the 23-module closure already logged `NET_DRIVER_NONE` on hardware.

This kernel has no UDC. `CONFIG_USB_GADGET` is unset and `CONFIG_USB_DWC3_HOST=y`, so `dwc3_gadget_init` is not linked. The packed `dwc3-apple.ko` matches `apple,t8103-dwc3` and logs `No device mode on this port: staying off` for a device-role request. The boot device tree does describe the three ports as `apple,t6040-dwc3` with `dr_mode=otg` and `apple,sn201202x` connectors, and `phy-apple-t6040-usb2.ko` matches `apple,t6040-atcphy`. `CONFIG_PHY_APPLE_ATC` is unset. `CONFIG_TYPEC_TPS6598X` is unset; the custom config that is set is `CONFIG_TYPEC_SN201202X=m`, and that module is not in the 23-module closure. No dock NIC id was saved. Do not force a gadget role onto the host-only PHY.

## Wi-Fi hold

`stamp-wifi-hold.py` keeps that kernel suffix and the 23 modules, then adds `cfg80211`, `brcmutil`, `brcmfmac`, and the cyw/wcc/bca vendor modules from the matched `9f456199` archive. `depmod` is run on that combined tree. `modules.dep` from the full archive is not copied: it names modules that are not packed. Firmware is the uncompressed `apple,hostomel` c0 set (`8b18acb6…` for the 1,804,429-byte bin) plus the six relative c2 symlinks. `CONFIG_FW_LOADER_COMPRESS` is unset. `PCIE_APPLE`, `APPLE_DART`, `RFKILL`, `BRCMFMAC_PCIE`, and `BRCMFMAC_PROTO_MSGBUF` are built in. The driver asks for `brcmfmac4388c0-pcie.<board-type>` and reads `brcm,board-type` from the wifi node. This boot tree's node is `apple,hostomel`. The hold loads the wifi modules only after that node says `apple,hostomel` and the c0 file is present, then unblocks a wlan rfkill and waits for `wlan*`.

NetworkManager keyfiles are read at runtime from the already read-only root. The parser accepts only `type=wifi`, infrastructure, `key-mgmt=wpa-psk`, and `ipv4.method=auto`. Anything else is rejected. At most three autoconnect profiles are tried, highest `autoconnect-priority` first. The generated wpa_supplicant file stays under `/run` mode 0600. Logs, telemetry, and the image do not contain the ssid or the psk. The supplicant is started with `-q` and its log on `/dev/null`. DHCP uses the existing hook, which adds no default route. Dropbear then binds that address. The root stays `ro,noload`. There is no remount and no `switch_root`. This has not been booted on the machine.

`tools/rescue/m4d` is the 1TR bootstrap. It runs `enroll-v2.sh` with `m4diag.pins`. Set `H` yourself. It does not reboot.

`hib=1` on this image is changed to `hib=0`, and `resume=` / `resume_offset=` / `initcall_debug` on the bootargs line are cleared. `chosen.aura,nv2=1` and `init=/usr/local/sbin/rover-init` stay. Enrollment is `enroll-v2.sh` with a pins file that names the new Mach-O. The build does not enroll and does not reboot.
