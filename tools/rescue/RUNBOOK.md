# Rescue stage2

`/9` at 21:49 completed (`kmutil` done, loader `91f3490f`). The black screen is after that install. This path does not use `--raw`.

The package keeps the Aura stage1 (`bchain.macho`, `1bcb9250`) and replaces only `aurora/stage2.bin`. The new stage2 is the trial `82781366` kernel `7.3.0-m4.222-g55obs2` and its nvme modules. `/init` is `#!/usr/bin/ash` linked to busybox. It does not call `switch_root`. It may mount the cmdline root with `-o ro,noload` and print whether `rover-init` and `/sbin/init` exist. No fsck, no root write, no reboot.

Build, on a machine that already has the trial file:

`python3 tools/rescue/build-rescue-stage2.py STAGE2-82781366.bin rescue-stage2.bin`

No cross compiler. Pins and the stage2 binary are not in git.
