#!/bin/sh
# Run the real wifi_setup try loop under the image busybox.
# Fixture select, supplicant argv, DHCP hook, then dropbear listen and key auth.
# Does not print the fixture passphrase. Does not bind host /dev.
set -eu
HERE=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
MACHO=${MACHO:-/opt/cursor/artifacts/m4wifi/m4wifi.macho}
T=$(mktemp -d)
cleanup() {
  if [ -s "$T/root/run/dropbear.pid" ]; then
    sudo kill "$(cat "$T/root/run/dropbear.pid")" 2>/dev/null || true
  fi
  if [ -n "${NS:-}" ]; then
    sudo kill "$NS" 2>/dev/null || true
  fi
  sudo "${IP_BIN:-ip}" netns del m4wifi2ns 2>/dev/null || true
  if findmnt -R -n "$T" 2>/dev/null | grep -q .; then
    printf '%s\n' "CLEANUP_REFUSED mounted"
    exit 1
  fi
  if [ "${KEEP:-0}" != 1 ]; then
    sudo rm -rf "$T"
  else
    printf '%s\n' "KEEP $T"
  fi
}
trap cleanup EXIT
umask 077

python3 - "$MACHO" "$T/src" << 'PY'
import pathlib, struct, subprocess, sys, stat
macho, dest = sys.argv[1:]
b = pathlib.Path(macho).read_bytes()
base, size_at, early = 0x10000, 4103735, 234816
total = struct.unpack_from("<I", b, base + size_at)[0]
mid = b[base + size_at + 4 + early:base + size_at + 4 + total]
dec = subprocess.run(["zstd", "-d", "-c"], input=mid, capture_output=True).stdout
want = {
    "usr/bin/busybox",
    "usr/lib/ld-linux-aarch64.so.1",
    "usr/lib/libc.so.6",
    "usr/lib/libcrypt.so.2",
    "usr/lib/libcrypt.so.2.0.0",
    "usr/bin/tr",
}
j = 0
while j + 110 <= len(dec) and dec[j:j + 6] == b"070701":
    ns = int(dec[j + 94:j + 102], 16)
    fs = int(dec[j + 54:j + 62], 16)
    mode = int(dec[j + 14:j + 22], 16)
    name = dec[j + 110:j + 110 + ns].split(b"\0", 1)[0].decode()
    start = j + 110 + ns
    start = (start + 3) & ~3
    blob = dec[start:start + fs]
    if name in want:
        path = pathlib.Path(dest) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if stat.S_ISLNK(mode):
            if path.is_symlink() or path.exists():
                path.unlink()
            path.symlink_to(blob.decode())
        else:
            path.write_bytes(blob)
            path.chmod(0o755)
    j = start + ((fs + 3) & ~3)
    if name == "TRAILER!!!":
        break
PY

ROOT=$T/root
mkdir -p "$ROOT/bin" "$ROOT/usr/bin" "$ROOT/usr/sbin" "$ROOT/lib" "$ROOT/usr/lib" \
  "$ROOT/run/m4wifi" "$ROOT/dev" "$ROOT/proc" "$ROOT/sys/class/net/wlan0" \
  "$ROOT/etc" "$ROOT/root/.ssh" "$ROOT/usr/share/udhcpc" \
  "$ROOT/sysroot/etc/NetworkManager/system-connections" \
  "$ROOT/usr/lib/firmware/brcm" \
  "$ROOT/proc/device-tree/pcie@1cb0000000/pci@0,0/wifi@0,0"
cp -a "$T/src/usr/bin/busybox" "$ROOT/usr/bin/busybox"
cp -a "$T/src/usr/lib/." "$ROOT/usr/lib/"
ln -s ../usr/lib/ld-linux-aarch64.so.1 "$ROOT/lib/ld-linux-aarch64.so.1"
cp -a "$T/src/usr/bin/tr" "$ROOT/usr/bin/tr.real"
cat > "$T/dispatch.c" << 'EOF'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
  const char *base = strrchr(argv[0], '/');
  char **av;
  int i;
  base = base ? base + 1 : argv[0];
  av = calloc(argc + 5, sizeof(char *));
  if (!av) return 1;
  av[0] = "qemu-aarch64-static";
  if (strcmp(base, "tr") == 0) {
    av[1] = "/usr/bin/tr.real";
    for (i = 1; i < argc; i++) av[i + 1] = argv[i];
    av[argc + 1] = 0;
  } else {
    av[1] = "/usr/bin/busybox";
    av[2] = (char *)base;
    for (i = 1; i < argc; i++) av[i + 2] = argv[i];
    av[argc + 2] = 0;
  }
  execv("/qemu-aarch64-static", av);
  return 127;
}
EOF
gcc -static -O2 -o "$ROOT/bin/dispatch" "$T/dispatch.c"
qemu-aarch64-static -L "$ROOT" "$ROOT/usr/bin/busybox" --list > "$T/applets"
while IFS= read -r app; do
  [ -n "$app" ] || continue
  ln -sfn dispatch "$ROOT/bin/$app"
  ln -sfn ../../bin/dispatch "$ROOT/usr/bin/$app"
done < "$T/applets"
ln -sfn dispatch "$ROOT/bin/sh"
ln -sfn ../../bin/dispatch "$ROOT/usr/bin/tr"
test -x "$ROOT/bin/mkdir"
test -x "$ROOT/usr/bin/tr"
cp "$HERE/m4diag-lib.sh" "$ROOT/usr/bin/m4diag-lib.sh"
cp "$HERE/nm-wifi.sh" "$ROOT/usr/bin/nm-wifi.sh"
cp "$HERE/nm-wifi.awk" "$ROOT/usr/bin/nm-wifi.awk"
cp "$HERE/udhcpc-m4.sh" "$ROOT/usr/share/udhcpc/m4.script"
cp "$HERE/out/dropbear-static" "$ROOT/usr/sbin/dropbear.real"
cat > "$T/dropwrap.c" << 'EOF'
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
int main(int argc, char **argv) {
  char **av = calloc(argc + 6, sizeof(char *));
  int i, n = 4, server = 0;
  if (!av) return 1;
  av[0] = "qemu-aarch64-static";
  av[1] = "-0";
  av[2] = "dropbearmulti";
  av[3] = "/usr/sbin/dropbear.real";
  for (i = 1; i < argc; i++) {
    av[n++] = argv[i];
    if (i == 1 && strcmp(argv[i], "dropbear") == 0) {
      av[n++] = "-F";
      server = 1;
    }
  }
  av[n] = 0;
  if (server && fork() > 0) {
    int spins;
    for (spins = 0; spins < 50; spins++) {
      if (access("/run/dropbear.pid", R_OK) == 0)
        break;
      usleep(100000);
    }
    return 0;
  }
  if (server)
    setsid();
  execv("/qemu-aarch64-static", av);
  return 127;
}
EOF
gcc -static -O2 -o "$ROOT/usr/sbin/dropbearmulti" "$T/dropwrap.c"
cp /usr/bin/qemu-aarch64-static "$ROOT/qemu-aarch64-static"
chmod 755 "$ROOT/usr/bin/m4diag-lib.sh" "$ROOT/usr/bin/nm-wifi.sh" \
  "$ROOT/usr/share/udhcpc/m4.script" "$ROOT/usr/sbin/dropbearmulti" "$ROOT/qemu-aarch64-static"
printf 'x' > "$ROOT/usr/lib/firmware/brcm/brcmfmac4388c0-pcie.apple,hostomel.bin"
printf 'apple,hostomel\0' > "$ROOT/proc/device-tree/pcie@1cb0000000/pci@0,0/wifi@0,0/brcm,board-type"
printf '%s\n' '# none' > "$ROOT/etc/m4diag-wifi-modules"
printf '%s\n' '192.0.2.25' > "$ROOT/etc/m4diag-host"
printf '%s\n' 'root:x:0:0:root:/root:/bin/sh' > "$ROOT/etc/passwd"
printf '%s\n' 'root:x:0:' > "$ROOT/etc/group"
sudo chown root:root "$ROOT/root" "$ROOT/root/.ssh"
sudo mknod "$ROOT/dev/null" c 1 3
sudo mknod "$ROOT/dev/zero" c 1 5
sudo mknod "$ROOT/dev/random" c 1 8
sudo mknod "$ROOT/dev/urandom" c 1 9

ssid=fixture-alpha
psk=fixture-psk-alpha-1
cat > "$ROOT/sysroot/etc/NetworkManager/system-connections/01-good" << EOF
[connection]
type=wifi
autoconnect-priority=10

[wifi]
ssid=$ssid

[wifi-security]
key-mgmt=wpa-psk
psk=$psk

[ipv4]
method=auto
EOF
printf '%s\n' '[connection]' 'type=wifi' 'interface-name=eth0' '[wifi]' 'ssid=fixture-other' \
  '[wifi-security]' 'key-mgmt=wpa-psk' 'psk=fixture-psk-other-1' '[ipv4]' 'method=auto' \
  > "$ROOT/sysroot/etc/NetworkManager/system-connections/02-other"
ssh-keygen -q -t ed25519 -N '' -f "$T/ok"
ssh-keygen -q -t ed25519 -N '' -f "$T/bad"
cat "$T/ok.pub" > "$ROOT/etc/m4diag-authorized_keys"

cat > "$T/wpa.c" << 'EOF'
#include <stdio.h>
int main(int argc, char **argv) {
  FILE *f = fopen("/run/m4wifi/wpa.argv", "w");
  int i;
  if (!f) return 1;
  for (i = 0; i < argc; i++) fprintf(f, "%s\n", argv[i]);
  fclose(f);
  return 0;
}
EOF
cat > "$T/cli.c" << 'EOF'
#include <stdio.h>
int main(void) {
  printf("wpa_state=COMPLETED\n");
  return 0;
}
EOF
cat > "$T/ip.c" << 'EOF'
#include <stdio.h>
int main(int argc, char **argv) {
  FILE *f = fopen("/run/ip.log", "a");
  int i;
  if (!f) return 1;
  for (i = 0; i < argc; i++) fprintf(f, "%s%s", i ? " " : "", argv[i]);
  fputc('\n', f);
  fclose(f);
  return 0;
}
EOF
cat > "$T/udhcpc.c" << 'EOF'
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
int main(int argc, char **argv) {
  FILE *f = fopen("/run/udhcpc.argv", "w");
  int i;
  char *av[] = {"qemu-aarch64-static", "/usr/bin/busybox", "ash",
                "/usr/share/udhcpc/m4.script", "bound", 0};
  if (!f) return 1;
  for (i = 0; i < argc; i++) fprintf(f, "%s\n", argv[i]);
  fclose(f);
  setenv("interface", "wlan0", 1);
  setenv("ip", "192.0.2.10", 1);
  setenv("subnet", "255.255.255.0", 1);
  setenv("mask", "24", 1);
  setenv("router", "192.0.2.1", 1);
  execv("/qemu-aarch64-static", av);
  return 1;
}
EOF
gcc -static -O2 -o "$ROOT/usr/sbin/wpa_supplicant" "$T/wpa.c"
gcc -static -O2 -o "$ROOT/usr/sbin/wpa_cli" "$T/cli.c"
gcc -static -O2 -o "$ROOT/usr/bin/ip" "$T/ip.c"
gcc -static -O2 -o "$ROOT/bin/udhcpc" "$T/udhcpc.c"
ln -sfn ../../bin/udhcpc "$ROOT/usr/bin/udhcpc"

cat > "$ROOT/inner.sh" << 'EOF'
#!/bin/sh
export PATH=/usr/bin:/bin:/sbin
export M4_RUN=/run
export M4_SSH_ADDR_FILE=/run/m4ssh.addr
export M4_TELEMETRY_HOST=192.0.2.25
export LOG=/run/m4diag.txt
: > "$LOG"
. /usr/bin/m4diag-lib.sh
wifi_setup
start_ssh
echo READY >> "$LOG"
sleep 30
EOF
chmod 755 "$ROOT/inner.sh"

IP_BIN=$(command -v ip || true)
[ -n "$IP_BIN" ] || IP_BIN=/usr/sbin/ip
sudo "$IP_BIN" netns del m4wifi2ns 2>/dev/null || true
sudo "$IP_BIN" netns add m4wifi2ns
sudo "$IP_BIN" netns exec m4wifi2ns "$IP_BIN" link set lo up
sudo "$IP_BIN" netns exec m4wifi2ns "$IP_BIN" addr add 192.0.2.10/32 dev lo
sudo "$IP_BIN" netns exec m4wifi2ns chroot "$ROOT" /qemu-aarch64-static /usr/bin/busybox ash /inner.sh > "$T/inner.out" 2>&1 &
NS=$!
i=0
while [ "$i" -lt 20 ]; do
  if grep -q READY "$ROOT/run/m4diag.txt" 2>/dev/null; then
    break
  fi
  sleep 1
  i=$((i + 1))
done
cp "$ROOT/run/m4diag.txt" /tmp/wifi-try-debug.txt 2>/dev/null || true
cp "$T/inner.out" /tmp/wifi-try-inner.txt 2>/dev/null || true
cp "$ROOT/run/dropbear.out" /tmp/wifi-try-dropbear.txt 2>/dev/null || true
if ! grep -q READY "$ROOT/run/m4diag.txt" 2>/dev/null; then
  echo INNER_NOT_READY
  exit 1
fi

# Secrets must not be in the boot log or the supplicant argv.
if grep -q -F "$psk" "$ROOT/run/m4diag.txt" || grep -q -F "$psk" "$ROOT/run/m4wifi/wpa.argv"; then
  echo LEAK
  exit 1
fi
grep -q '/run/m4wifi/1.conf' "$ROOT/run/m4wifi/wpa.argv"
grep -q '^-c$' "$ROOT/run/m4wifi/wpa.argv" || grep -q '^-c /run/m4wifi/1.conf$' "$ROOT/run/m4wifi/wpa.argv" || {
  # argv is one token per line
  grep -q -- '-c' "$ROOT/run/m4wifi/wpa.argv"
}
grep -q 'WIFI_ASSOCIATED' "$ROOT/run/m4diag.txt"
grep -q 'addr add 192.0.2.10/24 dev wlan0' "$ROOT/run/ip.log"
grep -q '192.0.2.10' "$ROOT/run/m4ssh.addr"
grep -q 'SSH_LISTEN 192.0.2.10:22' "$ROOT/run/m4diag.txt"
grep -q 'WIFI_REJECT iface' "$ROOT/run/m4diag.txt"
if grep -q 'WIFI_SLOT_BAD' "$ROOT/run/m4diag.txt"; then
  echo SLOT_BAD
  exit 1
fi

sudo "$IP_BIN" netns exec m4wifi2ns ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -o BatchMode=yes -o IdentitiesOnly=yes -o PreferredAuthentications=publickey \
  -o ConnectTimeout=5 -i "$T/ok" root@192.0.2.10 true
echo OK_AUTH
set +e
sudo "$IP_BIN" netns exec m4wifi2ns ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -o BatchMode=yes -o IdentitiesOnly=yes -o PreferredAuthentications=publickey \
  -o ConnectTimeout=5 -i "$T/bad" root@192.0.2.10 true > "$T/bad.out" 2>&1
badrc=$?
set -e
if [ "$badrc" -eq 0 ]; then
  echo BAD_KEY_ACCEPTED
  exit 1
fi
if ! grep -q 'Permission denied' "$T/bad.out"; then
  echo BAD_KEY_UNEXPECTED
  exit 1
fi
echo BAD_KEY_DENIED
if grep -q -F "$psk" "$T/bad.out" || grep -q -F "$ssid" "$T/inner.out"; then
  echo LEAK_SSH
  exit 1
fi
echo WIFI_TRY_OK
