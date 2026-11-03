# kalinka-plugin-airplay

Optional **server-side AirPlay receiver** for Kalinka. Disabled by default.
An iPhone, iPad or Mac picks Kalinka in its AirPlay menu and plays through the
renderer selected in Kalinka. The renderer can be on another machine and is
the only process that opens audio hardware.

```text
iPhone → shairport-sync → packed PCM → plugin → Core live source
              ↓ metadata                          ↓ continuous HTTP
           title/art/progress                selected Kalinka renderer
```

The plugin forwards the receiver's PCM unchanged. Core owns a bounded memory
ring and `/live-audio/<route>` responses. There is no FLAC encoder, stdbuf child,
disk audio cache, or plugin-side prebuffer, for either local or remote outputs.

- **Output switching.** The receiver, PCM source and input hold survive a move
  to another compatible renderer. Core prepares the output before selecting
  fresh audio. Slow readers recover at the live edge instead of accumulating
  seconds of old audio. Acquisition/retry never blocks receiver PCM draining.
- **Format.** shairport-sync's output declaration supplies the actual rate and
  packed 24-bit stereo layout. All six bytes of each frame are preserved, even
  for a 16-bit sender. Input status retains the original codec/depth; renderer
  media is PCM. A pause, seek or format change ends the interrupted source;
  subsequent audio starts a new one. Metadata-only track changes keep playing.
- **Timing.** Core/renderer use a shared live policy (60 ms target, 250 ms maximum
  lag by default). These are downstream budgets, not measured end-to-end delay.
  shairport emits early by the target plus *Additional output delay*, which is
  for downstream DSP/device delay. Measure lip sync for the selected output.
  The renderer uses bounded live queues and slow clock-drift correction;
  normal file playback keeps its existing buffering.
- **Now playing and control.** Title, artist, album, artwork, progress and volume
  still come from the receiver's metadata pipe. The app shows AirPlay as the
  active input independently of the chosen output. Queue takeover pauses the
  sender. Classic AirPlay supports DACP play/pause/skip; AirPlay 2 supports
  Kalinka-side pause/resume while the sender may keep sending. Seeking remains
  on the sender. Artwork remains under `/content/airplay/art-*`.

## Requirements and migration

Linux, Python 3.11+, SDK **3.22+**, and a matching Core and renderer implementing
live PCM v1. Upgrade remote renderers too. A legacy renderer reports an upgrade
requirement; there is no FLAC fallback. This migration must ship alongside the
Core/renderer change, before installing the updated plugin.

Legacy `flac_executable`, `cache_limit_mib` and `prebuffer_ms` settings are ignored
and no longer shown. The live target replaces the old 500 ms startup prebuffer.
`renderer_delay_ms` remains as an additional manual device/DSP delay, with a
new default of zero. Review any saved override (including a previous 300 ms
estimate) and retune it for this path rather than counting the old pipeline.
The shairport-sync build retains its FFmpeg decoding dependencies; no transport
encoder is needed. For networks unable to sustain PCM, use wired Ethernet.

AirPlay 2 needs UDP ports 319 and 320 to itself for nqptp. When another PTP
service holds them, or *Offer AirPlay 2* is off, Kalinka offers classic AirPlay
and says so in its settings. Only one AirPlay 2 receiver can run per host.

## Installation

On the Kalinka server (Debian 13, or Raspberry Pi OS 13 or DietPi based on
it; amd64 or arm64), install or upgrade to the latest release with:

```sh
curl -fsSL https://raw.githubusercontent.com/Kalinka-Player/kalinka-plugin-airplay/main/scripts/install-latest.sh | sudo bash
```

The script picks the `.deb` for the machine's architecture, checks it against
the release's `SHA256SUMS` and installs it with apt; Kalinka restarts by
itself. To install by hand, download `SHA256SUMS` and the package for your
architecture from the [latest release](https://github.com/Kalinka-Player/kalinka-plugin-airplay/releases/latest),
such as `kalinka-plugin-airplay_<version>_arm64.deb` for a Raspberry Pi on
Raspberry Pi OS 13, then:

```sh
sha256sum -c SHA256SUMS --ignore-missing
sudo apt install ./kalinka-plugin-airplay_<version>_arm64.deb
```

The package bundles shairport-sync 5.5.2 and nqptp 1.2.8, built against
Debian 13's libraries, with their source, and a `kalinka.service` drop-in
granting `CAP_NET_BIND_SERVICE` for nqptp.
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
executables and PCM source doubles. Frame-preservation tests include arbitrary
pipe boundaries and nonzero low sample bits. Physical phone/Pi latency and
CPU measurements remain hardware acceptance work.

## License

GPL-3.0-or-later. The bundled programs keep their own licenses; see
[`THIRD_PARTY.md`](THIRD_PARTY.md).


## Live receiver controls

With Core API 0.22 / SDK 3.22 and the companion app, **Connect a device** on Home
or in the server menu shows this receiver independently of catalogs and the
chosen output. Enable/disable takes effect without a Core restart, including
first enable after boot. Disabling ends the current AirPlay session and stays
off across a restart.

Leave the AirPlay name empty to follow the streamer name, or set an explicit
override. Renaming restarts only the owned receiver and ends any AirPlay session.
The panel shows sender sessions but offers no Bluetooth-style pairing, grants
or unpairing. Availability on the network is not a per-sender permission list.
A running shairport process reports readiness; mDNS visibility stays unknown
until independently confirmed. No temporary “stop receiving” switch is exposed.
