#!/bin/sh
# 1TR diagnostic client — fixed read-only inventory allowlist.
# BASE is injected by the relay when serving /bootstrap. No extra bearer.
# Does not read stdin (safe under curl|sh). Does not persist across a restart.
# The network cannot add a command: unknown ops are rejected, and the
# poll interval and result id must be plain tokens.
set -eu
BASE="__BASE__"
UA="1tr-diag-client/1"
POLL_SEC=5
MAX_ITERS=540
MAX_BYTES=262144

if [ "$BASE" = "__BASE__" ] || [ -z "$BASE" ]; then
  echo "bootstrap is not bound to a session; refuse to run" >&2
  exit 1
fi

# Trim one trailing slash
BASE="${BASE%/}"

safe_id() {
  _id="$1"
  if [ -z "$_id" ] || [ "${#_id}" -gt 40 ]; then
    printf '%s' none
    return
  fi
  case "$_id" in
    *[!A-Za-z0-9_-]*) printf '%s' none ;;
    *) printf '%s' "$_id" ;;
  esac
}

post_result() {
  _op="$1"
  _id="$2"
  _file="$3"
  curl -fsS --max-time 60 -A "$UA" \
    -X POST \
    -H "Content-Type: text/plain; charset=utf-8" \
    -H "X-1TR-Op: ${_op}" \
    -H "X-1TR-Id: ${_id}" \
    --data-binary @"${_file}" \
    "${BASE}/result" >/dev/null
}

fetch_task() {
  curl -fsS --max-time 30 -A "$UA" "${BASE}/task"
}

run_op() {
  _op="$1"
  case "$_op" in
    inventory)
      printf '=== 1tr-inventory-only START %s ===\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
      if command -v sw_vers >/dev/null 2>&1; then sw_vers; fi
      printf '\n=== diskutil list ===\n'
      diskutil list
      printf '\n=== diskutil list internal ===\n'
      diskutil list internal
      printf '\n=== diskutil apfs listVolumeGroups ===\n'
      diskutil apfs listVolumeGroups 2>&1 || printf '(unavailable)\n'
      printf '\n=== mount (current mounts only; no new mounts) ===\n'
      mount
      printf '\n=== 1tr-inventory-only END ===\n'
      ;;
    sw_vers)
      if command -v sw_vers >/dev/null 2>&1; then sw_vers; else echo "(sw_vers unavailable)"; fi
      ;;
    diskutil_list)
      diskutil list
      ;;
    diskutil_list_internal)
      diskutil list internal
      ;;
    apfs_list_volume_groups)
      diskutil apfs listVolumeGroups 2>&1 || printf '(unavailable)\n'
      ;;
    mount_list)
      mount
      ;;
    uname)
      uname -a
      ;;
    stop)
      echo "client-stop"
      ;;
    *)
      printf 'REJECTED unknown op: %s\n' "$_op"
      return 1
      ;;
  esac
}

WORKDIR="${TMPDIR:-/tmp}/1tr-diag-$$"
mkdir -m 700 "$WORKDIR" || exit 1
OUT="$WORKDIR/out.txt"
trap 'rm -rf "$WORKDIR"' EXIT INT TERM

echo "1TR diag client starting; results go to the relay (not kept after a restart)."

# Immediate first inventory — no extra user gesture.
if run_op inventory >"$OUT" 2>&1; then
  post_result inventory bootstrap "$OUT" || echo "WARN: inventory post failed" >&2
  echo "inventory uploaded"
else
  post_result inventory bootstrap-error "$OUT" || true
  echo "inventory command failed; uploaded stderr/stdout" >&2
fi

i=0
while [ "$i" -lt "$MAX_ITERS" ]; do
  i=$((i + 1))
  resp="$(fetch_task 2>/dev/null || echo 'OP none')"
  op="$(printf '%s\n' "$resp" | awk '/^OP /{print $2; exit}')"
  id="$(safe_id "$(printf '%s\n' "$resp" | awk '/^ID /{print $2; exit}')")"
  poll="$(printf '%s\n' "$resp" | awk '/^POLL /{print $2; exit}')"
  [ -n "$op" ] || op="none"
  [ -n "$id" ] || id="none"
  [ -n "$poll" ] || poll="$POLL_SEC"
  case "$op" in
    none|"")
      ;;
    stop)
      echo "server requested stop"
      echo client-stop >"$OUT"
      post_result stop "$id" "$OUT" || true
      exit 0
      ;;
    inventory|sw_vers|diskutil_list|diskutil_list_internal|apfs_list_volume_groups|mount_list|uname)
      if run_op "$op" >"$OUT" 2>&1; then
        sz=$(wc -c <"$OUT" | tr -d ' ')
        if [ "$sz" -gt "$MAX_BYTES" ]; then
          echo "truncated: result exceeded ${MAX_BYTES} bytes" >"$OUT"
        fi
        post_result "$op" "$id" "$OUT" || echo "WARN: post $op failed" >&2
        echo "uploaded $op ($id)"
      else
        post_result "$op" "$id" "$OUT" || true
        echo "op $op failed; uploaded output" >&2
      fi
      ;;
    *)
      echo "REJECTED unknown op" >"$OUT"
      post_result rejected "$id" "$OUT" || true
      ;;
  esac
  case "$poll" in
    [1-9]|[1-5][0-9]|60) ;;
    *) poll=$POLL_SEC ;;
  esac
  sleep "$poll"
done
echo "poll deadline reached; exiting"
exit 0
