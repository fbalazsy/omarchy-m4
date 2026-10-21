# N3: the fix for the N2 black screen

The GPU and display drivers are masked. Linux starts with a software desktop: Bluetooth, the power button, idle and the keyboard light work.

## 1. Install (on Linux, in a terminal)

```sh
sudo sh /var/lib/m4-repo/n3/install
```

It installs `m4-support 1.0-4` and `linux-m4-rescue`. It then shows a short cleanup list (old R-series files in `~/.config/uwsm`) and asks once: type `cleanup` to confirm. Reboot afterwards. You get the software desktop again.

## 2. Try the GPU (when you have time, at the Mac)

```sh
sudo systemctl unmask m4-gpu.service m4-display.service && sudo reboot
```

- **Works:** a GPU-rendered login screen and desktop at 120 Hz, with brightness keys.
- **Black screen:** hold the power button for 10 s. The next start is rescue mode (software desktop). Then turn the GPU off again and go back to normal:

```sh
sudo systemctl mask m4-gpu.service m4-display.service && sudo m4-boot clear
```

The black-screen case now triggers rescue: a boot only counts as good when the GPU driver actually served the login screen.

## Previous kernel

`sudo m4-boot select 7.1.13-m4` (the N1 kernel). Its modules now come from the `linux-m4-rescue` package, so they can't be deleted.
