# Third-party components

The plugin's Python code is GPL-3.0-or-later. Its packages bundle two native
programs, built unmodified from pinned upstream commits by
[`scripts/build-native.sh`](scripts/build-native.sh). The plugin starts them as
separate processes and talks to them over pipes; none of their code is linked
into Kalinka.

| Component | Version and commit | License |
|---|---|---|
| [shairport-sync](https://github.com/mikebrady/shairport-sync) | 5.5.2, `7bad231c18368dbd26f298577f6210e36e4b0797` | MIT (Shairport Sync, pair_ap), BSD-2-Clause (tinyhttp), GPL-2.0-only (`nqptp-shm-structures.h`, compiled in for AirPlay 2) |
| [nqptp](https://github.com/mikebrady/nqptp) | 1.2.8, `c925f27c1fd12e4033ac477e5a405969b0b0260b` | GPL-2.0-only |

Both packages ship the complete source of these two builds, as the archives
`scripts/build-native.sh` makes from the pinned commits, in
`/usr/share/kalinka-plugin-airplay/source`. Each release attaches them too.

shairport-sync is built without convolution, so it carries no GPL-3.0 code
beside nqptp's GPL-2.0-only header. It links the distribution's FFmpeg,
OpenSSL, libplist, libsodium, libgcrypt, libsoxr, libconfig, popt, libuuid and
avahi-client, which the packages depend on rather than bundle; each is under
its own terms, in `/usr/share/doc/<package>/copyright`.

The FLAC encoder is the distribution's `flac` program, run per stream.
