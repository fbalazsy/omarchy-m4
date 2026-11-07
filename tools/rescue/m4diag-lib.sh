#!/bin/sh
# PID 1 helpers for the g55obs2 diagnostic handoff.
# A verified configured init is handed to switch_root. Anything else stays
# here and prints why. This is not a root-filesystem repair.
set -eu

say() {
  printf '%s\n' "$*"
  if [ -n "${LOG:-}" ]; then
    printf '%s\n' "$*" >> "$LOG"
  fi
}

run_dir() {
  printf '%s\n' "${M4_RUN:-/run}"
}

uptime_s() {
  cut -d. -f1 /proc/uptime 2>/dev/null || echo 0
}

child_ok() {
  case ${1:-} in
    ''|0|1|-1) return 1 ;;
  esac
  return 0
}

load_one() {
  name=$1
  modprobe "$name" >"$(run_dir)/mp-$name.err" 2>&1 &
  p=$!
  if ! child_ok "$p"; then
    say "MODPROBE $name no-child"
    return 0
  fi
  i=0
  while [ "$i" -lt 5 ]; do
    if ! kill -0 "$p" 2>/dev/null; then
      break
    fi
    sleep 1
    i=$((i + 1))
  done
  if kill -0 "$p" 2>/dev/null; then
    if child_ok "$p"; then
      kill "$p" 2>/dev/null || true
    fi
    set +e
    wait "$p"
    set -e
    say "MODPROBE $name timeout"
    return 0
  fi
  set +e
  wait "$p"
  rc=$?
  set -e
  say "MODPROBE $name $rc"
  return 0
}

load_list() {
  phase=$1
  budget=$2
  start=$(uptime_s)
  [ -r /etc/m4diag-modules ] || { say "MODULES_LIST absent"; return 0; }
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    case $name in
      \#*) continue ;;
    esac
    is_nvme=0
    case $name in
      nvme*) is_nvme=1 ;;
    esac
    if [ "$phase" = "early" ] && [ "$is_nvme" -eq 1 ]; then
      continue
    fi
    if [ "$phase" = "nvme" ] && [ "$is_nvme" -eq 0 ]; then
      continue
    fi
    now=$(uptime_s)
    if [ $((now - start)) -ge "$budget" ]; then
      say "MODULES_BUDGET $phase"
      return 0
    fi
    load_one "$name"
  done < /etc/m4diag-modules
}

parse_url() {
  M4_HOST=
  M4_PORT=
  M4_PATH=
  url=
  [ -r /etc/m4diag-url ] && url=$(cat /etc/m4diag-url)
  case $url in
    http://*) ;;
    *) return 0 ;;
  esac
  rest=${url#http://}
  hostport=${rest%%/*}
  M4_PATH=/${rest#*/}
  M4_HOST=${hostport%%:*}
  M4_PORT=${hostport#*:}
  [ "$M4_PORT" = "$hostport" ] && M4_PORT=80
  case $M4_HOST in
    *[!0-9.]*) M4_HOST= ;;
  esac
}

net_setup() {
  parse_url
  printf '%s\n' "${M4_HOST:-}" > /etc/m4diag-host
  export M4_TELEMETRY_HOST=${M4_HOST:-}
  export M4_SSH_ADDR_FILE=$(run_dir)/m4ssh.addr
  sleep 1
  found=0
  i=0
  seen_list=" "
  while [ "$i" -lt 8 ]; do
    for n in /sys/class/net/*; do
      [ -e "$n" ] || continue
      base=${n##*/}
      [ "$base" = "lo" ] && continue
      drv=$(readlink "$n/device/driver" 2>/dev/null || true)
      drv=${drv##*/}
      case $seen_list in
        *" $base "*) ;;
        *)
          seen_list="$seen_list$base "
          say "NET_SEEN $base ${drv:-none}"
          ;;
      esac
      case ${drv:-} in
        cdc_ether|cdc_ncm|r8153_ecm) found=$((found + 1)) ;;
      esac
    done
    [ "$found" -gt 0 ] && break
    sleep 1
    i=$((i + 1))
  done
  used=0
  for n in /sys/class/net/*; do
    [ -e "$n" ] || continue
    base=${n##*/}
    [ "$base" = "lo" ] && continue
    [ "$used" -ge 2 ] && break
    drv=$(readlink "$n/device/driver" 2>/dev/null || true)
    drv=${drv##*/}
    case ${drv:-} in
      cdc_ether|cdc_ncm|r8153_ecm) ;;
      *) continue ;;
    esac
    ip link set "$base" up || say "NET_UP_FAIL $base"
    j=0
    carrier=0
    while [ "$j" -lt 3 ]; do
      carrier=$(cat "$n/carrier" 2>/dev/null || echo 0)
      [ "$carrier" = "1" ] && break
      sleep 1
      j=$((j + 1))
    done
    say "NET $base driver=$drv carrier=${carrier:-0}"
    used=$((used + 1))
    if [ "$carrier" = "1" ] && [ -n "${M4_HOST:-}" ]; then
      set +e
      udhcpc -n -q -t 2 -T 2 -i "$base" -s /usr/share/udhcpc/m4.script
      rc=$?
      set -e
      say "DHCP $base $rc"
    else
      say "DHCP $base skipped"
    fi
  done
  [ "$used" -eq 0 ] && say "NET_DRIVER_NONE"
  return 0
}

norm_abs() {
  printf '%s\n' "$1" | awk -F/ '{
    n = 0
    for (i = 1; i <= NF; i++) {
      if ($i == "" || $i == ".") continue
      if ($i == "..") { if (n > 0) n--; continue }
      s[++n] = $i
    }
    out = ""
    for (i = 1; i <= n; i++) out = out "/" s[i]
    if (out == "") out = "/"
    printf "%s\n", out
  }'
}

# Print the absolute path inside the target root. Absolute symlinks are
# re-rooted there; they are not opened on the initramfs host.
resolve_under() {
  root=$1
  path=$2
  depth=0
  while [ "$depth" -lt 8 ]; do
    path=$(norm_abs "$path")
    full=$root$path
    if [ -L "$full" ]; then
      link=$(readlink "$full") || return 1
      case $link in
        /*) path=$link ;;
        *)
          dir=${path%/*}
          [ -n "$dir" ] || dir=/
          path=$dir/$link
          ;;
      esac
      depth=$((depth + 1))
      continue
    fi
    if [ -e "$full" ]; then
      printf '%s\n' "$path"
      return 0
    fi
    return 1
  done
  return 1
}

field() {
  key=$1
  file=$2
  sed -n "s/^${key} //p" "$file" | head -n 1
}

verify_root_init() {
  root=$1
  rel=$2
  case $rel in
    /*) ;;
    *) say "VERIFY $rel not-absolute"; return 1 ;;
  esac
  full=$root$rel
  if [ ! -e "$full" ] && [ ! -L "$full" ]; then
    say "VERIFY $rel absent"
    return 1
  fi
  if ! resolved=$(resolve_under "$root" "$rel"); then
    say "VERIFY $rel resolve-failed"
    return 1
  fi
  say "STAT $rel $(stat -c '%F %a' "$full" 2>/dev/null || echo unstatable)"
  say "RESOLVED $rel $resolved"
  info=$(run_dir)/m4diag-file.txt
  elf-interp "$root$resolved" > "$info"
  kind=$(field KIND "$info")
  interp=$(field INTERP "$info")
  say "FILE $rel kind=$kind interp=${interp:-none}"
  case $kind in
    script)
      [ -n "${interp:-}" ] && [ "$interp" != "none" ] || { say "VERIFY $rel no-shebang"; return 1; }
      case $interp in
        /*) ;;
        *) say "VERIFY $rel interpreter-not-absolute $interp"; return 1 ;;
      esac
      if ! ipath=$(resolve_under "$root" "$interp"); then
        say "VERIFY $rel interpreter-absent $interp"
        return 1
      fi
      if [ ! -x "$root$ipath" ]; then
        say "VERIFY $rel interpreter-not-executable $interp"
        return 1
      fi
      say "INTERP_FILE $rel $ipath"
      base=${ipath##*/}
      case $base in
        bash|sh|ash)
          set +e
          chroot "$root" "$interp" -n "${rel#/}" > "$(run_dir)/m4diag-verify.txt" 2>&1
          rc=$?
          set -e
          say "VERIFY $rel script $interp rc=$rc"
          inspect_script "$root$resolved" || say "SCRIPT_NOTE inspect-failed"
          if [ "$rc" -eq 0 ]; then
            return 0
          fi
          return 1
          ;;
        *)
          say "VERIFY $rel script $interp not-a-shell"
          return 1
          ;;
      esac
      ;;
    elf)
      [ -n "${interp:-}" ] && [ "$interp" != "none" ] && [ "$interp" != "unreadable" ] || {
        say "VERIFY $rel interp-unreadable"
        return 1
      }
      case $interp in
        /*) ;;
        *) say "VERIFY $rel interpreter-not-absolute $interp"; return 1 ;;
      esac
      if ! ipath=$(resolve_under "$root" "$interp"); then
        say "VERIFY $rel interpreter-absent $interp"
        return 1
      fi
      if [ ! -x "$root$ipath" ]; then
        say "VERIFY $rel interpreter-not-executable $interp"
        return 1
      fi
      say "INTERP_FILE $rel $ipath"
      set +e
      chroot "$root" "$interp" --list "${rel#/}" > "$(run_dir)/m4diag-verify.txt" 2>&1
      rc=$?
      set -e
      if grep -q "not found" "$(run_dir)/m4diag-verify.txt"; then
        say "VERIFY $rel elf $interp missing-lib rc=$rc"
        return 1
      fi
      if [ "$rc" -ne 0 ]; then
        say "VERIFY $rel elf $interp rc=$rc"
        return 1
      fi
      say "VERIFY $rel elf $interp rc=$rc"
      return 0
      ;;
    *)
      say "VERIFY $rel not-executable-kind $kind"
      return 1
      ;;
  esac
}

report_extra() {
  root=$1
  for rel in /sbin/init /bin/bash /usr/bin/bash /lib/ld-linux-aarch64.so.1 /usr/lib/ld-linux-aarch64.so.1; do
    full=$root$rel
    if [ ! -e "$full" ] && [ ! -L "$full" ]; then
      say "EXTRA $rel absent"
      continue
    fi
    resolved=$(resolve_under "$root" "$rel" 2>/dev/null) || {
      say "EXTRA $rel resolve-failed"
      continue
    }
    if ! elf-interp "$root$resolved" > "$(run_dir)/m4diag-extra.txt" 2>/dev/null; then
      say "EXTRA $rel elf-interp-failed"
      continue
    fi
    summary=$(tr '\n' ' ' < "$(run_dir)/m4diag-extra.txt" 2>/dev/null) || summary=unreadable
    say "EXTRA $rel $resolved ${summary:-empty}"
  done
  return 0
}

inspect_script() {
  file=$1
  say "SCRIPT_NOTE syntax-only; declared exec lines are not run"
  head -n 20 "$file" 2>/dev/null | while IFS= read -r line; do
    say "SCRIPT_HEAD $line"
  done || say "SCRIPT_HEAD unreadable"
  grep -n -e 'exec ' -e 'init=' "$file" 2>/dev/null | head -n 10 | while IFS= read -r line; do
    say "SCRIPT_DECL $line"
  done || true
  return 0
}

post_named() {
  name=$1
  body=$2
  case $name in
    m4diag-start.txt|m4diag-checks.txt|m4diag-handoff.txt) ;;
    *) say "NET_EXPORT rejected $name"; return 0 ;;
  esac
  parse_url
  if [ -z "${M4_HOST:-}" ] || [ -z "${M4_PORT:-}" ]; then
    say "NET_EXPORT $name skipped"
    return 0
  fi
  case $M4_PATH in
    /up) ;;
    *) say "NET_EXPORT $name bad-path"; return 0 ;;
  esac
  len=$(wc -c < "$body" | tr -d ' ')
  attempt=0
  while [ "$attempt" -lt 2 ]; do
    attempt=$((attempt + 1))
    set +e
    {
      printf 'PUT /up/%s HTTP/1.0\r\nHost: %s\r\nContent-Type: text/plain\r\nContent-Length: %s\r\nConnection: close\r\n\r\n' \
        "$name" "$M4_HOST" "$len"
      cat "$body"
    } | nc -w 5 "$M4_HOST" "$M4_PORT" > "$(run_dir)/m4diag.http" 2>"$(run_dir)/m4diag.nc"
    rc=$?
    set -e
    status=$(head -n 1 "$(run_dir)/m4diag.http" 2>/dev/null || true)
    case $status in
      'HTTP/1.0 2'*|'HTTP/1.1 2'*)
        say "NET_EXPORT $name ok attempt=$attempt $status"
        return 0
        ;;
    esac
    say "NET_EXPORT $name fail attempt=$attempt rc=$rc ${status:-none}"
  done
  return 0
}

linker_report() {
  root=/sysroot
  if ! mountpoint -q "$root" 2>/dev/null; then
    if [ ! -d "$root/bin" ] && [ ! -d "$root/usr" ]; then
      say "LINKER root-not-mounted"
      return 0
    fi
  fi
  if [ -L "$root/lib" ]; then
    say "LINKER_LIB symlink $(readlink "$root/lib" 2>/dev/null || echo unread)"
  elif [ -d "$root/lib" ]; then
    say "LINKER_LIB directory"
  else
    say "LINKER_LIB absent"
  fi
  for rel in /lib/ld-linux-aarch64.so.1 /usr/lib/ld-linux-aarch64.so.1 /bin/bash /usr/local/sbin/rover-init; do
    if [ -e "$root$rel" ] || [ -L "$root$rel" ]; then
      if [ -x "$root$rel" ]; then
        say "LINKER_PATH $rel executable"
      else
        say "LINKER_PATH $rel present-not-executable"
      fi
    else
      say "LINKER_PATH $rel absent"
    fi
  done
  if [ -e "$root/bin/bash" ] || [ -L "$root/bin/bash" ]; then
    if resolved=$(resolve_under "$root" /bin/bash 2>/dev/null); then
      elf-interp "$root$resolved" > "$(run_dir)/m4diag-bash.txt" 2>/dev/null || true
      say "LINKER_BASH $(tr '\n' ' ' < "$(run_dir)/m4diag-bash.txt" 2>/dev/null || echo unread)"
    fi
  fi
  return 0
}

gadget_report() {
  found=0
  if [ -d /sys/class/udc ]; then
    for u in /sys/class/udc/*; do
      [ -e "$u" ] || continue
      found=1
      say "UDC ${u##*/}"
    done
  fi
  if [ "$found" -eq 0 ]; then
    say "USB_GADGET_UNSUPPORTED no-udc"
    say "USB_GADGET_REASON dwc3-apple probe sets device-mode off; CONFIG_USB_GADGET unset; CONFIG_USB_DWC3_HOST=y"
  fi
  return 0
}

wifi_setup() {
  fw="/usr/lib/firmware/brcm/brcmfmac4388c0-pcie.apple,hostomel.bin"
  if [ ! -s "$fw" ]; then
    say "WIFI_FIRMWARE_ABSENT"
    return 0
  fi
  if [ ! -r /etc/m4diag-wifi-modules ]; then
    say "WIFI_MODULES_LIST_ABSENT"
    return 0
  fi
  board=
  bfile=/proc/device-tree/pcie@1cb0000000/pci@0,0/wifi@0,0/brcm,board-type
  if [ -r "$bfile" ]; then
    board=$(tr -d '\000\n' < "$bfile" 2>/dev/null || true)
  fi
  case $board in
    apple,hostomel) say "WIFI_BOARD dt-hostomel" ;;
    "") say "WIFI_BOARD absent"; return 0 ;;
    *) say "WIFI_BOARD unexpected"; return 0 ;;
  esac
  if [ -L "/usr/lib/firmware/brcm/brcmfmac4388c2-pcie.apple,hostomel.bin" ]; then
    say "WIFI_FW_C2_LINK present"
  else
    say "WIFI_FW_C2_LINK absent"
  fi
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    case $name in
      \#*) continue ;;
    esac
    load_one "$name"
  done < /etc/m4diag-wifi-modules
  for t in /sys/class/rfkill/*/type; do
    [ -r "$t" ] || continue
    typ=$(cat "$t" 2>/dev/null || true)
    if [ "$typ" = "wlan" ] || [ "$typ" = "wifi" ]; then
      echo 0 > "${t%type}soft" 2>/dev/null || true
    fi
  done
  say "WIFI_RFKILL checked"
  iface=
  i=0
  while [ "$i" -lt 10 ]; do
    for n in /sys/class/net/wlan*; do
      [ -e "$n" ] || continue
      iface=${n##*/}
      break
    done
    [ -n "$iface" ] && break
    sleep 1
    i=$((i + 1))
  done
  if [ -z "$iface" ]; then
    say "WIFI_NO_IFACE"
    return 0
  fi
  say "WIFI_IFACE $iface"
  if [ ! -x /usr/sbin/wpa_supplicant ] || [ ! -r /usr/bin/nm-wifi.awk ] || [ ! -r /usr/bin/nm-wifi.sh ]; then
    say "WIFI_SUPPLICANT_ABSENT"
    return 0
  fi
  if [ ! -d /sysroot/etc/NetworkManager/system-connections ]; then
    say "WIFI_NO_PROFILE"
    return 0
  fi
  # shellcheck disable=SC1091
  . /usr/bin/nm-wifi.sh
  rund=$(run_dir)/m4wifi
  mkdir -p "$rund"
  nm_wifi_select /sysroot/etc/NetworkManager/system-connections "$rund" > "$rund/select" || true
  while IFS= read -r line; do
    case $line in
      WIFI_*) say "$line" ;;
      REJECT\ *) say "WIFI_$line" ;;
    esac
  done < "$rund/select"
  saved=
  if [ -s "$(run_dir)/m4ssh.addr" ]; then
    saved=$(head -n 1 "$(run_dir)/m4ssh.addr")
  fi
  rm -f "$(run_dir)/m4ssh.addr"
  got=0
  # Default IFS splits "1 1". IFS= would leave nslot empty and skip the conf.
  while IFS=' ' read -r _try nslot; do
    case $nslot in
      ''|*[!0-9]*) say "WIFI_SLOT_BAD"; continue ;;
    esac
    conf="$rund/$nslot.conf"
    meta="$rund/$nslot.meta"
    if [ ! -f "$conf" ]; then
      say "WIFI_SLOT_BAD"
      continue
    fi
    want=$(awk '$1=="iface"{print $2; exit}' "$meta" 2>/dev/null || true)
    if [ -n "$want" ] && [ "$want" != "$iface" ]; then
      say "WIFI_SKIP iface"
      continue
    fi
    if [ -f "$rund/wpa.pid" ]; then
      kill "$(cat "$rund/wpa.pid" 2>/dev/null)" 2>/dev/null || true
      rm -f "$rund/wpa.pid"
    fi
    mkdir -p "$rund/ctrl"
    if ! /usr/sbin/wpa_supplicant -B -qq -i "$iface" -c "$conf" -P "$rund/wpa.pid" >/dev/null 2>&1; then
      say "WIFI_SUPPLICANT_FAIL"
      continue
    fi
    state=
    j=0
    while [ "$j" -lt 20 ]; do
      state=$(/usr/sbin/wpa_cli -p "$rund/ctrl" -i "$iface" status 2>/dev/null | awk -F= '$1=="wpa_state"{print $2; exit}')
      [ "$state" = "COMPLETED" ] && break
      sleep 1
      j=$((j + 1))
    done
    if [ "$state" != "COMPLETED" ]; then
      say "WIFI_ASSOC_FAIL"
      if [ -s "$rund/wpa.pid" ]; then
        kill "$(cat "$rund/wpa.pid")" 2>/dev/null || true
      fi
      continue
    fi
    say "WIFI_ASSOCIATED"
    set +e
    udhcpc -n -q -t 3 -T 3 -i "$iface" -s /usr/share/udhcpc/m4.script
    rc=$?
    set -e
    say "WIFI_DHCP $rc"
    if [ -s "$(run_dir)/m4ssh.addr" ]; then
      say "WIFI_ADDRESS_READY"
      got=1
      break
    fi
    say "WIFI_NO_ADDRESS"
    if [ -s "$rund/wpa.pid" ]; then
      kill "$(cat "$rund/wpa.pid")" 2>/dev/null || true
    fi
  done << EOF
$(awk '$1=="WIFI_TRY"{print $2 " " $3}' "$rund/select" 2>/dev/null || true)
EOF
  if [ "$got" -eq 0 ] && [ -n "$saved" ]; then
    printf '%s\n' "$saved" > "$(run_dir)/m4ssh.addr"
    say "WIFI_KEPT_OTHER_ADDRESS"
  fi
  return 0
}

start_ssh() {
  if [ ! -s /etc/m4diag-authorized_keys ]; then
    say "SSH_NO_KEY"
    return 0
  fi
  if [ ! -x /usr/sbin/dropbearmulti ]; then
    say "SSH_NO_SERVER"
    return 0
  fi
  mkdir -p /root/.ssh "$(run_dir)" || { say "SSH_DIR_FAIL"; return 0; }
  cp /etc/m4diag-authorized_keys /root/.ssh/authorized_keys || { say "SSH_KEY_COPY_FAIL"; return 0; }
  chmod 700 /root /root/.ssh || true
  chmod 600 /root/.ssh/authorized_keys || true
  if ! /usr/sbin/dropbearmulti dropbearkey -t ed25519 -f "$(run_dir)/dropbear_ed25519_host_key" > "$(run_dir)/dropbearkey.out" 2>&1; then
    say "SSH_HOSTKEY_FAIL"
    return 0
  fi
  addr=
  if [ -s "$(run_dir)/m4ssh.addr" ]; then
    addr=$(head -n 1 "$(run_dir)/m4ssh.addr")
  fi
  case $addr in
    *.*.*.*) ;;
    *) say "SSH_NO_ADDRESS"; return 0 ;;
  esac
  /usr/sbin/dropbearmulti dropbear -j -k -r "$(run_dir)/dropbear_ed25519_host_key" -p "$addr:22" -P "$(run_dir)/dropbear.pid" > "$(run_dir)/dropbear.out" 2>&1 &
  p=$!
  i=0
  while [ "$i" -lt 3 ]; do
    if [ -s "$(run_dir)/dropbear.pid" ]; then
      say "SSH_LISTEN $addr:22"
      return 0
    fi
    if ! child_ok "$p" || ! kill -0 "$p" 2>/dev/null; then
      break
    fi
    sleep 1
    i=$((i + 1))
  done
  say "SSH_LISTEN_FAIL"
  return 0
}

mount_ro() {
  rootarg=$1
  i=0
  dev=
  while [ "$i" -lt 8 ]; do
    dev=$(blkid -t "$rootarg" -o device 2>/dev/null | head -n 1 || true)
    [ -n "$dev" ] && break
    sleep 1
    i=$((i + 1))
  done
  say "ROOT_DEV ${dev:-none}"
  if [ -z "$dev" ]; then
    return 1
  fi
  M4_ROOT_DEV=$dev
  if mount -t ext4 -o ro,noload "$dev" /sysroot; then
    say "MOUNT ro,noload $dev"
    return 0
  fi
  say "MOUNT_RO_FAIL $dev"
  return 1
}

rescue_shell() {
  linker_report || say "LINKER_REPORT_FAIL"
  gadget_report || say "GADGET_REPORT_FAIL"
  wifi_setup || say "WIFI_FAIL"
  start_ssh || say "SSH_START_FAIL"
  cp "$LOG" "$(run_dir)/m4diag-checks.body" 2>/dev/null || true
  post_named m4diag-checks.txt "$(run_dir)/m4diag-checks.body" || true
  say "HOLD configured init was not started"
  say "ROOT_STAYS ro,noload"
  exec /usr/bin/ash
  while :; do sleep 3600; done
}

remount_rw() {
  umount /sysroot || { say "UMOUNT_FAIL"; return 1; }
  if mount -t ext4 -o rw "$M4_ROOT_DEV" /sysroot; then
    say "MOUNT rw"
    return 0
  fi
  say "MOUNT_RW_FAIL"
  mount -t ext4 -o ro,noload "$M4_ROOT_DEV" /sysroot || say "MOUNT_RO_AGAIN_FAIL"
  return 1
}

m4diag_main() {
  export PATH=/usr/bin:/usr/sbin:/bin:/sbin
  mkdir -p /run /proc /sys /dev /sysroot /etc
  mount -t proc proc /proc 2>/dev/null || true
  mount -t sysfs sysfs /sys 2>/dev/null || true
  mount -t devtmpfs devtmpfs /dev 2>/dev/null || true
  LOG=$(run_dir)/m4diag.txt
  : > "$LOG"
  say "M4DIAG 1"
  say "UNAME $(uname -a 2>/dev/null || echo none)"
  say "UNAME_R $(uname -r 2>/dev/null || echo none)"
  say "CMDLINE $(cat /proc/cmdline 2>/dev/null || echo none)"
  if [ -r /etc/m4diag-limits ]; then
    while IFS= read -r line; do
      [ -n "$line" ] && say "LIMIT $line"
    done < /etc/m4diag-limits
  fi
  rel=$(uname -r 2>/dev/null || echo none)
  if [ ! -e "/lib/modules/$rel/modules.dep" ]; then
    say "MODULES_DEP_ABSENT $rel"
  fi
  if [ -f /usr/lib/firmware/apple/tpmtfw-j616s.bin ]; then
    say "FIRMWARE packed apple/tpmtfw-j616s.bin"
  else
    say "FIRMWARE packed-file-absent"
  fi
  cp "$LOG" "$(run_dir)/m4diag-start.body"
  load_list nvme 15
  initarg=/sbin/init
  rootarg=
  for w in $(cat /proc/cmdline 2>/dev/null || true); do
    case $w in
      root=*) rootarg=${w#root=} ;;
      init=*) initarg=${w#init=} ;;
    esac
  done
  say "INIT_ARG $initarg"
  verified=0
  if [ -n "$rootarg" ] && mount_ro "$rootarg"; then
    report_extra /sysroot
    if verify_root_init /sysroot "$initarg"; then
      verified=1
    fi
  else
    say "VERIFY mount-failed"
  fi
  load_list early 30
  set +e
  net_setup
  set -e
  post_named m4diag-start.txt "$(run_dir)/m4diag-start.body"
  mode=
  if [ -f /etc/m4diag-ssh ]; then
    mode=$(tr -d ' \t\r\n' < /etc/m4diag-ssh)
  fi
  if [ "$mode" = "ro-hold" ]; then
    say "CLASS diagnostic-hold"
    say "SSH_HOLD no-handoff no-rw"
    rescue_shell
  fi
  if [ "$verified" -eq 1 ] && [ -x /usr/bin/switch_root ] && remount_rw; then
    say "CLASS handoff"
    say "SWITCH_ROOT_ATTEMPT $initarg"
    say "LIMIT final exec is irreversible"
    say "LIMIT no fallback after this exec"
    cp "$LOG" "$(run_dir)/m4diag-checks.body" 2>/dev/null || true
    post_named m4diag-checks.txt "$(run_dir)/m4diag-checks.body" || true
    cp "$LOG" "$(run_dir)/m4diag-handoff.body" 2>/dev/null || true
    post_named m4diag-handoff.txt "$(run_dir)/m4diag-handoff.body" || true
    # Packed util-linux switch_root does chdir, MS_MOVE of proc/sys/dev/run,
    # chroot, then exec. BusyBox ash treats a failed exec as fatal.
    exec /usr/bin/switch_root /sysroot "$initarg"
  fi
  if [ "$verified" -eq 1 ] && [ ! -x /usr/bin/switch_root ]; then
    say "SWITCH_ROOT_ABSENT"
  fi
  if [ "$mode" = "boot" ]; then
    say "HANDOFF_PRE_EXEC_FAIL"
    say "FALLBACK_WIFI pre-exec-only"
    say "CLASS diagnostic-hold"
    rescue_shell
  fi
  say "CLASS diagnostic-hold"
  cp "$LOG" "$(run_dir)/m4diag-checks.body" 2>/dev/null || true
  post_named m4diag-checks.txt "$(run_dir)/m4diag-checks.body" || true
  say "HOLD configured init was not started"
  say "ROOT_STAYS ro,noload"
  exec /usr/bin/ash
  while :; do sleep 3600; done
}
