"""The receiver end to end, with stand-in shairport-sync and nqptp executables."""

import asyncio
import shutil
import stat
import sys
from pathlib import Path

import pytest
from fakes import Direct, Remote

from kalinka_plugin_airplay.receiver import Receiver, ReceiverOptions
from kalinka_plugin_airplay.segment import segment_factory
from kalinka_plugin_airplay.service import AirPlayService

FLAC = shutil.which("flac")
pytestmark = pytest.mark.skipif(FLAC is None, reason="the flac encoder is not installed")

TESTS = Path(__file__).parent

# Writes a session's metadata to the configured FIFO and a second of silence
# to stdout, then idles, or exits when told to by its environment-free argv.
SHAIRPORT = f"""#!{sys.executable}
import os, re, sys, time
sys.path.insert(0, {str(TESTS)!r})
from pipe_items import ssnc, track

if sys.argv[1:] == ["-V"]:
    print("5.5.2-AirPlay2-FFmpeg-OpenSSL-Avahi-stdout-metadata-sysconfdir:/etc")
    sys.exit(0)
config = open(sys.argv[2]).read()
with open(os.path.join(os.path.dirname(sys.argv[2]), "starts"), "a") as starts:
    starts.write("x")
pipe = re.search(r'pipe_name = "([^"]+)"', config).group(1)
fd = os.open(pipe, os.O_WRONLY)
os.write(fd, ssnc("pbeg") + ssnc("snam", b"Test iPhone") + track("Song", "Singer", "Record"))
for _ in range(10):
    sys.stdout.buffer.write(bytes(6 * 4410))
    sys.stdout.buffer.flush()
    time.sleep(0.02)
if "exit" in config:
    sys.exit(3)
time.sleep(60)
"""

NQPTP_BUSY = f"""#!{sys.executable}
import sys
print("nqptp cannot open the shared memory", file=sys.stderr)
sys.exit(1)
"""


def executable(path: Path, text: str) -> str:
    path.write_text(text)
    path.chmod(0o755)
    return str(path)


@pytest.fixture
def direct():
    return Direct()


def receiver(tmp_path, direct, *, name="Kalinka (test)", nqptp=NQPTP_BUSY):
    service = AirPlayService(
        direct,
        segment_factory(FLAC, 44100, tmp_path, 1 << 24),
        Remote(available=False),
        prebuffer_ms=300,
    )
    options = ReceiverOptions(
        executable=executable(tmp_path / "shairport-sync", SHAIRPORT),
        nqptp_executable=executable(tmp_path / "nqptp", nqptp),
        flac_executable=FLAC,
        name=name,
        port=7000,
        airplay2=True,
        output_rate=44100,
        latency_offset_s=-0.8,
    )
    state = tmp_path / "state"
    return Receiver(options, service, state, retry_delay_s=0.05), service, state


async def until(condition, timeout=10.0):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.02)


async def test_a_sender_plays_through_kalinka(tmp_path, direct):
    rx, service, state = receiver(tmp_path, direct)
    rx.start()
    try:
        await until(lambda: direct.holds and direct.hold.named("play"))
        [(_, source, track)] = direct.hold.named("play")
        assert source.source.asset_id == service.segment.id
        assert (track.title, track.performer.name) == ("Song", "Singer")
        assert service.status == "Playing from Test iPhone"
        assert rx.status == "Playing from Test iPhone"
        assert "AirPlay 2 is off" in rx.notice
        config = state / "shairport-sync.conf"
        assert 'service_type = "classic";' in config.read_text()
        assert stat.S_IMODE(config.stat().st_mode) == 0o600
        assert stat.S_ISFIFO((state / "metadata").stat().st_mode)
    finally:
        await rx.stop()
    assert direct.hold.named("release")


async def test_airplay_2_when_nqptp_runs(tmp_path, direct):
    nqptp = f"#!{sys.executable}\nimport time\ntime.sleep(60)\n"
    rx, _, state = receiver(tmp_path, direct, nqptp=nqptp)
    rx.start()
    try:
        await until(lambda: rx.running)
        assert rx.notice is None
        assert 'service_type = "airplay2";' in (state / "shairport-sync.conf").read_text()
    finally:
        await rx.stop()


async def test_a_receiver_that_stops_is_started_again(tmp_path, direct):
    rx, _, state = receiver(tmp_path, direct, name="exit")
    rx.start()
    try:
        await until(lambda: (state / "starts").exists() and len((state / "starts").read_text()) >= 2)
        await until(lambda: rx.error is not None or rx.running)
        assert len(direct.holds) >= 1
        assert direct.holds[0].named("release")
    finally:
        await rx.stop()


async def test_a_build_kalinka_cannot_use_is_an_error(tmp_path, direct):
    rx, _, _ = receiver(tmp_path, direct)
    executable(tmp_path / "shairport-sync", "#!/bin/sh\necho 4.3.7-OpenSSL-Avahi-ALSA\n")
    rx.start()
    try:
        await until(lambda: rx.error is not None)
        assert "without stdout and metadata support" in rx.error
        assert rx.status.endswith("; retrying")
    finally:
        await rx.stop()
