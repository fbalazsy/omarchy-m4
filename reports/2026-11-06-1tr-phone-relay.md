# 1TR phone relay — published bootstrap

Read-only Android Termux relay. The first phone run is described below from the on-screen transcript. The fix was not run on a phone. Nothing on the installed machine or its boot entries was changed.

## Public pin

Use this pin, not the earlier bootstrap. Verified with an unauthenticated HTTPS GET (`HTTP 200`, no redirect) on 2026-11-06:

- URL: `https://raw.githubusercontent.com/fbalazsy/omarchy-m4/b575130ce49608f618a27ed97d60784dff0de37d/tools/1tr-phone-relay/termux-bootstrap.sh`
- Commit: `b575130ce49608f618a27ed97d60784dff0de37d`
- SHA256: `4731dd5d7a392ddc3bdde9c596d2211ff9f676fdd93d0f274ae0f75880b0427a`

The downloaded bytes match that commit. The shortest command is:

```sh
curl -fsSL https://raw.githubusercontent.com/fbalazsy/omarchy-m4/b575130ce49608f618a27ed97d60784dff0de37d/tools/1tr-phone-relay/termux-bootstrap.sh|sh
```

No short link was created. A branch-tip raw URL is not the pin. Stop the previous relay with Ctrl-C first; the already-running process does not pick up this code.

The bootstrap installs `python` with Termux `pkg`, installs `iproute2` when the mirror has it, and downloads the payload from commit `daf369b57b76c6aae8de60b6ba8828b18e29b257`. It does not start the relay unless these SHA256 values match:

| File | SHA256 |
|---|---|
| `phone_relay.py` | `7c34d0216fe50581189f34bd7b14acd202513befa5198301f5dae0f9f48fd7d7` |
| `client_template.sh` | `b8418744ca3bd319c920b877d8b659f7f20b887785a7f43241cbe4f35c1d821d` |
| `run-1tr-phone-relay.sh` | `824717c7637dccabf53104677afa5ba2519fb219aa20b85e0c49a0e98dead2a2` |

Those three objects were also fetched anonymously from that payload commit and matched.

## What the relay will do

The Mac in recovery runs only this allowlist: `sw_vers`, `diskutil list`, `diskutil list internal`, `diskutil apfs listVolumeGroups`, `mount` with no arguments, and `uname -a`, plus a stop. The phone's LAN port can collect that text. It cannot queue commands. Queueing is only on the Tailscale address, and only for those same operations.

## Phone run

The first bootstrap did install, verify its payload, and listen on `0.0.0.0:8780`. The card then said no private hotspot address was visible and no Tailscale CGNAT address was visible, and the relay printed `mcp not started (no tailscale IP)`. That is what Android does to Termux: from Android 10, and enforced for every app on Android 13, `ip` cannot bind a netlink route socket (`Permission denied`), so the old scanner treated a failed lookup as "no addresses". `ifconfig` still learns addresses through `SIOCGIFADDR`. The official Tailscale app can be connected at the same time. Its VPN interface belongs to that app. Termux is not given the address, and the app does not forward inbound tailnet TCP to other apps.

The new relay reads addresses with `SIOCGIFADDR`, still tries `ip` when it works, and refreshes the card if a private address shows up later. It does not invent an address and it does not ask for one. A forced address is used only when this process can bind it. Cellular interfaces are not offered to the Mac. MCP is bound only to a tun-like interface that actually has a Tailscale-range address inside Termux, never to `0.0.0.0`. If the only Tailscale app is the official Android app, MCP stays off on purpose.

Confidence: high that the blank card was the netlink denial, not proof the radios were off. Medium-high that ioctl will show a normal hotspot interface (`ap0`, `swlan0`, `wlan1`, or `wlan0` when it is the only private interface). High that the official app's address still will not become an MCP listener. No further observation is required to rerun the command. If the new card still has no private address, the tether interface is hidden from Termux; do not type an address from the Tailscale app or from memory.

## What was checked here

`python3 -B tools/1tr-phone-relay/tests/test_relay.py` passed (16 tests). That run is local loopback only. It covers the allowlist, a denied `ip` command with ioctl still seeing loopback, rejection of an unassigned forced address, rejection of carrier NAT as if it were Tailscale, request-log redaction, a `Host` header that is not an IPv4 address, and a payload byte that does not match the pinned SHA256. The mismatched payload is not left in place and the relay is not started. `tools/privacy-check.py` is clean for this tree. No session token, disk inventory, key, or LAN address is in the commit. This was not re-run on the phone.

## Still manual

1. Install Termux from F-Droid or the official GitHub releases, and open it once. Official Termux already includes `curl`.
2. Give Termux a path to GitHub and the Termux package mirror. On many phones, turning the hotspot on drops Wi-Fi, so download before that if mobile data is the remaining path.
3. Paste the one command above, or a short link made from it after the SHA256 is checked. The first run waits on `pkg`. If Termux asks for a mirror, finish that and run the command again. If install fails on an old Termux, run `pkg upgrade` once and run the command again.
4. Turn the hotspot on and leave Termux in the foreground. If the hotspot address appears after startup, the phone prints a new paste line.
5. Boot the Mac into recovery, join that hotspot, and paste the one line the phone prints. That line is a session secret. Do not put it in chat, GitHub, or a shortener. The hotspot hop is plain HTTP, so no other client should be on it.
6. Connect the official Tailscale app only if a bot should use the MCP URL printed on the phone. Do not share that VPN with hotspot clients.
7. Stop with Ctrl-C. Inventory stays on the phone under `~/.local/share/1tr-phone-relay/results` until that directory is deleted.

Live Android, hotspot routing, Termux address visibility, and tailnet reachability are not verified.
