# Bundled Shairport fixes

`shairport-classic-password.patch` applies to the pinned 5.5.2 source. In its
AirPlay 2 build, a connection initially has type AP2, including when advertising
classic service. The first request (including OPTIONS or ANNOUNCE) used to set
the classic `authorized` flag without checking `general.password`. ANNOUNCE only
selects classic mode inside its handler, after that authorization decision.

Recognize ANNOUNCE before dispatch and retain authorization only after a classic
password check. AP2 pairing/authentication remains in Shairport's AP2 handlers.
The build archives the patched corresponding source and packaging rejects old
native builds. Run `scripts/check-native-password.py` against the rebuilt binary
with Avahi/D-Bus available in an isolated network namespace/container.

This checks classic authentication in the shipped AP2 build. Apple-device AP2
password pairing still requires hardware verification.
