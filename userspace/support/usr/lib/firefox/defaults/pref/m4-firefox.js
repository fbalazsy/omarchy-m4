// m4-support (GR-09): Firefox defaults for the aurora GPU and hardware video decoding on the M4's AVD (VA-API through
// libva-v4l2_request-avd, zero-copy into WebRender), as in daily use since 2026-10-21 (7.1.12-m4.17). These are
// defaults: about:config and user.js win. VA-API needs the RDD sandbox off: the Hyprland session sets
// MOZ_DISABLE_RDD_SANDBOX=1 when the GPU and the decoder are up (/usr/lib/m4/session-env.sh); without it Firefox
// silently decodes in software.
// shortcut: static defaults, so a boot without the GPU driver still asks for GPU WebRender (Firefox falls back to
// software WebRender when GL fails); make them conditional (autoconfig on the session variable) if that bites.
pref("gfx.webrender.software", false);
pref("gfx.webrender.all", true);
pref("layers.acceleration.force-enabled", true);
pref("gfx.webrender.compositor", false);
pref("widget.dmabuf.force-enabled", true);
pref("media.hardware-video-decoding.enabled", true);
pref("media.hardware-video-decoding.force-enabled", true);
pref("media.ffmpeg.vaapi.enabled", true);
pref("media.av1.enabled", true);
// Not media.ffmpeg.disable-software-fallback: it keeps 4K60 AV1 from tripping the slow-decode check, but also removes the
// software path whenever VA-API cannot run (apple_avd blacklisted, a profile the AVD does not decode). Set it in user.js.
// WebGL is not yet qualified on real pages on the aurora GPU.
pref("webgl.disabled", true);
