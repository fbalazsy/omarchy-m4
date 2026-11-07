# Read one NetworkManager keyfile. Write a wpa_supplicant conf, or reject it.
# stdout is only "ACCEPT <priority>" or "REJECT <reason>". No ssid, no psk.
function bad(reason) {
  if (rejected)
    exit 0
  rejected = 1
  print "REJECT " reason
  exit 0
}

function is_hex64(s,    i, c) {
  if (length(s) != 64)
    return 0
  for (i = 1; i <= 64; i++) {
    c = substr(s, i, 1)
    if (index("0123456789abcdefABCDEF", c) == 0)
      return 0
  }
  return 1
}

function wpa_escape(s,    i, c, out) {
  out = ""
  for (i = 1; i <= length(s); i++) {
    c = substr(s, i, 1)
    if (c < " " || c > "~")
      return ""
    if (c == "\\" || c == "\"")
      out = out "\\"
    out = out c
  }
  return out
}

BEGIN {
  if (outdir == "" || slot == "")
    bad("usage")
  section = ""
  type = ""
  autoconnect = "true"
  priority = "0"
  iface = ""
  ssid = ""
  mode = ""
  hidden = ""
  keymgmt = ""
  psk = ""
  pskflags = ""
  ipv4 = ""
  have_ssid = 0
  have_psk = 0
  have_keymgmt = 0
  have_priority = 0
  have_type = 0
  have_mode = 0
  have_hidden = 0
  have_autoconnect = 0
  have_iface = 0
  have_pskflags = 0
  have_ipv4 = 0
  rejected = 0
}

/^[\t ]*$/ { next }
/^[\t ]*[#;]/ { next }

/^\[/ {
  line = $0
  sub(/^\[/, "", line)
  sub(/\][\t ]*$/, "", line)
  section = line
  next
}

{
  line = $0
  eq = index(line, "=")
  if (eq <= 1)
    bad("bad-line")
  key = substr(line, 1, eq - 1)
  val = substr(line, eq + 1)
  sub(/[\t ]+$/, "", key)
  gsub(/^[ \t]+|[ \t]+$/, "", key)
  if (key ~ /[^A-Za-z0-9_.-]/)
    bad("bad-key")
  id = section "." key
  if (id == "connection.type") {
    if (have_type)
      bad("duplicate")
    have_type = 1
    type = val
  } else if (id == "connection.autoconnect") {
    if (have_autoconnect)
      bad("duplicate")
    have_autoconnect = 1
    autoconnect = val
  } else if (id == "connection.autoconnect-priority") {
    if (have_priority)
      bad("duplicate")
    have_priority = 1
    priority = val
  } else if (id == "connection.interface-name") {
    if (have_iface)
      bad("duplicate")
    have_iface = 1
    iface = val
  } else if (id == "wifi.ssid") {
    if (have_ssid)
      bad("duplicate")
    have_ssid = 1
    ssid = val
  } else if (id == "wifi.mode") {
    if (have_mode)
      bad("duplicate")
    have_mode = 1
    mode = val
  } else if (id == "wifi.hidden") {
    if (have_hidden)
      bad("duplicate")
    have_hidden = 1
    hidden = val
  } else if (id == "wifi-security.key-mgmt") {
    if (have_keymgmt)
      bad("duplicate")
    have_keymgmt = 1
    keymgmt = val
  } else if (id == "wifi-security.psk") {
    if (have_psk)
      bad("duplicate")
    have_psk = 1
    psk = val
  } else if (id == "wifi-security.psk-flags") {
    if (have_pskflags)
      bad("duplicate")
    have_pskflags = 1
    pskflags = val
  } else if (id == "ipv4.method") {
    if (have_ipv4)
      bad("duplicate")
    have_ipv4 = 1
    ipv4 = val
  }
}

END {
  if (rejected)
    exit 0
  if (type != "wifi")
    bad("not-wifi")
  if (autoconnect == "false" || autoconnect == "no" || autoconnect == "0")
    bad("autoconnect-off")
  if (autoconnect != "true" && autoconnect != "yes" && autoconnect != "1")
    bad("bad-autoconnect")
  if (priority !~ /^-?[0-9]+$/ || priority < -999 || priority > 999)
    bad("bad-priority")
  if (mode != "" && mode != "infrastructure")
    bad("not-infra")
  if (keymgmt != "wpa-psk")
    bad("unsupported-auth")
  if (pskflags != "" && pskflags != "0")
    bad("no-psk")
  if (ipv4 != "" && ipv4 != "auto")
    bad("ipv4-not-dhcp")
  if (iface != "" && iface !~ /^wlan[0-9]+$/)
    bad("iface")
  if (ssid == "" || length(ssid) > 32)
    bad("bad-ssid")
  essid = wpa_escape(ssid)
  if (essid == "")
    bad("bad-ssid")
  scan = 0
  if (hidden == "true" || hidden == "yes" || hidden == "1")
    scan = 1
  else if (hidden != "" && hidden != "false" && hidden != "no" && hidden != "0")
    bad("bad-hidden")
  psk_line = ""
  if (is_hex64(psk))
    psk_line = "\tpsk=" psk
  else if (length(psk) >= 8 && length(psk) <= 63) {
    epsk = wpa_escape(psk)
    if (epsk == "")
      bad("bad-psk")
    psk_line = "\tpsk=\"" epsk "\""
  } else
    bad("bad-psk")
  path = outdir "/" slot ".conf"
  printf "ctrl_interface=/run/m4wifi/ctrl\nap_scan=1\nnetwork={\n\tssid=\"%s\"\n%s\n\tkey_mgmt=WPA-PSK\n\tscan_ssid=%d\n}\n", essid, psk_line, scan > path
  meta = outdir "/" slot ".meta"
  extra = ""
  if (iface != "")
    extra = "iface " iface "\n"
  printf "priority %d\nscan %d\n%s", priority + 0, scan, extra > meta
  print "ACCEPT " (priority + 0)
}
