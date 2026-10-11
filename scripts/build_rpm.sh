#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
. "$root/scripts/package_common.sh"

if (( $# != 1 )); then
    echo "Usage: $0 <scripts/build-native.sh directory> (optionally set PYTHON to the build interpreter)" >&2
    exit 2
fi
for tool in "${PYTHON:-python3}" rpm rpmbuild strip; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Required build tool not found: $tool" >&2
        exit 1
    fi
done
use_native_build "$1"

# As with the deb, the build distro sets the glibc floor and the dist tag.
arch="$(rpm --eval '%{_arch}')"
work="$(mktemp -d "${TMPDIR:-/tmp}/kalinka-airplay-rpm.XXXXXXXX")"
trap 'rm -rf -- "$work"' EXIT
build_fresh_wheel "$work/wheels"

stage="$work/stage"
install -d -m 755 "$stage"
install -m 644 "$wheel" "$stage/$wheel_name"
install -m 755 "$shairport" "$nqptp" "$stage/"
strip --strip-unneeded --remove-section=.comment "$stage/shairport-sync" "$stage/nqptp"
install -m 644 "${sources[@]}" "$stage/"
install -m 644 "$root/debian/kalinka-airplay.conf" "$stage/airplay.conf"
install -m 644 "$root/LICENSE" "$root/THIRD_PARTY.md" "$root/README.md" "$stage/"

rpmbuild -bb --quiet \
    --define "_topdir $work/rpmbuild" \
    --define "plugin_version $package_version" \
    --define "stage $stage" \
    --define "wheel $wheel_name" \
    "$root/rpm/kalinka-plugin-airplay.spec"

shopt -s nullglob
rpms=("$work"/rpmbuild/RPMS/"$arch"/kalinka-plugin-airplay-*."$arch".rpm)
if (( ${#rpms[@]} != 1 )); then
    echo "rpmbuild did not produce exactly one $arch package." >&2
    exit 1
fi
install -m 644 "${rpms[0]}" "$root/"
printf 'Built %s\n' "$root/$(basename -- "${rpms[0]}")"
