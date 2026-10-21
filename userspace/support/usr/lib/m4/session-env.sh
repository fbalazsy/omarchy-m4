# POSIX sh, sourced by uwsm for the Hyprland session: with /run/m4/gpu-ready, clear every renderer key the environment may have inherited (as the
# qualified session path did) and export aurora's Mesa (/usr/lib/m4/session-env, a link to m4-aurora-mesa's file). AQ_DRM_DEVICES is the display
# driver's card, chosen at run time (/usr/lib/m4/kms-card.sh); MOZ_DISABLE_RDD_SANDBOX for Firefox's VA-API on the AVD. Without the GPU
# nothing is changed.
if [ -e /run/m4/gpu-ready ] && [ -r /usr/lib/m4/session-env ]; then
    if [ -r /usr/lib/m4/env-keys ]; then
        while IFS= read -r _m4_k; do
            case $_m4_k in ''|'#'*) ;; *) unset "$_m4_k" ;; esac
        done < /usr/lib/m4/env-keys
    fi
    while IFS='=' read -r _m4_k _m4_v; do
        case $_m4_k in ''|'#'*) continue ;; esac
        export "$_m4_k=$_m4_v"
    done < /usr/lib/m4/session-env
    # shellcheck source=/dev/null
    . /usr/lib/m4/kms-card.sh
    _m4_card=$(m4_kms_card)
    [ -z "$_m4_card" ] || export AQ_DRM_DEVICES="$_m4_card"
    # Firefox's media (RDD) sandbox has no rule for /dev/media* nor for the V4L2 request ioctls, and Firefox 157 has no pref to
    # allow them, so VA-API decoding on the AVD needs it off (prefs: /usr/lib/firefox/defaults/pref/m4-firefox.js). Only while
    # the decoder is loaded (kill switch: blacklist apple_avd).
    [ ! -d /sys/module/apple_avd ] || export MOZ_DISABLE_RDD_SANDBOX=1
    unset _m4_k _m4_v _m4_card
fi
