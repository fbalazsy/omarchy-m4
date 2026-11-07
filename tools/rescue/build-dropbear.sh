#!/bin/sh
# Rebuild the static aarch64 dropbear used by --ssh-hold.
# Password login is compiled out. The private rescue key is not an input.
# Needs gcc-aarch64-linux-gnu. Writes the multi-binary path given as $1.
set -eu
out=${1:?usage: build-dropbear.sh OUT}
command -v aarch64-linux-gnu-gcc >/dev/null || {
  echo "install gcc-aarch64-linux-gnu" >&2
  exit 1
}
ver=2024.86
t=$(mktemp -d)
trap 'rm -rf "$t"' EXIT
curl -fsSL --max-time 60 -o "$t/src.tar.bz2" \
  "https://matt.ucc.asn.au/dropbear/releases/dropbear-${ver}.tar.bz2"
tar -xjf "$t/src.tar.bz2" -C "$t"
cd "$t/dropbear-${ver}"
printf '%s\n' \
  '#define DROPBEAR_SVR_PASSWORD_AUTH 0' \
  '#define DROPBEAR_SVR_PAM_AUTH 0' \
  '#define DROPBEAR_SVR_PUBKEY_AUTH 1' \
  > localoptions.h
./configure --host=aarch64-linux-gnu --disable-zlib --disable-syslog \
  --disable-lastlog --disable-utmp --disable-utmpx --disable-wtmp \
  --disable-wtmpx --disable-pututline --disable-loginfunc
make -j"$(nproc)" PROGRAMS="dropbear dropbearkey" MULTI=1
aarch64-linux-gnu-gcc -static -o "$out" obj/*.o \
  libtomcrypt/libtomcrypt.a libtommath/libtommath.a
