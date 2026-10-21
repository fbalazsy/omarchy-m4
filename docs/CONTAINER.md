# A second install as a container (HP)

HP is a second, clean Omarchy on its own partition (GPT slot 3, file system label `HP`; address it by PARTUUID). Besides booting it
natively (phase 2), it runs as a `systemd-nspawn` container on the dirty root, with its Hyprland nested as a window there. This file is
what HP needs for that. The nested test passed on aurora (2026-10-19: aquamarine 0.15.1-1.4 and hyprland 0.56.2-8, omacom/omarchy-pkgs #1319 and #1320).

## What m4-support does in a container

- Every hardware unit and the boot counter (`m4-boot-pending`, `m4-boot-ok`) carry `ConditionVirtualization=!container`: they are
  skipped. HP never touches the host's hardware, and never leaves `/boot/m4/boot-pending` on its own root (its next native boot would
  start in rescue). Do **not** boot HP with `m4.safe`: that means a rescue boot.
- Nothing in HP writes `/run/m4`. The GPU belongs to the host, so HP gets the host's `/run/m4/gpu-ready` read-only; with it,
  `session-env.sh` gives HP's session aurora's Mesa (`m4-aurora-mesa`; without it: llvmpipe).
- `m4-boot` names HP's own root (`FS_IOC_GETFSUUID`), in the container and in a chroot, so kernel upgrades inside HP write correct
  entries to HP's `/boot/m4`.

## Users and the GPU (aurora)

- aurora's `asahi` driver has no per-uid render rules (`renderD128` is 0666); the G16 uid and 6-slot rules are gone.
- HP's user must still be **uid 1000** and the container must not shift uids (`PrivateUsers=no`; the stock
  `systemd-nspawn@.service` passes `-U`): the host's Wayland and PipeWire sockets accept uid 1000 only.
- Risk (STATUS G-02, G-03): a GPU recovery can drop renders of an innocent client, and nested, that client can be HP's whole
  compositor; a GPU wedged for good takes both desktops down.

## Settings (to install with HP; machine name `hp`)

`/etc/systemd/nspawn/hp.nspawn`:

```ini
[Exec]
ResolvConf=off
Boot=yes
PrivateUsers=no
Parameters=systemd.mask=sddm.service

[Files]
Bind=/dev/dri
BindReadOnly=/run/m4/gpu-ready
BindReadOnly=/run/user/1000/wayland-1:/run/host/wayland-1
BindReadOnly=/run/user/1000/hp-pulse:/run/host/pulse

[Network]
VirtualEthernet=yes
```

A drop-in for the container's unit (`systemctl edit systemd-nspawn@hp`):

```ini
[Service]
DeviceAllow=char-drm rw
```

- The image is HP's file system mounted at `/var/lib/machines/hp`. Unmount it after `machinectl poweroff hp`; after a crash with HP
  mounted, `e2fsck` it before a native boot (the loader refuses a dirty ext4).
- `VirtualEthernet=yes` gives HP its own network namespace: HP's NetworkManager, tailscaled and ufw never touch the host's interfaces,
  and HP's bluetoothd cannot reach the controller (Bluetooth sockets exist only in the host's namespace). The host still needs NAT for
  `ve-hp` and `ufw route allow in on ve-hp`. `/dev/net/tun` is already allowed by the stock unit.
- Only two things from the host session are bound: the Wayland socket (HP's window) and the directory of a separate,
  restricted Pulse server for HP (sound). Never the whole runtime directory: it holds the native PipeWire socket (camera,
  microphones), the session bus and the systemd user manager (commands as the host user), the keyring and the GnuPG agent,
  and a read-only bind does not stop anyone from connecting to a socket.
  - The Pulse server is a second `pipewire-pulse -c hp-pulse.conf` (a user unit) that listens only on
    `/run/user/1000/hp-pulse/native` with `client.access = "restricted"`, no D-Bus name and no startup modules. Through it
    HP can still use the microphones, record the host's output and change the host volume.
  - A bound socket file goes stale when the host's Hyprland restarts; HP's opener binds it again then. HP's session uses
    `WAYLAND_DISPLAY=/run/host/wayland-1` and `PULSE_SERVER=unix:/run/host/pulse/native`.
- HP's session is `start-hyprland` with the m4 Hyprland config, run inside HP by `/usr/local/bin/hp-nested-hyprland`.
- `gpu-ready` exists only while the host's GPU driver is up, so HP will not start without it.

## Open

- Keybindings: done on the host (`~/.config/hypr/hp.lua`: SUPER goes to HP while it is fullscreen, SUPER+ALT+ESCAPE leaves).
- Packages: HP's pacman.conf lists the local repo first, with aquamarine and hyprland built from #1319/#1320 until they merge
  (the `[omarchy]` builds are needed: `[extra]`, listed before it, has the unpatched ones).
- Clipboard between the two desktops, HP's idle/lock off while nested, notifications.
