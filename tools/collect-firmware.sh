#!/bin/sh
# Copy the m4-firmware files from the running system (installed by m4-firmware) into userspace/firmware/ so the package can be rebuilt.
# They are Apple/Broadcom firmware and per-device calibration: never committed (gitignored). Run from the repo root.
set -eu
cd userspace/firmware
while read -r sum path; do
    [ -n "$path" ] || continue
    mkdir -p "$(dirname "$path")"
    cp "/$path" "$path"
done < FIRMWARE.sha256
sha256sum --quiet -c FIRMWARE.sha256 && echo "firmware collected and verified"
