"""One unbroken run of the sender's audio, encoded to FLAC as the renderer pulls it."""

import asyncio
import logging
from pathlib import Path
from typing import Callable, Optional

from kalinka_plugin_sdk.live_capture import LiveCapture, LiveCaptureError

from .child import Child, ChildError
from .shairport import CHANNELS, FRAME_BYTES, SAMPLE_BYTES

logger = logging.getLogger(__name__)

_READ_BYTES = 64 * 1024


def flac_argv(executable: str, rate: int) -> list[str]:
    """Raw PCM on stdin to a FLAC stream on stdout, its length left unknown."""
    return [
        executable,
        "--silent",
        "--force-raw-format",
        "--endian=little",
        "--sign=signed",
        f"--channels={CHANNELS}",
        f"--bps={SAMPLE_BYTES * 8}",
        f"--sample-rate={rate}",
        "--compression-level-0",
        "--no-seektable",
        "--no-padding",
        "--stdout",
        "-",
    ]


class Segment:
    """
    @brief Audio between two interruptions: one FLAC encoder, one LiveCapture.

    Every resume after a pause, seek or takeover is a new segment, never
    the old one continued, so nothing stale is ever heard. The capture is
    what the server serves to the renderer, as `/content/airplay/<id>`.
    """

    def __init__(self, capture: LiveCapture, encoder: Child, rate: int):
        self.capture = capture
        self._encoder = encoder
        self._frame_ms = 1000 / rate
        self._pending = b""
        self._pump: Optional[asyncio.Task] = None
        self.fed_ms = 0.0
        self.failure: Optional[str] = None

    @property
    def id(self) -> str:
        return self.capture.id

    async def start(self) -> None:
        await self._encoder.start()
        self._pump = asyncio.create_task(self._pump_encoded())

    async def feed(self, pcm: bytes) -> None:
        """Encode PCM; a frame split across calls is kept until it is whole."""
        data = self._pending + pcm
        whole = len(data) - len(data) % FRAME_BYTES
        self._pending = data[whole:]
        if not whole or self.failure:
            return
        try:
            self._encoder.stdin.write(data[:whole])
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


SegmentFactory = Callable[[], Segment]


def segment_factory(
    flac_executable: str, rate: int, directory: Path, max_bytes: int
) -> SegmentFactory:
    def create() -> Segment:
        return Segment(
            LiveCapture(directory, max_bytes=max_bytes, keep_delivered=False),
            Child("flac", flac_argv(flac_executable, rate), stdin=True, stdout=True),
            rate,
        )

    return create


async def check_encoder(flac_executable: str) -> None:
    """@throw ChildError when the FLAC encoder cannot run."""
    probe = Child("flac", [flac_executable, "--version"])
    await probe.start()
    if await probe.wait() != 0:
        raise ChildError(f"the FLAC encoder at {flac_executable} does not run")
