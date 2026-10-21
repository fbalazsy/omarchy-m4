# POSIX sh, sourced by sddm-compositor and session-env.sh. m4_kms_card prints the DRM node Hyprland (aquamarine) must drive, or nothing.
#
# The card numbers are not fixed: simpledrm is card0 until the display driver takes over, the GPU driver is a render-only card of its own, and the
# panel's card comes after them (a native boot's kernel log: simpledrm minor 0, asahi/m4-gpu minor 1, apple/display-subsystem minor 2). Choose by driver:
# the apple display driver's card, else the simpledrm one; never the GPU's, whatever its number, and nothing when neither is there (aquamarine
# then finds the panel on its own). The stable /dev/dri/by-path/platform-<device>-card link is printed when udev made one (its name has no ':',
# which AQ_DRM_DEVICES uses as the separator). M4_SYS_DRM and M4_DEV_DRI exist for the tests.
m4_kms_card() {
    _sys=${M4_SYS_DRM:-/sys/class/drm}
    _dev=${M4_DEV_DRI:-/dev/dri}
    _apple=
    _simple=
    for _c in "$_sys"/card[0-9]*; do
        [ -e "$_c/device/driver" ] || continue
        _n=${_c##*/}
        case $_n in *-*) continue ;; esac
        _drv=$(readlink "$_c/device/driver" 2>/dev/null) || continue
        _drv=${_drv##*/}
        case $_drv in
            apple-drm|appledrm|apple_drm) [ -n "$_apple" ] || _apple=$_n ;;
            simple-framebuffer|simpledrm) [ -n "$_simple" ] || _simple=$_n ;;
        esac
    done
    _pick=${_apple:-$_simple}
    if [ -z "$_pick" ]; then
        unset _sys _dev _apple _simple _c _n _drv _pick
        return 0
    fi
    _base=$(readlink -f "$_sys/$_pick/device" 2>/dev/null)
    _base=${_base##*/}
    _link=$_dev/by-path/platform-$_base-card
    if [ -n "$_base" ] && [ -L "$_link" ] && [ "$(readlink -f "$_link")" = "$(readlink -f "$_dev/$_pick")" ]; then
        printf '%s\n' "$_link"
    else
        printf '%s\n' "$_dev/$_pick"
    fi
    unset _sys _dev _apple _simple _c _n _drv _pick _base _link
}
