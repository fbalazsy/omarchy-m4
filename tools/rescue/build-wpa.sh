#!/bin/sh
# Static aarch64 wpa_supplicant for the rescue initramfs. WPA-PSK only.
set -eu
out=${1:-$(CDPATH= cd -- "$(dirname "$0")" && pwd)/out/wpa_supplicant}
src=${WPA_SRC:-/tmp/srcbuild}
cc=${CC:-aarch64-linux-gnu-gcc}
mkdir -p "$(dirname "$out")" "$src"
if [ ! -f "$src/libnl-3.10.0.tar.gz" ]; then
  curl -fsSL -o "$src/libnl-3.10.0.tar.gz" \
    https://github.com/thom311/libnl/releases/download/libnl3_10_0/libnl-3.10.0.tar.gz
fi
if [ ! -f "$src/wpa_supplicant-2.11.tar.gz" ]; then
  curl -fsSL -o "$src/wpa_supplicant-2.11.tar.gz" \
    https://w1.fi/releases/wpa_supplicant-2.11.tar.gz
fi
rm -rf "$src/libnl-3.10.0" "$src/wpa_supplicant-2.11" "$src/nlroot"
tar -C "$src" -xzf "$src/libnl-3.10.0.tar.gz"
tar -C "$src" -xzf "$src/wpa_supplicant-2.11.tar.gz"
(
  cd "$src/libnl-3.10.0"
  ./configure --host=aarch64-linux-gnu --prefix="$src/nlroot" \
    --enable-static --disable-shared --disable-cli
  make -j"$(nproc)"
  make install
)
cat > "$src/wpa_supplicant-2.11/wpa_supplicant/.config" << EOF
CONFIG_DRIVER_NL80211=y
CONFIG_LIBNL32=y
CFLAGS += -I$src/nlroot/include/libnl3
LIBS += -L$src/nlroot/lib -lnl-genl-3 -lnl-3
CONFIG_CTRL_IFACE=y
CONFIG_BACKEND=file
CONFIG_IEEE8021X_EAPOL=y
CONFIG_NO_RADIUS=y
CONFIG_NO_CONFIG_BLOBS=y
CONFIG_NO_CONFIG_WRITE=y
CONFIG_NO_RANDOM_POOL=y
CONFIG_TLS=internal
CONFIG_INTERNAL_LIBTOMMATH=y
CONFIG_CRYPTO=internal
EOF
make -C "$src/wpa_supplicant-2.11/wpa_supplicant" -j"$(nproc)" \
  CC="$cc" LDFLAGS="-static" BINDIR=/usr/sbin
cp "$src/wpa_supplicant-2.11/wpa_supplicant/wpa_supplicant" "$out"
cp "$src/wpa_supplicant-2.11/wpa_supplicant/wpa_cli" "$(dirname "$out")/wpa_cli"
file "$out"
