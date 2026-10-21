-- The SDDM greeter session (selected by /usr/lib/m4/sddm-compositor): the stock Omarchy greeter profile, on the internal panel's
-- highest refresh rate, with the owner's keyboard layout and no XWayland.
dofile("/usr/share/sddm/hyprland.lua")
hl.config({ debug = { disable_logs = false } })

-- Match the installed desktop output scale; Qt reads the Wayland scale.
-- "highrr" = the highest refresh rate without losing resolution: on the display driver's card that is 3456x2234@120 ("preferred"
-- is the 60 Hz mode there). On simpledrm the output has the single framebuffer mode, which is what highrr picks too.
hl.monitor({ output = "", mode = "highrr", position = "auto", scale = 2 })

-- Keyboard layout: Belgian AZERTY unless /etc/m4/greeter-kb-layout holds another layout name.
local layout = "be"
local f = io.open("/etc/m4/greeter-kb-layout", "r")
if f then
  local value = f:read("l")
  f:close()
  if value and value:match("^[%w_-]+$") then layout = value end
end
hl.config({ input = { kb_layout = layout, kb_variant = "", kb_model = "", kb_rules = "", kb_options = "compose:caps,shift:both_capslock_cancel" } })

-- The Qt greeter uses Wayland; no X11 server is needed for this compositor.
hl.config({ xwayland = { enabled = false } })
