# Read-only 1TR phone relay

Termux on the phone runs a short-lived relay. The Mac in one-time recovery (1TR) joins the phone hotspot and runs a fixed inventory script. A Tailscale MCP endpoint on the phone can queue those same inventory operations and read the text that comes back.

This is a diagnostic allowlist. It is not a remote shell.

## One command (Termux)

Install Termux from F-Droid or the official Termux GitHub releases. In Termux:

```sh
curl -fsSL https://raw.githubusercontent.com/fbalazsy/omarchy-m4/102a39045d35b77af5f811af6a8323186180ff0b/tools/1tr-phone-relay/termux-bootstrap.sh | sh
```

That URL is commit `102a39045d35b77af5f811af6a8323186180ff0b`, not the branch tip. SHA256 of the bootstrap file: `628ed7d275354f91e1c8c9a95e98a8765f39b46e8438ad21ecd23333ddeb655d`. A public GET of that URL returned 200 on 2026-11-06. Stop the previous relay with Ctrl-C before running it. Details are in `reports/2026-11-06-1tr-phone-relay.md`. This repository does not create a TinyURL or any other short link.

The script does not read stdin, so `curl | sh` is safe for the script itself. It installs `python` with Termux `pkg` (and `iproute2` when the mirror has it), downloads `phone_relay.py`, `client_template.sh` and `run-1tr-phone-relay.sh` from one pinned commit, refuses to start unless each SHA256 matches, and starts the relay. Payload files land in `~/.local/share/1tr-phone-relay` on the phone.

## Allowlist

The Mac runs only these operations. Both `phone_relay.py` and `client_template.sh` encode the same set. `rejected` is a stored report, not a Mac command.

| Op | Command |
|---|---|
| `inventory` | `sw_vers`, `diskutil list`, `diskutil list internal`, `diskutil apfs listVolumeGroups`, `mount` (list only) |
| `sw_vers` | `sw_vers` |
| `diskutil_list` | `diskutil list` |
| `diskutil_list_internal` | `diskutil list internal` |
| `apfs_list_volume_groups` | `diskutil apfs listVolumeGroups` |
| `mount_list` | `mount` (list only) |
| `uname` | `uname -a` |
| `stop` | exit the 1TR client and stop the relay |

No other operation is queued or executed. Arguments on the MCP `enqueue` tool besides `op` are ignored. The LAN port cannot queue work. The hotspot callback URL is built only from an IPv4 address and the session token, so a hostile `Host` header is not copied into the recovery script.

The relay does not mount or unmount disks, erase or partition anything, run `bless` or `nvram`, write the ESP, or change a boot entry.

## Boundaries

- The hotspot side is plain HTTP. Use a hotspot with no other clients. Anyone who can read the session URL can fetch the inventory script and post result text. They still cannot queue commands on that port.
- The session token is created when the relay starts, printed only on the phone, and stored in `state/session` mode `0600`. It is not in this tree.
- Address discovery uses `SIOCGIFADDR` because Android denies Termux the netlink socket `ip` needs. A missing address is not treated as proof that the hotspot or the official Tailscale app is off, and no address is invented. The paste line is printed only for a private address this process can actually see. Cellular interfaces are not used.
- Not seeing the VPN address is separate from delivery. Android often denies Termux the netlink lookup and does not assign the official app's VPN address inside Termux, so this process cannot bind that address. Tailscale staff have said a normal Termux listener is reachable over Tailscale anyway (issue 20209). MCP therefore listens on every interface, and closes the socket before reading a request unless both the destination and the peer are Tailscale addresses. Loopback is accepted only from this phone. A hotspot client is not given the queue. Do not share the VPN with hotspot clients. If Termux cannot see the address, the host to dial is the one shown in the official app. It is not guessed.
- Results are disk inventory. They stay on the phone (`results/`, mode `0600`) and must not be committed or pasted into chat.
- The relay stops after two hours, or sooner with Ctrl-C or `request_stop`.

## Tests

Synthetic tests only (Linux, local loopback):

```sh
python3 -B tools/1tr-phone-relay/tests/test_relay.py
```

They check the allowlist, rejection of unknown operations, Host-header filtering, result-path containment, the 1TR client's refusal to run anything outside the allowlist, and (once `termux-bootstrap.sh` is in the tree) the pinned hashes and a local HTTPS fetch. They do not exercise a phone, a hotspot, Tailscale, or a Mac in recovery.

## What you still do by hand

1. Install official Termux and open it once.
2. Give Termux a network path to GitHub and the Termux package mirror before the hotspot takes the Wi-Fi. On many phones the hotspot uses mobile data and disconnects Wi-Fi.
3. Run the one command above (or the shortened link, after its SHA256 is checked against the report). The first run waits while `pkg` downloads Python.
4. If Termux asks you to pick a mirror, finish `termux-change-repo` and run the command again. If `pkg install` fails on an old install, run `pkg upgrade` once and run the command again.
5. Turn the hotspot on. Keep Termux in the foreground. If a hotspot address becomes visible after the relay starts, the phone prints a new paste line. If it still says no private address is visible, Android is hiding the tether interface; do not type an address.
6. Boot the Mac into 1TR, join that hotspot, and paste the one line shown on the phone into the recovery Terminal. That line is a secret. Do not put it in chat, GitHub, or a shortener.
7. For the bot, use the phone's address shown in the official Tailscale app and this relay's MCP port. Do not type that address into the relay. The relay prints a URL only when the address is actually visible on one of its interfaces.
8. Stop with Ctrl-C. Inventory files remain on the phone until you delete that directory.

Live Android, hotspot routing, Termux address visibility and tailnet reachability are not verified here.
