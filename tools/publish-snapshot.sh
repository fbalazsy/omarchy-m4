#!/bin/sh
# Publish this repository's current state as one commit on the public repository (jgoyvaerts/omarchy-m4). Nothing of this
# repository's history goes out: the public repo is a series of snapshots.
#
#   tools/publish-snapshot.sh PUBLIC-CLONE KERNEL-COMMIT KERNEL-PKGREL [REF]
#
# PUBLIC-CLONE   a local clone of the public repository (its working tree is replaced)
# KERNEL-COMMIT  the commit of jgoyvaerts/linux branch m4/aurora that has the same tree as the linux-m4 release published with
#                this snapshot (the private branch carries other commit ids)
# KERNEL-PKGREL  that release's pkgrel
# REF            what to publish (default HEAD)
#
# It rewrites the source URLs to the public repositories, drops the retired m4-mesa, runs the privacy check over the whole
# tree and commits under the published ref's author. It never pushes: `git -C PUBLIC-CLONE push` is the owner's step.
set -eu
[ $# -ge 3 ] || { sed -n '2,15p' "$0"; exit 2; }
pub=$(realpath "$1"); kcommit=$2; kpkgrel=$3; ref=${4:-HEAD}
cd "$(dirname "$0")/.."
src=$(git rev-parse --short "$ref")
name=$(git log -1 --format=%an "$ref"); mail=$(git log -1 --format=%ae "$ref")   # the owner, as on the published ref
[ -d "$pub/.git" ] || { echo "publish-snapshot: $pub is not a git clone" >&2; exit 1; }
find "$pub" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
git archive "$ref" | tar -x -C "$pub"
cd "$pub"
rm -rf packages/m4-mesa
sed -i '/^| `m4-mesa` |/d' README.md
grep -rlI 'jgoyvaerts/omarchy-m4' . --exclude-dir=.git | xargs -r sed -i 's#jgoyvaerts/omarchy-m4#jgoyvaerts/omarchy-m4#g'
sed -i -e "s#^pkgrel=.*#pkgrel=$kpkgrel#" \
       -e "s#^url=.*#url='https://github.com/jgoyvaerts/linux/tree/m4/aurora'#" \
       -e "s#^_repo=.*#_repo=\${M4_KERNEL_REPO:-https://github.com/jgoyvaerts/linux.git}#" \
       -e "s#^_ref=.*#_ref=\${M4_KERNEL_REF:-commit=$kcommit}   \# jgoyvaerts/linux m4/aurora#" packages/linux-m4/PKGBUILD
sed -i "s#^url=.*#url='https://github.com/jgoyvaerts/linux'   \# the owner's rescue kernel (7.1.13-m4) is not published#" packages/linux-m4-rescue/PKGBUILD
sed -i 's#| \[linux-m4\](https://github.com/jgoyvaerts/linux-m4) |#| [jgoyvaerts/linux `m4/aurora`](https://github.com/jgoyvaerts/linux/tree/m4/aurora) |#' README.md
python3 tools/privacy-check.py .
git add -A
git -c user.name="$name" -c user.email="$mail" commit -q -m "Snapshot of the working repository at $src

linux-m4 pinned to jgoyvaerts/linux $kcommit (pkgrel $kpkgrel).

Signed-off-by: $name <$mail>
Assisted-by: LLM"
echo "publish-snapshot: committed in $pub; push it with: git -C $pub push"
