# 1TR phone relay — published bootstrap

Synthetic publish of the read-only Android Termux relay. No phone, hotspot, Tailscale, or Mac recovery session was used. Nothing on the installed machine or its boot entries was changed.

## Public pin

Verified with an unauthenticated HTTPS GET (`HTTP 200`, no redirect) on 2026-11-06:

- URL: `https://raw.githubusercontent.com/fbalazsy/omarchy-m4/904e5c788660375104ebcedbf18003ea080754bf/tools/1tr-phone-relay/termux-bootstrap.sh`
- Commit: `904e5c788660375104ebcedbf18003ea080754bf`
- SHA256: `1932ef33b9e9da52f2ff4dbf7dd71b6bef1ffeb9df921550c1f08b423ea9335f`

The downloaded bytes match that commit. The shortest command is:

```sh
curl -fsSL https://raw.githubusercontent.com/fbalazsy/omarchy-m4/904e5c788660375104ebcedbf18003ea080754bf/tools/1tr-phone-relay/termux-bootstrap.sh|sh
```

No short link was created. A branch-tip raw URL is not the pin.

The bootstrap installs `python` with Termux `pkg`, installs `iproute2` when the mirror has it, and downloads the payload from commit `1cca129333b9e5df9ee8c9ec7d561fa71e00c085`. It does not start the relay unless these SHA256 values match:

| File | SHA256 |
|---|---|
| `phone_relay.py` | `5817b276a6a5a255b3df355f5b9e266a12f32697f12a8418aceb078fdedaddd6` |
| `client_template.sh` | `b8418744ca3bd319c920b877d8b659f7f20b887785a7f43241cbe4f35c1d821d` |
| `run-1tr-phone-relay.sh` | `824717c7637dccabf53104677afa5ba2519fb219aa20b85e0c49a0e98dead2a2` |

Those three objects were also fetched anonymously from that payload commit and matched.

## What the relay will do

The Mac in recovery runs only this allowlist: `sw_vers`, `diskutil list`, `diskutil list internal`, `diskutil apfs listVolumeGroups`, `mount` with no arguments, and `uname -a`, plus a stop. The phone's LAN port can collect that text. It cannot queue commands. Queueing is only on the Tailscale address, and only for those same operations.

## What was checked here

`python3 -B tools/1tr-phone-relay/tests/test_relay.py` passed (9 tests). That run is local loopback only. It rejects unknown operations, a `Host` header that is not an IPv4 address, result names that leave the results directory, and a payload byte that does not match the pinned SHA256. The mismatched payload is not left in place and the relay is not started. `tools/privacy-check.py` is clean for this tree. No session token, disk inventory, key, or LAN address is in the commit.

## Still manual

1. Install Termux from F-Droid or the official GitHub releases, and open it once. Official Termux already includes `curl`.
2. Give Termux a path to GitHub and the Termux package mirror. On many phones, turning the hotspot on drops Wi-Fi, so download before that if mobile data is the remaining path.
3. Paste the one command above, or a short link made from it after the SHA256 is checked. The first run waits on `pkg`. If Termux asks for a mirror, finish that and run the command again. If install fails on an old Termux, run `pkg upgrade` once and run the command again.
4. Turn the hotspot on and leave Termux in the foreground. If the hotspot address appears after startup, the phone prints a new paste line.
5. Boot the Mac into recovery, join that hotspot, and paste the one line the phone prints. That line is a session secret. Do not put it in chat, GitHub, or a shortener. The hotspot hop is plain HTTP, so no other client should be on it.
6. Connect the official Tailscale app only if a bot should use the MCP URL printed on the phone. Do not share that VPN with hotspot clients.
7. Stop with Ctrl-C. Inventory stays on the phone under `~/.local/share/1tr-phone-relay/results` until that directory is deleted.

Live Android, hotspot routing, Termux address visibility, and tailnet reachability are not verified.
