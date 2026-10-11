# shellcheck shell=bash
# What the deb and the RPM build alike: the checked native build, a fresh
# wheel, and the package version derived from it. Sourced, after `root` is set.
. "$root/scripts/deb_version.sh"

# Sets shairport, nqptp and sources from a scripts/build-native.sh directory.
use_native_build() {
    local native
    native="$(realpath -- "$1")"
    shairport="$native/shairport-sync/shairport-sync"
    nqptp="$native/nqptp/nqptp"
    sources=("$native"/shairport-sync-*.tar.gz "$native"/nqptp-*.tar.gz)
    local file
    for file in "$shairport" "$nqptp" "${sources[@]}"; do
        if [[ ! -f "$file" ]]; then
            echo "Not a scripts/build-native.sh build: $file is missing." >&2
            exit 1
        fi
    done
    local version
    version="$("$shairport" -V)"
    if [[ "-$version-" != *-AirPlay2-* || "-$version-" != *-stdout-* ]]; then
        echo "$shairport is not the build this plugin ships: $version" >&2
        exit 1
    fi
}

# Sets wheel, wheel_name and package_version from a wheel built into $1.
# A stale dist/ wheel must never decide the version or the payload.
build_fresh_wheel() {
    "$root/scripts/build_wheel.sh" "$1"
    shopt -s nullglob
    local wheels=("$1"/kalinka_plugin_airplay-*-py3-none-any.whl)
    shopt -u nullglob
    if (( ${#wheels[@]} != 1 )); then
        echo "Expected exactly one freshly built AirPlay wheel." >&2
        exit 1
    fi
    wheel="${wheels[0]}"
    wheel_name="$(basename -- "$wheel")"
    local version="${wheel_name#kalinka_plugin_airplay-}"
    package_version="$(deb_version "${version%-py3-none-any.whl}")"
}
