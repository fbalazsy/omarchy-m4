# Working on this repo (for coding agents on the M4 and on macOS)

Read [docs/STATUS.md](docs/STATUS.md) first.

## Workflow

- Change the sources, then bump `pkgrel`, run `makepkg -f` in `packages/<name>`, and `sudo pacman -U` the result. Test on the hardware (`m4-test`, a reboot), then commit and push. The owner pulls on the other side.
- Report native results in `docs/STATUS.md` (current state only), or in `reports/YYYY-MM-DD-<topic>.md` as summaries. Never commit raw journals, logs with addresses, or keys.
- Never commit `local.conf`, firmware files, `private/`, or anything the privacy check refuses. Don't add exceptions to `tools/privacy-allow.txt` for real values.

## Rules learned the hard way

- Only `m4-boot` writes `/boot/m4`; never edit it by hand. The rollback is `m4-boot select <entry>`, and rescue mode is automatic after a failed boot.
- Every `linux-m4` build gets its own release name (`7.1.13-m4.<pkgrel>`); never reuse one. The rescue kernel's module tree is the package `linux-m4-rescue`.
- Hardware units use `ConditionFirmware=device-tree-compatible(apple,j616s)` (with the hyphen), `ConditionKernelCommandLine=!m4.safe` and `ConditionVirtualization=!container`, and must end quickly when the hardware is absent. A container (HP, [CONTAINER.md](docs/CONTAINER.md)) sees this machine's device tree but must never touch its hardware or its own `boot-pending`; `m4.safe` means a rescue boot, never "container".
- Hardware modules that must not autoload are blacklisted in `/usr/lib/modprobe.d/m4.conf` and loaded by name from units.
- Unproven behaviour ships default off. A module is real only after the real kernel loaded it; an opt-in value only after the driver's validator accepted it.
- **Our config can lose to the distribution's:**
  - sddm reads `/etc/sddm.conf.d/*` in name order and the last file wins (ours is `zz-m4.conf`);
  - `kernel-modules-hook` deletes unowned module trees at boot;
  - check the owner's real files before relying on an order.
- A boot only counts as good when the user can see it. With the GPU, that needs the driver's greeter adoption line and no `submit rejected`.
- The display card number is not fixed: simpledrm, the GPU and the panel can be `card0`–`card2` in any order. Pick it at runtime (`/usr/lib/m4/kms-card.sh`).
- Owner preferences:
  - minimal changes per update;
  - no fallback or compatibility layers;
  - short, staged commands;
  - ask before doing anything outward-facing or irreversible.
