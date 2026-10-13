"""One unbroken run of the sender's audio, encoded to FLAC as the renderer pulls it."""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from kalinka_plugin_sdk.live_capture import LiveCapture, LiveCaptureError

from .child import Child, ChildError
from .shairport import CHANNELS, FRAME_BYTES, SAMPLE_BYTES

logger = logging.getLogger(__name__)

_READ_BYTES = 64 * 1024


@dataclass(frozen=True)
class SegmentFormat:
    """
    @brief What a segment's FLAC stream is: the sender's rate and depth.

    The receiver writes every stream as packed 24-bit PCM. A 16-bit stream
    sits in the top two bytes of each sample with a zero below, so encoding
    it at 16 bits drops nothing and tells the renderer the truth.
    """

    rate: int
    bits: int

    @property
    def sample_bytes(self) -> int:
        return self.bits // 8

    @property
    def frame_bytes(self) -> int:
        return CHANNELS * self.sample_bytes

    @property
    def describe(self) -> str:
        """24-bit 48 kHz, as Kalinka writes it."""
        return f"{self.bits}-bit {self.rate / 1000:g} kHz"


def flac_argv(executable: str, format: SegmentFormat) -> list[str]:
    """Raw PCM on stdin to a FLAC stream on stdout, its length left unknown."""
    return [
        executable,
        "--silent",
        "--force-raw-format",
        "--endian=little",
        "--sign=signed",
        f"--channels={CHANNELS}",
        f"--bps={format.bits}",
        f"--sample-rate={format.rate}",
        "--compression-level-0",
        "--no-seektable",
        "--no-padding",
        "--stdout",
        "-",
    ]


def narrow_to_16_bits(pcm: bytes) -> bytes:
    """Whole S24_3LE frames to S16_LE: the top two bytes of each sample."""
    narrowed = bytearray(len(pcm) // SAMPLE_BYTES * 2)
    narrowed[0::2] = pcm[1::SAMPLE_BYTES]
    narrowed[1::2] = pcm[2::SAMPLE_BYTES]
    return bytes(narrowed)


class Segment:
    """
    @brief Audio between two interruptions: one FLAC encoder, one LiveCapture.

    Every resume after a pause, seek, takeover or format change is a new
    segment, never the old one continued, so nothing stale is ever heard.
    The capture is what the server serves to the renderer, as
    `/content/airplay/<id>`.
    """

    def __init__(self, capture: LiveCapture, encoder: Child, format: SegmentFormat):
        self.capture = capture
        self.format = format
        self._encoder = encoder
        self._frame_ms = 1000 / format.rate
        self._pending = b""
        self._pump: Optional[asyncio.Task] = None
        self.fed_ms = 0.0
        self.failure: Optional[str] = None
        # A 16-bit segment whose low bytes were not all zero: bits were lost.
        self.lost_bits = False

    @property
    def id(self) -> str:
        return self.capture.id

    async def start(self) -> None:
        await self._encoder.start()
        self._pump = asyncio.create_task(self._pump_encoded())

    async def feed(self, pcm: bytes) -> None:
        """Encode the receiver's S24_3LE; a frame split across calls is kept until whole."""
        data = self._pending + pcm
        whole = len(data) - len(data) % FRAME_BYTES
        self._pending = data[whole:]
        if not whole or self.failure:
            return
        frames = data[:whole]
        if self.format.bits == 16:
            low = frames[0::SAMPLE_BYTES]
            if not self.lost_bits and low.count(0) != len(low):
                self.lost_bits = True
                logger.warning("AirPlay: a 16-bit stream carried more than 16 bits")
            frames = narrow_to_16_bits(frames)
        try:
            self._encoder.stdin.write(frames)
            await self._encoder.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            self.failure = self._encoder.exit_reason()
            return
        self.fed_ms += whole // FRAME_BYTES * self._frame_ms

    async def _pump_encoded(self) -> None:
        try:
            while chunk := await self._encoder.stdout.read(_READ_BYTES):
                await self.capture.append(chunk)
            await self.capture.finish()
        except LiveCaptureError as error:
            self.failure = str(error)
            await self._encoder.stop()

    async def end(self) -> None:
        """Stop taking audio. The renderer keeps what was captured until it hangs up."""
        if self._encoder.returncode is None:
            self._encoder.stdin.close()
            try:
                await asyncio.wait_for(asyncio.shield(self._encoder.wait()), 2)
            except TimeoutError:
                await self._encoder.stop()
        if self._pump:
            await self._pump
        await self.capture.retire(wait_for_disconnect=True)


SegmentFactory = Callable[[SegmentFormat], Segment]


def segment_factory(flac_executable: str, directory: Path, max_bytes: int) -> SegmentFactory:
    def create(format: SegmentFormat) -> Segment:
        return Segment(
            LiveCapture(directory, max_bytes=max_bytes, keep_delivered=False),
            Child("flac", flac_argv(flac_executable, format), stdin=True, stdout=True),
            format,
        )

    return create


async def check_encoder(flac_executable: str) -> None:
    """@throw ChildError when the FLAC encoder cannot run."""
    probe = Child("flac", [flac_executable, "--version"])
    await probe.start()
    if await probe.wait() != 0:
        raise ChildError(f"the FLAC encoder at {flac_executable} does not run")
