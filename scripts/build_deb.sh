#!/usr/bin/env bash
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
. "$root/scripts/package_common.sh"

if (( $# != 1 )); then
    echo "Usage: $0 <scripts/build-native.sh directory> (optionally set PYTHON to the build interpreter)" >&2
    exit 2
fi
for tool in "${PYTHON:-python3}" dpkg dpkg-deb dpkg-shlibdeps strip; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        echo "Required build tool not found: $tool" >&2
        exit 1
    fi
done
use_native_build "$1"

# The binaries are built on the machine that packages them, against its
# distribution's FFmpeg and other libraries, whose sonames differ between
# releases: the package is for this release alone, and says so in its version.
arch="$(dpkg --print-architecture)"
codename="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-}")"
if [[ -z "$codename" ]]; then
    echo "/etc/os-release names no VERSION_CODENAME to build for." >&2
    exit 1
fi
work="$(mktemp -d "${TMPDIR:-/tmp}/kalinka-airplay-deb.XXXXXXXX")"
trap 'rm -rf -- "$work"' EXIT
build_fresh_wheel "$work/wheels"
package_version="$package_version+$codename"
dpkg --validate-version "$package_version"

pkgroot="$work/pkgroot"
docdir="$pkgroot/usr/share/doc/kalinka-plugin-airplay"
libexec="$pkgroot/usr/libexec/kalinka-plugin-airplay"
sourcedir="$pkgroot/usr/share/kalinka-plugin-airplay/source"
dropin="$pkgroot/usr/lib/systemd/system/kalinka.service.d"
install -d -m 755 "$pkgroot/DEBIAN" "$pkgroot/opt/kalinka/wheels" "$docdir" \
    "$libexec" "$sourcedir" "$dropin"
install -m 644 "$wheel" "$pkgroot/opt/kalinka/wheels/$wheel_name"
install -m 755 "$shairport" "$nqptp" "$libexec/"
strip --strip-unneeded --remove-section=.comment "$libexec/shairport-sync" "$libexec/nqptp"
install -m 644 "${sources[@]}" "$sourcedir/"
install -m 644 "$root/debian/kalinka-airplay.conf" "$dropin/airplay.conf"
install -m 644 "$root/debian/copyright" "$root/debian/README.Debian" "$docdir/"

# dpkg-shlibdeps reads the package list from a debian/control beside it.
install -d "$work/debian"
printf 'Source: kalinka-plugin-airplay\n\nPackage: kalinka-plugin-airplay\nArchitecture: any\n' \
    > "$work/debian/control"
shlibs_depends="$(
    cd "$work"
    dpkg-shlibdeps -O "$libexec/shairport-sync" "$libexec/nqptp" \
        | sed -n 's/^shlibs:Depends=//p'
)"
if [[ -z "$shlibs_depends" ]]; then
    echo "dpkg-shlibdeps found no shared-library dependencies." >&2
    exit 1
fi

sed -e "s/@VERSION@/$package_version/g" \
    -e "s/@ARCH@/$arch/g" \
    -e "s/@SHLIBS_DEPENDS@/$shlibs_depends/g" \
    "$root/debian/control.in" > "$pkgroot/DEBIAN/control"
chmod 644 "$pkgroot/DEBIAN/control"
install -m 644 "$root/debian/triggers" "$pkgroot/DEBIAN/triggers"
install -m 755 "$root/debian/prerm" "$root/debian/postinst" "$root/debian/postrm" \
    "$pkgroot/DEBIAN/"
(
    cd "$pkgroot"
    find opt usr -type f -print0 | sort -z | xargs -0 md5sum > DEBIAN/md5sums
)
chmod 644 "$pkgroot/DEBIAN/md5sums"

# Explicit xz keeps the archive readable by older dpkg versions too.
package="kalinka-plugin-airplay_${package_version}_${arch}.deb"
dpkg-deb --root-owner-group -Zxz --build "$pkgroot" "$work/$package"
install -m 644 "$work/$package" "$root/$package"
printf 'Built %s\n' "$root/$package"
