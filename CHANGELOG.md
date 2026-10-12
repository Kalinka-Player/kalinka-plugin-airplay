# Changelog

Curated, user-facing notes per release. Add a `## <version>` section before tagging — the release workflow puts the matching section into the GitHub Release body.

## 0.1.0

First release.

### Added
- **Play from an iPhone, iPad or Mac over AirPlay.** Enable AirPlay in Kalinka's settings and the speaker appears in the sender's AirPlay menu. The audio plays through the renderer selected in Kalinka, which can be on another machine; the server never opens audio hardware itself.
- AirPlay 2 and classic AirPlay, with bundled shairport-sync 5.5.2 and nqptp 1.2.8. When UDP ports 319 and 320 are taken, or *Offer AirPlay 2* is off, Kalinka offers classic AirPlay and says so in its settings.
- Kalinka shows what the sender plays: title, artist, album, cover art, progress and volume. Pause, play, next and previous in the Kalinka app go back to a classic AirPlay sender; with an AirPlay 2 sender, pause and play stop and start Kalinka's own playback.
- Playing from Kalinka's queue takes the output back and pauses the sender. Lip sync with video is tunable with the *Renderer delay* setting.
- Packages for Debian 12 and 13, including Raspberry Pi OS and DietPi, and Ubuntu 24.04, on amd64 and arm64, and a one-command installer script.
- Needs Kalinka server 5.9 or newer, which brings plugin SDK 3.11. The package will not install on an older server.
