import asyncio
import math
import os
import shutil
import struct
import subprocess

import pytest
from kalinka_server.live_content import serve
from starlette.requests import Request

from kalinka_plugin_airplay.child import ChildError
from kalinka_plugin_airplay.segment import check_encoder, flac_argv, segment_factory
from kalinka_plugin_airplay.shairport import FRAME_BYTES

FLAC = shutil.which("flac")
needs_flac = pytest.mark.skipif(FLAC is None, reason="the flac encoder is not installed")
RATE = 44100


def tone(seconds: float) -> bytes:
    """A stereo 24-bit packed sine, as shairport-sync's S24_3LE output."""
    frames = []
    for n in range(int(RATE * seconds)):
        sample = round(math.sin(2 * math.pi * 440 * n / RATE) * 0x3FFFFF)
        packed = struct.pack("<i", sample)[:3]
        frames.append(packed + packed)
    return b"".join(frames)


def decode(flac: bytes) -> bytes:
    return subprocess.run(
        [FLAC, "--silent", "--decode", "--force-raw-format", "--endian=little",
         "--sign=signed", "--stdout", "-"],
        input=flac, capture_output=True, check=True,
    ).stdout


async def read_all(capture) -> bytes:
    reader = await capture.open(0, None)
    data = b""
    while chunk := await reader.read(65536):
        data += chunk
    await reader.aclose()
    return data


def test_the_encoder_is_told_the_pinned_format():
    argv = flac_argv("/usr/bin/flac", 48000)
    assert {"--channels=2", "--bps=24", "--sample-rate=48000", "--stdout"} <= set(argv)


@needs_flac
async def test_pcm_becomes_a_live_flac_stream_of_unknown_length(tmp_path):
    pcm = tone(0.5)
    segment = segment_factory(FLAC, RATE, tmp_path, 1 << 24)()
    await segment.start()
    # Feeds that split frames, as reads of a pipe do.
    for start in range(0, len(pcm), 10007):
        await segment.feed(pcm[start : start + 10007])
    assert segment.fed_ms == pytest.approx(500, abs=1)
    reader = asyncio.create_task(read_all(segment.capture))
    await segment.end()
    flac = await reader

    assert flac[:4] == b"fLaC"
    streaminfo = flac[8:26]
    total_samples = int.from_bytes(streaminfo[13:18], "big") & 0xFFFFFFFFF
    assert total_samples == 0
    assert decode(flac) == pcm
    assert segment.capture.closed


@needs_flac
async def test_the_server_serves_a_segment_while_it_is_encoded(tmp_path):
    segment = segment_factory(FLAC, RATE, tmp_path, 1 << 24)()
    await segment.start()
    await segment.feed(tone(0.2))
    request = Request({"type": "http", "method": "GET", "headers": [(b"range", b"bytes=0-383999")]})
    response = await serve(segment.capture, "audio/flac", request)
    assert response.status_code == 200
    assert response.headers["x-kalinka-live"] == "1"
    assert (await anext(response.body_iterator))[:4] == b"fLaC"
    await response.reader.aclose()
    await segment.end()


@needs_flac
async def test_a_segment_outlasts_its_buffer_while_the_renderer_keeps_up(tmp_path):
    noise = os.urandom(int(RATE * 0.1) * FRAME_BYTES)
    segment = segment_factory(FLAC, RATE, tmp_path, 256 * 1024)()
    await segment.start()
    reader = asyncio.create_task(read_all(segment.capture))
    for _ in range(30):
        await segment.feed(noise)
        await asyncio.sleep(0.01)
    await segment.end()
    flac = await reader

    assert segment.failure is None
    assert segment.capture.available > 3 * 256 * 1024
    assert decode(flac) == noise * 30


@needs_flac
async def test_a_full_buffer_fails_the_segment(tmp_path):
    segment = segment_factory(FLAC, RATE, tmp_path, 4096)()
    await segment.start()
    for _ in range(20):
        await segment.feed(tone(0.1))
        if segment.failure:
            break
        await asyncio.sleep(0.05)
    assert "outgrew" in segment.failure
    await segment.end()


async def test_a_missing_encoder_is_reported(tmp_path):
    with pytest.raises(ChildError, match="could not start"):
        await check_encoder(str(tmp_path / "flac"))
