#!/bin/sh
# Fault-inject the handoff decision. Mount, copy and upload are mocks.
# Nothing here mounts a host filesystem or deletes outside this temp dir.
set -eu
HERE=$(CDPATH= cd "$(dirname "$0")" && pwd)
T=$(mktemp -d)
cleanup() {
  if findmnt -R -n "$T" 2>/dev/null | grep -q .; then
    echo "refusing to remove mounted $T" >&2
    return
  fi
  rm -rf "$T"
}
trap cleanup EXIT
mkdir -p "$T/bin" "$T/run" "$T/etc" "$T/sysroot"
printf '%s\n' 'http://192.0.2.25:9/up' > "$T/etc/url"

cat > "$T/bin/mount" << 'EOF'
#!/bin/sh
printf '%s\n' "mount $*" >> "$MOCK_LOG"
case " $* " in
  *" -o rw "*)
    if [ -n "${FAIL_RW:-}" ]; then
      exit 1
    fi
    printf '%s\n' "$M4_ROOT_DEV /sysroot ext4 rw,relatime 0 0" > "$M4_MOUNTS"
    exit 0
    ;;
  *" -o ro,noload "*|*" remount,ro,noload "*)
    if [ -n "${FAIL_RO:-}" ]; then
      exit 1
    fi
    printf '%s\n' "$M4_ROOT_DEV /sysroot ext4 ro,noload 0 0" > "$M4_MOUNTS"
    exit 0
    ;;
esac
exit 0
EOF
cat > "$T/bin/umount" << 'EOF'
#!/bin/sh
printf '%s\n' "umount $*" >> "$MOCK_LOG"
: > "$M4_MOUNTS"
exit 0
EOF
cat > "$T/bin/cp" << 'EOF'
#!/bin/sh
printf '%s\n' "cp $*" >> "$MOCK_LOG"
if [ -n "${FAIL_CP:-}" ]; then
  exit 1
fi
PATH=${PATH#*:} command cp "$@"
EOF
cat > "$T/bin/nc" << 'EOF'
#!/bin/sh
printf '%s\n' "nc $*" >> "$MOCK_LOG"
exit 1
EOF
cat > "$T/bin/switch_root" << 'EOF'
#!/bin/sh
/bin/cp "$M4_MOUNTS" "$MOCK_DIR/mounts-at-exec"
printf '%s\n' "SWITCHED $*" > "$MOCK_DIR/switched"
exit 0
EOF
cat > "$T/bin/ash" << 'EOF'
#!/bin/sh
printf '%s\n' ASH_HOLD >> "$LOG"
exit 0
EOF
chmod 755 "$T/bin/mount" "$T/bin/umount" "$T/bin/cp" "$T/bin/nc" "$T/bin/switch_root" "$T/bin/ash"

run_case() {
  name=$1
  shift
  case_dir=$T/case-$name
  mkdir -p "$case_dir"
  printf '%s\n' '/dev/fixture /sysroot ext4 ro,noload 0 0' > "$case_dir/mounts"
  : > "$case_dir/mock.log"
  log=$case_dir/m4diag.txt
  : > "$log"
  set +e
  env \
    PATH="$T/bin:$PATH" \
    LOG="$log" \
    M4_RUN="$T/run" \
    M4_MOUNTS="$case_dir/mounts" \
    M4_ROOT_DEV=/dev/fixture \
    M4_SWITCH_ROOT="$T/bin/switch_root" \
    M4_ASH="$T/bin/ash" \
    M4_URL_FILE="$T/etc/url" \
    MOCK_LOG="$case_dir/mock.log" \
    MOCK_DIR="$case_dir" \
    FAIL_RW="${FAIL_RW:-}" \
    FAIL_RO="${FAIL_RO:-}" \
    FAIL_CP="${FAIL_CP:-}" \
    "$@" \
    sh -c '
      set -eu
      . "$1"
      boot_decision
    ' sh "$HERE/m4diag-lib.sh" > "$case_dir/stdout" 2>&1
  rc=$?
  set -e
  printf '%s\n' "rc=$rc" >> "$case_dir/stdout"
}

# 1. Read-write remount fails, read-only mount comes back.
FAIL_RW=1 run_case rw-fail verified=1 mode=boot initarg=/usr/local/sbin/rover-init
log=$T/case-rw-fail/m4diag.txt
grep -q 'MOUNT_RW_FAIL' "$log"
grep -q 'MOUNT_RO_AGAIN' "$log"
grep -q 'ROOT_MOUNT /dev/fixture /sysroot ext4 ro,noload 0 0' "$log"
grep -q 'ROOT_JOURNAL noload' "$log"
grep -q 'ROOT_OBSERVED ro,noload' "$log"
grep -q 'FALLBACK_WIFI pre-exec-only' "$log"
grep -q 'HANDOFF_PRE_EXEC_FAIL' "$log"
grep -q 'ASH_HOLD' "$log"
if grep -q 'ROOT_STAYS' "$log"; then
  echo "rw-fail claimed ROOT_STAYS" >&2
  exit 1
fi
if [ -f "$T/case-rw-fail/switched" ]; then
  echo "rw-fail still exec'd switch_root" >&2
  exit 1
fi

# 2. Read-write fails and the read-only mount does not come back.
FAIL_RW=1 FAIL_RO=1 run_case ro-fail verified=1 mode=boot initarg=/usr/local/sbin/rover-init
log=$T/case-ro-fail/m4diag.txt
grep -q 'MOUNT_RW_FAIL' "$log"
grep -q 'MOUNT_RO_AGAIN_FAIL' "$log"
grep -q 'ROOT_MOUNT absent' "$log"
grep -q 'ROOT_JOURNAL unread' "$log"
grep -q 'FALLBACK_WIFI pre-exec-only' "$log"
if grep -q 'ROOT_STAYS' "$log" || grep -q 'ROOT_OBSERVED ro,noload' "$log"; then
  echo "ro-fail reported a read-only root that is not mounted" >&2
  exit 1
fi

# 3. Copy and upload fail. The root is still read-only until switch_root runs.
FAIL_CP=1 run_case upload-fail verified=1 mode=boot initarg=/usr/local/sbin/rover-init
log=$T/case-upload-fail/m4diag.txt
grep -q 'LOG_COPY_FAIL' "$log"
grep -q 'NET_EXPORT m4diag-checks.txt fail' "$log"
grep -q 'NET_EXPORT m4diag-handoff.txt fail' "$log"
grep -q 'SWITCHED /sysroot /usr/local/sbin/rover-init' "$T/case-upload-fail/switched"
grep -q 'rw,relatime' "$T/case-upload-fail/mounts-at-exec"
# The failure lines are in the log written before the read-write mount.
awk '
  /LOG_COPY_FAIL/ { copy = NR }
  /NET_EXPORT m4diag-checks.txt fail/ { up = NR }
  /MOUNT rw/ { rw = NR }
  END { if (!(copy && up && rw && copy < rw && up < rw)) exit 1 }
' "$log"

# 4. The init did not verify. No remount, Wi-Fi hold, mount line unchanged.
run_case pre-exec verified=0 mode=boot initarg=/usr/local/sbin/rover-init
log=$T/case-pre-exec/m4diag.txt
grep -q 'HANDOFF_PRE_EXEC_FAIL' "$log"
grep -q 'FALLBACK_WIFI pre-exec-only' "$log"
grep -q 'ROOT_MOUNT /dev/fixture /sysroot ext4 ro,noload 0 0' "$log"
grep -q 'ASH_HOLD' "$log"
if grep -q 'mount -t ext4 -o rw' "$T/case-pre-exec/mock.log"; then
  echo "pre-exec remounted read-write" >&2
  exit 1
fi
if [ -f "$T/case-pre-exec/switched" ]; then
  echo "pre-exec exec'd switch_root" >&2
  exit 1
fi

for name in rw-fail ro-fail upload-fail pre-exec; do
  echo "===== $name ====="
  cat "$T/case-$name/m4diag.txt"
done
echo "boot fallback ok"
