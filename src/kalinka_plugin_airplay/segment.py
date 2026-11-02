"""Frame alignment for one uninterrupted run of receiver PCM.

The Core owns buffering, HTTP routing and renderer selection. No encoder,
filesystem capture, narrowing, or per-renderer processing belongs here.
"""

from dataclasses import dataclass
from typing import Callable

from kalinka_plugin_sdk.live_audio import LiveAudio, LiveSource, PcmFormat

from .shairport import CHANNELS, FRAME_BYTES


@dataclass(frozen=True)
class SegmentFormat:
    rate: int
    bits: int  # Sender depth for display; shairport always outputs packed 24-bit.

    @property
    def describe(self) -> str:
        return f"{self.bits}-bit {self.rate / 1000:g} kHz"


class Segment:
    def __init__(self, source: LiveSource, format: SegmentFormat):
        self.source = source
        self.format = format
        self._pending = b""

    @property
    def id(self) -> str:
        return self.source.reference.live_source_id

    def feed(self, pcm: bytes) -> None:
        # Both methods are immediate: the producer never waits for a renderer.
        data = self._pending + pcm
        whole = len(data) - len(data) % FRAME_BYTES
        self._pending = data[whole:]
        if whole:
            self.source.offer(data[:whole])

    def close(self) -> None:
        self._pending = b""
        self.source.close()


SegmentFactory = Callable[[SegmentFormat], Segment]


def segment_factory(live_audio: LiveAudio) -> SegmentFactory:
    def create(format: SegmentFormat) -> Segment:
        source = live_audio.create_pcm(PcmFormat(format.rate, CHANNELS, "S24_3LE"))
        return Segment(source, format)

    return create
