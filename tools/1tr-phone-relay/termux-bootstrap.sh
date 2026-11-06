#!/bin/sh
# Termux one-command bootstrap for the read-only 1TR phone relay.
# Does not read stdin, so it can be piped to sh.
# Downloads one pinned commit and checks SHA256 before starting the relay.
set -eu
umask 077

PAYLOAD_COMMIT="1cca129333b9e5df9ee8c9ec7d561fa71e00c085"
HASH_PY="5817b276a6a5a255b3df355f5b9e266a12f32697f12a8418aceb078fdedaddd6"
HASH_CLIENT="b8418744ca3bd319c920b877d8b659f7f20b887785a7f43241cbe4f35c1d821d"
HASH_RUN="824717c7637dccabf53104677afa5ba2519fb219aa20b85e0c49a0e98dead2a2"

REPO="https://raw.githubusercontent.com/fbalazsy/omarchy-m4/${PAYLOAD_COMMIT}/tools/1tr-phone-relay"

fail() {
  echo "1tr-phone-relay: $*" >&2
  exit 1
}

check_hash() {
  _value=$1
  case "$_value" in
    *[!0-9a-f]*|"") fail "refusing a payload pin that is not hex" ;;
  esac
  if [ "${#_value}" -ne 64 ]; then
    fail "refusing a payload pin that is not 64 hex characters"
  fi
}

case "$PAYLOAD_COMMIT" in
  *[!0-9a-f]*|"") fail "refusing a commit pin that is not hex" ;;
esac
if [ "${#PAYLOAD_COMMIT}" -ne 40 ]; then
  fail "refusing a commit pin that is not 40 hex characters"
fi
check_hash "$HASH_PY"
check_hash "$HASH_CLIENT"
check_hash "$HASH_RUN"

if ! command -v pkg >/dev/null 2>&1; then
  fail "Termux pkg is missing. Install Termux from F-Droid or the Termux GitHub releases."
fi
if ! command -v curl >/dev/null 2>&1; then
  fail "curl is missing"
fi
if ! command -v sha256sum >/dev/null 2>&1; then
  fail "sha256sum is missing"
fi

export DEBIAN_FRONTEND=noninteractive
# Skip a second mirror probe when Termux already has a repository configured.
export TERMUX_PKG_NO_MIRROR_SELECT=1

pkg install -y python </dev/null || fail "could not install python"
pkg install -y iproute2 </dev/null || echo "1tr-phone-relay: iproute2 was not installed; the hotspot address may stay hidden" >&2

if ! command -v python3 >/dev/null 2>&1; then
  fail "python3 is not on PATH after pkg install"
fi

DEST="${HOME}/.local/share/1tr-phone-relay"
mkdir -p "$DEST"
chmod 700 "$DEST"

fetch() {
  _name=$1
  _hash=$2
  _tmp="$DEST/.${_name}.download"
  curl --proto '=https' --tlsv1.2 --max-redirs 0 -fsSL --max-time 60 \
    "$REPO/$_name" -o "$_tmp" </dev/null || fail "download failed: $_name"
  if ! echo "$_hash  $_tmp" | sha256sum -c - >/dev/null; then
    rm -f "$_tmp"
    fail "sha256 mismatch: $_name"
  fi
  mv "$_tmp" "$DEST/$_name"
  chmod 644 "$DEST/$_name"
}

fetch phone_relay.py "$HASH_PY"
fetch client_template.sh "$HASH_CLIENT"
fetch run-1tr-phone-relay.sh "$HASH_RUN"
chmod 755 "$DEST/run-1tr-phone-relay.sh"

echo "payload pin ${PAYLOAD_COMMIT} verified"
exec "$DEST/run-1tr-phone-relay.sh" </dev/null
