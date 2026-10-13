# Changelog

Curated, user-facing notes per release. Add a `## <version>` section before tagging — the release workflow puts the matching section into the GitHub Release body.

## 0.2.0

### Changed
- **Kalinka shows the sender's real format.** Each stream is now encoded at the sender's own sample rate and bit depth: 16-bit 44.1 kHz for classic AirPlay and AirPlay 2 realtime streams, 24-bit 48 kHz for Apple Music Lossless over AirPlay 2. 0.1.x forced everything to 24-bit 44.1 kHz, which padded 16-bit streams and resampled 48 kHz ones. The *Sample rate* setting is gone; a saved value is ignored.
- The receiver keeps the audio in sync by dropping or repeating a single frame when it must, instead of resampling a whole packet, so what reaches the renderer is the sender's samples.
- The receiver status names what plays, as in "AirPlay 2, ALAC 24-bit 48 kHz".

## 0.1.1

### Fixed
- **Kalinka's plugin catalog now offers the plugin.** The 0.1.0 packages carried the distribution release in their Debian version, as in `0.1.0+trixie`, which the catalog rejects as not matching the release. Packages are now versioned like Kalinka's other plugins, `0.1.1`, one per architecture.

### Changed
- Packages are built for Debian 13 only, which covers Raspberry Pi OS 13 and DietPi based on it; there are no Debian 12 or Ubuntu 24.04 packages any more. The bundled shairport-sync links Debian 13's FFmpeg and other libraries, which apt checks on install.

## 0.1.0

First release.

### Added
- **Play from an iPhone, iPad or Mac over AirPlay.** Enable AirPlay in Kalinka's settings and the speaker appears in the sender's AirPlay menu. The audio plays through the renderer selected in Kalinka, which can be on another machine; the server never opens audio hardware itself.
- AirPlay 2 and classic AirPlay, with bundled shairport-sync 5.5.2 and nqptp 1.2.8. When UDP ports 319 and 320 are taken, or *Offer AirPlay 2* is off, Kalinka offers classic AirPlay and says so in its settings.
- Kalinka shows what the sender plays: title, artist, album, cover art, progress and volume. Pause, play, next and previous in the Kalinka app go back to a classic AirPlay sender; with an AirPlay 2 sender, pause and play stop and start Kalinka's own playback.
- Playing from Kalinka's queue takes the output back and pauses the sender. Lip sync with video is tunable with the *Renderer delay* setting.
- Packages for Debian 12 and 13, including Raspberry Pi OS and DietPi, and Ubuntu 24.04, on amd64 and arm64, and a one-command installer script.
- Needs Kalinka server 5.9 or newer, which brings plugin SDK 3.11. The package will not install on an older server.
