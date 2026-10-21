#!/bin/sh
# Build, sign and index the [m4] pacman repository for other M4 owners. It never uploads: it prints the upload command.
#
#   M4_REPO_KEY=<gpg key id> tools/repo-build.sh OUTDIR [PACKAGE...]
#
# Default packages: linux-m4 m4-tools m4-support m4-aurora-mesa quickshell m4-omarchy-qt612-compat.
# OUTDIR keeps the published state: new packages are added to the existing m4.db there, old files stay until you delete them.
# Refused: m4-firmware (Apple/Broadcom firmware and per-device calibration: never redistributable, each owner builds their own)
# and m4-mesa (retired; carries code that is not ours). Packages are built machine-neutral (M4_LOCAL_CONF=/dev/null), and any
# package that still contains a value from this machine's local.conf is refused.
set -eu
cd "$(dirname "$0")/.."
[ $# -ge 1 ] || { sed -n '2,10p' "$0"; exit 2; }
out=$(realpath -m "$1"); shift
key=${M4_REPO_KEY:?set M4_REPO_KEY to the repository signing key id}
repo=${M4_REPO_NAME:-m4}
[ $# -gt 0 ] || set -- linux-m4 m4-tools m4-support m4-aurora-mesa quickshell m4-omarchy-qt612-compat
mkdir -p "$out"

# The machine values of local.conf, in the forms a package could carry them (/home/<owner>, '<owner>', "<owner>", addresses).
patterns=$(sed -n 's/^M4_OWNER=\([^ #]*\).*/\/home\/\1\n'"'"'\1'"'"'\n"\1"/p; s/^M4_BT_[A-Z]*=\([^ #]*\).*/\1/p' local.conf 2>/dev/null | tr -d '\r' || true)

for p in "$@"; do
    case $p in
        m4-firmware|m4-mesa) echo "repo-build: $p is never published" >&2; exit 1 ;;
    esac
    [ -f "packages/$p/PKGBUILD" ] || { echo "repo-build: no packages/$p/PKGBUILD" >&2; exit 1; }
    ( cd "packages/$p" && M4_LOCAL_CONF=/dev/null makepkg -f --cleanbuild --sign --key "$key" )
    for f in $(cd "packages/$p" && makepkg --packagelist); do
        tmp=$(mktemp -d)
        bsdtar -xf "$f" -C "$tmp"
        if [ -n "$patterns" ] && printf '%s\n' "$patterns" | grep -rqF -f - "$tmp"; then
            rm -rf "$tmp"
            echo "repo-build: $(basename "$f") contains a local.conf value: build it machine-neutral first" >&2
            exit 1
        fi
        rm -rf "$tmp"
        cp "$f" "$f.sig" "$out/"
        repo-add --sign --key "$key" --verify "$out/$repo.db.tar.zst" "$out/$(basename "$f")"
    done
done

# GitHub release assets cannot be symlinks: replace repo-add's links by copies under the plain names pacman fetches.
for f in db db.sig files files.sig; do
    rm -f "$out/$repo.$f"
    cp "$out/$repo.${f%.sig}.tar.zst${f#${f%.sig}}" "$out/$repo.$f"
done
gpg --armor --export "$key" > "$out/$repo-repo.asc"

echo "repo-build: $out is ready. To publish (owner only):"
echo "  gh release upload repo-aarch64 --clobber -R jgoyvaerts/omarchy-m4 $out/*"
