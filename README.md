# kalinka-plugin-airplay

Optional **server-side AirPlay receiver** for Kalinka. Disabled by default.
An iPhone, iPad or Mac picks Kalinka in its AirPlay menu and plays through the
renderer selected in Kalinka. The renderer can be on another machine and is
the only process that opens audio hardware.

```text
iPhone ── AirPlay 2 / classic ── shairport-sync -o stdout ── PCM, 24-bit ──▶ plugin ──▶ flac
                                  nqptp (AirPlay 2 timing)    metadata FIFO ──▶ plugin
                                                                                  │ live FLAC
                                                                                  ▼
                              Kalinka server /content/airplay/<segment> ◀── LiveCapture
                                                                                  │ HTTP, X-Kalinka-Live
                                                                                  ▼
                                                              selected Kalinka renderer
```

The renderer is not told about AirPlay. It pulls a live FLAC stream from the
server exactly as it pulls Spotify Connect's Ogg stream, so it needs nothing
beyond Kalinka's existing live-stream support.

- **Segments.** Each unbroken run of the sender's audio is one FLAC stream: a
  `flac` process per segment, buffered in an SDK `LiveCapture`. A pause, seek or
  takeover ends the segment and the next audio starts a new one, so nothing
  stale is ever heard. The buffer lets go of what the renderer has read, so a
  segment may run for hours: *Stream buffer limit* bounds only how far it gets
  ahead of the renderer.
- **Lip sync.** shairport-sync emits audio early by the plugin's buffer plus
  the renderer's delay (`audio_backend_latency_offset_in_seconds`), so video on
  the sender stays in sync. Tune *Renderer delay* if it does not.
- **Now playing.** The sender's title, artist, album, cover art, progress and
  volume come from shairport-sync's metadata pipe. A track change inside the
  stream updates what clients show through `DirectPlaybackSession.show()`,
  without restarting playback.
- **Control.** The last to press play wins. Play from Kalinka's queue takes the
  output back, and Kalinka pauses the sender. Pause, play, next and previous in
  the Kalinka app go to the sender over DACP, which only classic AirPlay offers.
  With an AirPlay 2 sender, pause and play stop and start Kalinka's own
  playback while the sender keeps sending, and next and previous do nothing.
  Seeking is done on the sender: the plugin acquires the output as not
  seekable, so clients offer no position control.

## Requirements

Linux, Python 3.11+, and Kalinka **SDK 3.11** with its matching server: the
server must implement `DirectPlaybackSession.show()` and the SDK must provide
`kalinka_plugin_sdk.live_capture`. The renderer needs Kalinka's live FLAC
support, which it has had since SDK 3.5.

AirPlay 2 needs UDP ports 319 and 320 to itself for nqptp. When another PTP
service holds them, or *Offer AirPlay 2* is off, Kalinka offers classic AirPlay
and says so in its settings. Only one AirPlay 2 receiver can run per host.

## Installation

On the Kalinka server (Debian 12 or 13, Raspberry Pi OS 64-bit, DietPi or
Ubuntu 24.04; amd64 or arm64), install or upgrade to the latest release with:

```sh
curl -fsSL https://raw.githubusercontent.com/Kalinka-Player/kalinka-plugin-airplay/main/scripts/install-latest.sh | sudo bash
```

The script picks the `.deb` for the machine's distribution release and
architecture, checks it against the release's `SHA256SUMS` and installs it
with apt; Kalinka restarts by itself. To install by hand, download
`SHA256SUMS` and the package whose name carries your release and architecture
from the [latest release](https://github.com/Kalinka-Player/kalinka-plugin-airplay/releases/latest),
such as `kalinka-plugin-airplay_<version>+trixie_arm64.deb` for a Raspberry Pi
on Raspberry Pi OS 13, then:

```sh
sha256sum -c SHA256SUMS --ignore-missing
sudo apt install ./kalinka-plugin-airplay_<version>+trixie_arm64.deb
```

The package bundles shairport-sync 5.5.2 and nqptp 1.2.8 with their source,
and a `kalinka.service` drop-in granting `CAP_NET_BIND_SERVICE` for nqptp.
Keep a distribution `shairport-sync` or `nqptp` service disabled. See
[`debian/README.Debian`](debian/README.Debian) for firewall ports.

### From source

```sh
sudo ./scripts/install-build-deps.sh   # apt or dnf
./scripts/build-native.sh
python -m pip install -e '.[dev]'
```

Point the *shairport-sync executable* and *nqptp executable* settings at
`build/native/shairport-sync/shairport-sync` and `build/native/nqptp/nqptp`,
and let nqptp bind its ports with
`sudo setcap cap_net_bind_service=+ep build/native/nqptp/nqptp`; without that,
Kalinka offers classic AirPlay. Packages are built from the same directory: `./scripts/build_deb.sh build/native` or
`./scripts/build_rpm.sh build/native`.

## Development

```sh
python -m pip install -e path/to/KalinkaPlayer/packages/kalinka-plugin-sdk \
    -e path/to/KalinkaPlayer/packages/kalinka-server -e '.[dev]'
python -m pytest
python -m ruff check src tests
```

The tests run without shairport-sync: the receiver tests use stand-in
executables and the real `flac` encoder, and skip when `flac` is not installed.

## License

GPL-3.0-or-later. The bundled programs keep their own licenses; see
[`THIRD_PARTY.md`](THIRD_PARTY.md).
