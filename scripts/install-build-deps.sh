#!/usr/bin/env bash
# Install what scripts/build-native.sh and the package builds need, on a
# Debian-based system or Fedora. Run as root.
set -euo pipefail

if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update
    apt-get install -y --no-install-recommends \
        build-essential git ca-certificates autoconf automake libtool pkg-config \
        libpopt-dev libconfig-dev libavahi-client-dev libssl-dev libsoxr-dev \
        libplist-dev libplist-utils libsodium-dev uuid-dev libgcrypt-dev xxd \
        libavutil-dev libavcodec-dev libavformat-dev libswresample-dev \
        dpkg-dev binutils flac coreutils python3 python3-venv
elif command -v dnf >/dev/null 2>&1; then
    dnf install -y \
        gcc gcc-c++ make autoconf automake libtool git pkgconf-pkg-config \
        popt-devel libconfig-devel avahi-devel openssl-devel soxr-devel \
        libplist-devel libsodium-devel libgcrypt-devel libuuid-devel \
        ffmpeg-free-devel /usr/bin/xxd /usr/bin/plistutil \
        rpm-build binutils flac /usr/bin/stdbuf python3 tar
else
    echo "Neither apt-get nor dnf found." >&2
    exit 1
fi
