-- M4 desktop profile for Hyprland (selected by the m4-hyprland session entry: `start-hyprland -- --config /usr/share/m4/hyprland.lua`).
-- It loads the owner's own Hyprland config first and then pins the feature set the M4 GPU stack has been tested with. No user file is edited.
-- Kernel/Mesa admission remains authoritative; this is not an app sandbox.
local home = assert(os.getenv("HOME"), "HOME missing")
local named_desktop = os.getenv("XDG_CURRENT_DESKTOP") or ""
-- The normal startup callback (UWSM/DBus imports, the Omarchy shell supervisor) is preserved: the user's file runs unchanged.
dofile(home .. "/.config/hypr/hyprland.lua")
-- Normal defaults set Hyprland alone; preserve the session's name on reload.
if named_desktop ~= "" then hl.env("XDG_CURRENT_DESKTOP", named_desktop) end
-- Omarchy's own look (animations, blur, shadows, rounding, borders, the hardware cursor) is left to the user and theme
-- configuration since 2026-10-25: it ran clean on the aurora GPU stack (owner's live check on 7.3.0-m4.75). Only the
-- untested render paths below stay pinned. VRR is on: the kernel offers it only where the DCP programs adaptive sync
-- (aurora-silicon/linux#451 on the desktop 26.6 firmware); without that kernel no output is VRR-capable.
hl.config({
  input = { repeat_rate = 40, repeat_delay = 250 },
  general = { allow_tearing = false },
  render = {
    cm_enabled = false,
    use_fp16 = 0,
    cm_auto_hdr = 0,
    keep_unmodified_copy = 0,
    direct_scanout = 0,
    use_shader_blur_blend = false,
  },
  misc = { vrr = 1 },
  -- X11 clients (Wine games such as Hearthstone) run through Xwayland; native toolkits stay on Wayland below.
  -- At the panel's scale 2, zero scaling lets them render at native resolution (Wine DPI 192) instead of 1x upscaled.
  xwayland = { enabled = true, force_zero_scaling = true },
})
-- default.hypr.envs changes QT_QPA_PLATFORM to wayland;xcb. Restore the tested client selection after that import.
hl.env("QT_QPA_PLATFORM", "wayland")
hl.env("QT_WAYLAND_CLIENT_BUFFER_INTEGRATION", "wayland-egl")
hl.env("QSG_RHI_BACKEND", "opengl")
hl.env("QSG_RENDER_LOOP", "basic")
hl.env("GDK_BACKEND", "wayland")
hl.env("GSK_RENDERER", "gl")
hl.env("GDK_DISABLE", "vulkan")
hl.config({ misc = { disable_xdg_env_checks = true } })

-- The keyboard light is the standard LED class device (apple::kbd_backlight) and Omarchy's own helper drives it. The MacBook top row has
-- no keyboard-light keys, so add SUPER + brightness keys; Omarchy's XF86Kbd* bindings stay as they are.
local o = _G.o
if o then
  o.bind("SUPER + XF86MonBrightnessUp", "Keyboard backlight up", "omarchy-brightness-keyboard up", { repeating = true })
  o.bind("SUPER + XF86MonBrightnessDown", "Keyboard backlight down", "omarchy-brightness-keyboard down", { repeating = true })
  o.bind("SUPER + SHIFT + XF86MonBrightnessUp", "Keyboard backlight cycle", "omarchy-brightness-keyboard cycle")
end

-- Hyprland resolves "preferred" to 60 Hz on the internal panel although the driver flags 120 Hz preferred: run it at its highest rate
-- like the greeter. Scoped to eDP-1, after the user config, so rules for external displays still apply; inside pcall so an error cannot skip the above.
pcall(function()
  hl.monitor({ output = "eDP-1", mode = "highrr", position = "auto", scale = 2 })
end)
