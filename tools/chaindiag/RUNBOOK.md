# chaindiag

Read-only ESP diagnostic. Not a recovery. Do not replace `rover` or `bchain`.

## Blocker

No stage1/stage2 pair is safe to install.

The attached `bchain.macho` (sha256 `1bcb9250aef56caa8040fc2fc42877131bc3944ee5292e88afc0901ed6172a0c`) is an Aura `MH_FILESET`. Its `AURORA-S1-CFG01` record is version 1, a zero word, the ESP partition UUID, then `;aurora/stage2.bin`. There is no stage2 digest in the file and no `kcbz` string. The prelink plist says `KMBuildVersion=Aura` and the fileset id is `com.apple.kernel`.

That same stage1 hash is what both `bchain` scripts pin, against two different stage2 hashes: `44ea752c331fb32a9c63d826d7b7ff324d42768d137df2e056ffdcbf43e40160` (2026-11-04) and `850c303096b741049c860b2192943f38f47bb13bdb414800544a1b3786c373ea` (the script that stopped). Those pins are expected file contents, not a measured ABI. The 19:15 run found `d4877ab1c94d4324d11179dbd5f143cd8454269e61aaa0807dba5a4b8d49b61d` and changed nothing.

`d4877ab1…` is the prior `default_stage2.bin` / m4.220 fallback. The attached notes' last healthy uname is `7.3.0-m4.222-g55obs2` from trial `8278136603f9fbbf8ee64eb83a5f2231e43af084a94612f45326978b676577ef`, with aura-try spent. The same stage2 hash does not prove the black boot is healthy.

`/rover` reported boot object `b7a557ffab7ee74aa6923860400b79dace99b0d43e52d75aabfd53672c56d84e`. That file is not this macho. The black screen is that installed object plus whatever stage2 is on the ESP. Installing `1bcb9250` onto `d487` would be a new untested pair. Repin is not justified.

The partition UUID and the hypnos URL are not in this repository. They live only in the uncommitted pins file the parent copies beside the script.

## What the script collects

One GPT slice on `disk0` must match the pins (UUID, start block, byte size). It is mounted read-only, or read in place if already mounted read-only. A read-write mount is left alone.

Then the report has: `aurora/stage2.bin` size, sha256, catalog hit (`d487`, `850c`, `44ea`, `82781366`, `9ac3f687`, `54447d15`, or `UNLISTED`), first 64 header bytes, gzip/ELF/Mach-O/Linux-ARM64 tags, and up to 120 strings matching `m1n1`, `Linux version`, `m4.N`, `boot-pending`, `aura-try`, `chainload`. It hashes regular files under `aurora/` and `trial/` (not symlinks) and any small root `*try*`, `*spent*`, `*marker*`, `*.arm`, `*.conf`. Ext4 is not mounted. Other disks are not listed. NVRAM keeps only `failboot-breadcrumbs`, `boot-image-last`, `panic-count`, `panicmedic`, `auto-boot`, `boot-args` (values truncated), then deletes the full dump. `bless --getBoot` and `bless --info` are read-only. `bputil` is only `bputil -d -v` when exactly one System volume group is visible. No credential file is fetched.

Exit 0 means the stage2 hash was read. It does not mean the Mac will boot. Exit 1 means identity, mount, hash, or unmount did not finish; the report still has the `STOP` line.

## Test

`dash tools/chaindiag/test-chaindiag.sh` covers `sh -n`, a wrong disk identity, two matches, a missing stage2, a failing hash tool, a failing mount, a failed upload, a failed unmount, a read-write mount that is not read, and an existing read-only mount.

## Deploy

Copy the script and the pins file to the hypnos document root under new names. The one-line command is in the artifact runbook, not here.
