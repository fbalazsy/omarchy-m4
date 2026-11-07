#!/bin/sh
# Select NetworkManager wifi keyfiles. Prints counts and reasons only.
nm_wifi_select() {
  src=$1
  out=$2
  awk_bin=${NM_WIFI_AWK:-/usr/bin/nm-wifi.awk}
  mkdir -p "$out" || return 0
  chmod 700 "$out" || return 0
  if [ ! -d "$src" ]; then
    printf '%s\n' "WIFI_NO_PROFILE"
    return 0
  fi
  slot=0
  for f in "$src"/*; do
    [ -e "$f" ] || continue
    if [ -L "$f" ]; then
      printf '%s\n' "WIFI_REJECT symlink"
      continue
    fi
    if [ ! -f "$f" ]; then
      printf '%s\n' "WIFI_REJECT not-file"
      continue
    fi
    bytes=$(wc -c < "$f" 2>/dev/null || echo 0)
    if [ "$bytes" -gt 65536 ]; then
      printf '%s\n' "WIFI_REJECT too-large"
      continue
    fi
    slot=$((slot + 1))
    awk -f "$awk_bin" -v outdir="$out" -v slot="$slot" "$f" || printf '%s\n' "WIFI_REJECT parser"
    if [ -f "$out/$slot.conf" ]; then
      chmod 600 "$out/$slot.conf" || true
    fi
  done
  list=$(mktemp "$out/rank.XXXXXX")
  : > "$list"
  i=1
  while [ "$i" -le "$slot" ]; do
    if [ -f "$out/$i.meta" ] && [ -f "$out/$i.conf" ]; then
      pri=$(awk '$1=="priority"{print $2; exit}' "$out/$i.meta")
      printf '%s %s\n' "$pri" "$i" >> "$list"
    fi
    i=$((i + 1))
  done
  awk '
    NF == 2 && $1 ~ /^-?[0-9]+$/ && $2 ~ /^[0-9]+$/ {
      n++
      pri[n] = $1 + 0
      slot[n] = $2 + 0
    }
    END {
      for (a = 1; a <= n; a++) {
        for (b = a + 1; b <= n; b++) {
          if (pri[b] > pri[a] || (pri[b] == pri[a] && slot[b] < slot[a])) {
            t = pri[a]; pri[a] = pri[b]; pri[b] = t
            t = slot[a]; slot[a] = slot[b]; slot[b] = t
          }
        }
      }
      limit = n
      if (limit > 3)
        limit = 3
      print "WIFI_USABLE " n
      for (a = 1; a <= limit; a++)
        print "WIFI_TRY " a " " slot[a]
    }
  ' "$list"
  rm -f "$list"
  return 0
}
