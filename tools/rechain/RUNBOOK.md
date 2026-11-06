# rechain

Installs the chainloading stage1. Does not write the ESP and does not reboot.

The installed `rover.macho` (`b7a557ff…`, 29,556,736 bytes) embeds `Linux version 7.3.0-m4.220` #220 (3 Nov 2026 23:35 UTC) and its initramfs. It has no `AURORA-S1-CFG`. Its strings say chainloading files are not supported and internal storage is not supported. It does not contain `m4.163` or `m4.222`.

`bchain.macho` (`1bcb9250…`, 4,259,840 bytes) is the stage1 whose config names this ESP and `aurora/stage2.bin`, and it chainloads from the ESP. The 19:47 report shows that file is `d4877ab1…`, 18,240,123 bytes. A trial `aurora/stage2-m4222.bin` (`82781366…`) is also present. This script does not select or modify the trial. Before `kmutil` it prints any `aura-try` marker (30 bytes or less) and waits for `y`.

`kmutil configure-boot` is answered on the terminal (y, user, password). A `curl | sh` pipe is not used as its stdin. Pins and the short bootstrap are not in git. Not a hardware boot test.
