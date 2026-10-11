#!/usr/bin/env bash
# Build the pinned shairport-sync and nqptp the plugin ships.
#
# shairport-sync gets AirPlay 2, stdout output and the metadata pipe, and no
# audio backend of its own: the renderer is the only process that opens the
# DAC. Convolution stays out, so the binary carries no GPL-3.0 code beside
# nqptp's GPL-2.0-only header.
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
work="${1:-$root/build/native}"

shairport_tag=5.5.2
shairport_commit=7bad231c18368dbd26f298577f6210e36e4b0797
nqptp_tag=1.2.8
nqptp_commit=c925f27c1fd12e4033ac477e5a405969b0b0260b

if [[ -e "$work" ]]; then
    echo "Build directory already exists: $work; choose a new directory." >&2
    exit 1
fi
mkdir -p "$work"

fetch() {
    local name="$1" url="$2" tag="$3" commit="$4"
    git -c advice.detachedHead=false clone -q --depth 1 --branch "$tag" "$url" "$work/$name"
    local actual
    actual="$(git -C "$work/$name" rev-parse HEAD)"
    if [[ "$actual" != "$commit" ]]; then
        echo "$name $tag is $actual, not the pinned $commit." >&2
        exit 1
    fi
    # The release's corresponding source, as shipped.
    git -C "$work/$name" archive --format=tar.gz --prefix="$name-$tag/" \
        -o "$work/$name-$tag.tar.gz" HEAD
}

fetch shairport-sync https://github.com/mikebrady/shairport-sync.git "$shairport_tag" "$shairport_commit"
fetch nqptp https://github.com/mikebrady/nqptp.git "$nqptp_tag" "$nqptp_commit"

jobs="$(nproc)"
(
    cd "$work/nqptp"
    autoreconf -fi
    ./configure
    make -j"$jobs"
)
(
    cd "$work/shairport-sync"
    autoreconf -fi
    ./configure --sysconfdir=/etc --with-airplay-2 --with-ssl=openssl \
        --with-avahi --with-stdout --with-metadata --with-soxr
    make -j"$jobs"
)

version="$("$work/shairport-sync/shairport-sync" -V)"
for feature in AirPlay2 Avahi stdout metadata; do
    if [[ "-$version-" != *"-$feature-"* ]]; then
        echo "shairport-sync was built without $feature: $version" >&2
        exit 1
    fi
done
printf 'Built %s/shairport-sync/shairport-sync (%s)\n' "$work" "$version"
printf 'Built %s/nqptp/nqptp\n' "$work"
