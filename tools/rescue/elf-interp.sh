#!/bin/sh
# Print the interpreter of one file.
# KIND absent | other | script | elf
# INTERP <path> | unreadable | none
# POSIX awk and xxd. No network. Does not execute the file.
set -eu
if [ "${1:-}" = "" ] || [ ! -e "$1" ]; then
  printf '%s\n' "KIND absent" "INTERP none"
  exit 0
fi
f=$1
le() {
  # file-order hex to an integer. Values above 2^53 are not used.
  printf '%s\n' "$1" | awk '
    function hv(c) { return index("0123456789abcdef", c) - 1 }
    {
      h = tolower($0)
      n = length(h)
      acc = 0
      mul = 1
      for (i = 1; i <= n; i += 2) {
        b = hv(substr(h, i, 1)) * 16 + hv(substr(h, i + 1, 1))
        acc += b * mul
        mul *= 256
      }
      printf "%.0f\n", acc
    }
  '
}
xx() {
  xxd -s "$2" -l "$3" -p "$1" 2>/dev/null | tr -d " \n" || true
}
magic4=$(xx "$f" 0 4)
if [ "$(xx "$f" 0 2)" = "2321" ]; then
  line=$(head -n 1 "$f" | tr -d '\r')
  rest=${line#\#\!}
  rest=${rest#"${rest%%[![:space:]]*}"}
  interp=${rest%%[[:space:]]*}
  printf '%s\n' "KIND script" "INTERP ${interp:-none}"
  exit 0
fi
if [ "$magic4" != "7f454c46" ]; then
  printf '%s\n' "KIND other" "INTERP none"
  exit 0
fi
class=$(xx "$f" 4 1)
data=$(xx "$f" 5 1)
if [ "$class" != "02" ] || [ "$data" != "01" ]; then
  printf '%s\n' "KIND elf" "INTERP unreadable"
  exit 0
fi
phoff=$(le "$(xx "$f" 32 8)")
phsz=$(le "$(xx "$f" 54 2)")
phnum=$(le "$(xx "$f" 56 2)")
if [ "$phoff" -eq 0 ] || [ "$phsz" -lt 56 ] || [ "$phnum" -eq 0 ]; then
  printf '%s\n' "KIND elf" "INTERP unreadable"
  exit 0
fi
i=0
while [ "$i" -lt "$phnum" ]; do
  at=$((phoff + i * phsz))
  ptype=$(le "$(xx "$f" "$at" 4)")
  if [ "$ptype" -eq 3 ]; then
    poff=$(le "$(xx "$f" $((at + 8)) 8)")
    psz=$(le "$(xx "$f" $((at + 32)) 8)")
    if [ "$psz" -le 0 ] || [ "$psz" -gt 4096 ]; then
      printf '%s\n' "KIND elf" "INTERP unreadable"
      exit 0
    fi
    hex=$(xx "$f" "$poff" "$psz")
    interp=$(printf '%s\n' "$hex" | awk '
      function hv(c) { return index("0123456789abcdef", c) - 1 }
      {
        h = tolower($0)
        out = ""
        for (i = 1; i < length(h); i += 2) {
          b = hv(substr(h, i, 1)) * 16 + hv(substr(h, i + 1, 1))
          if (b == 0) break
          out = out sprintf("%c", b)
        }
        printf "%s\n", out
      }
    ')
    printf '%s\n' "KIND elf" "INTERP ${interp:-none}"
    exit 0
  fi
  i=$((i + 1))
done
printf '%s\n' "KIND elf" "INTERP none"
exit 0
