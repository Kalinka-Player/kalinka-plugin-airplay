"""shairport-sync's metadata pipe: its items in, what the sender did out."""

import base64
import binascii
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Cover art arrives base64-encoded in a single item.
MAX_ITEM_BYTES = 16 * 1024 * 1024
RTP_WRAP = 1 << 32
# The sender's progress counts RTP frames at 44100 a second, whatever plays.
PROGRESS_RATE = 44100

_START, _END = b"<item>", b"</item>"
_ITEM = re.compile(
    rb"<item><type>([0-9a-f]{1,8})</type><code>([0-9a-f]{1,8})</code>"
    rb"<length>(\d+)</length>"
    rb'(?:\s*<data encoding="base64">\s*([A-Za-z0-9+/=\s]*)</data>)?'
    rb"\s*</item>"
)


@dataclass(frozen=True)
class Item:
    """One item: a four-character type ("core" or "ssnc"), a code and its payload."""

    kind: str
    code: str
    data: bytes = b""


class ItemParser:
    """
    @brief Splits the pipe's byte stream into items, wherever its reads break.

    A malformed or oversized item is skipped up to the next one, so a write
    shairport-sync abandoned halfway cannot wedge the stream.
    """

    def __init__(self, max_item_bytes: int = MAX_ITEM_BYTES):
        self._buffer = bytearray()
        self._max_item_bytes = max_item_bytes

    def feed(self, chunk: bytes) -> list[Item]:
        self._buffer += chunk
        items = []
        while True:
            start = self._buffer.find(_START)
            if start < 0:
                del self._buffer[: max(0, len(self._buffer) - len(_START) + 1)]
                return items
            del self._buffer[:start]
            following = self._buffer.find(_START, len(_START))
            end = self._buffer.find(_END)
            if 0 < following < end or (end < 0 and following > 0):
                del self._buffer[:following]
                continue
            if end < 0:
                if len(self._buffer) > self._max_item_bytes:
                    del self._buffer[: len(_START)]
                    continue
                return items
            raw = bytes(self._buffer[: end + len(_END)])
            del self._buffer[: end + len(_END)]
            item = _parse(raw)
            if item is not None:
                items.append(item)


def _parse(raw: bytes) -> Optional[Item]:
    match = _ITEM.fullmatch(raw)
    if match is None:
        return None
    kind, code, length, payload = match.groups()
    try:
        data = base64.b64decode(re.sub(rb"\s", b"", payload)) if payload else b""
    except binascii.Error:
        return None
    if len(data) != int(length):
        return None
    return Item(_fourcc(kind), _fourcc(code), data)


def _fourcc(hex_digits: bytes) -> str:
    return int(hex_digits, 16).to_bytes(4, "big").decode("latin-1")


@dataclass(frozen=True)
class PlayBegan:
    """A sender started a play session."""


@dataclass(frozen=True)
class PlayEnded:
    """The play session ended: the sender stopped or went away."""


@dataclass(frozen=True)
class Interrupted:
    """The sender paused or seeked: audio already sent must not be heard."""


@dataclass(frozen=True)
class Resumed:
    """The sender resumed a paused stream."""


@dataclass(frozen=True)
class TrackChanged:
    title: str
    artist: str
    album: str


@dataclass(frozen=True)
class Artwork:
    image: bytes = field(repr=False)


@dataclass(frozen=True)
class Progress:
    position_ms: int
    duration_ms: int


@dataclass(frozen=True)
class VolumeChanged:
    """The sender's volume: 0 down to -30, or None for mute."""

    airplay_volume: Optional[float]


@dataclass(frozen=True)
class RemoteChanged:
    """
    @brief Where the sender takes remote control commands, as far as known.

    @note `active_remote` authorises control of the sender: never log it.
    """

    client_ip: Optional[str]
    port: Optional[int]
    active_remote: Optional[str] = field(repr=False)


@dataclass(frozen=True)
class SenderNamed:
    name: str


@dataclass(frozen=True)
class StreamType:
    """How the sender streams: "Classic", "Realtime" or "Buffered"."""

    kind: str


@dataclass(frozen=True)
class StreamFormat:
    """
    @brief What the receiver decodes, as it names it: ALAC/48000/S24_LE/2.

    Sent at the first frame of a session and whenever the encoding switches.

    @param bits The decoded sample depth: 16 for S16, 24 for S24 and for AAC,
        which decodes to floats the receiver writes at 24 bits.
    """

    codec: str
    rate: int
    bits: int
    channels: int


@dataclass(frozen=True)
class OutputFormat:
    """The PCM the receiver writes, as it names it: 44100/S24_3LE/2. Sent at
    the first frame of a session only."""

    rate: int
    sample_format: str
    channels: int


Event = (
    PlayBegan
    | PlayEnded
    | Interrupted
    | Resumed
    | TrackChanged
    | Artwork
    | Progress
    | VolumeChanged
    | RemoteChanged
    | SenderNamed
    | StreamType
    | StreamFormat
    | OutputFormat
)

_SIMPLE = {
    "pbeg": PlayBegan,
    "pend": PlayEnded,
    "pfls": Interrupted,
    "paus": Interrupted,
    "pres": Resumed,
    "prsm": Resumed,
}
_TRACK_FIELDS = {"minm": "title", "asar": "artist", "asal": "album"}


class EventDecoder:
    """
    @brief Turns items into events, keeping what spans several items.

    A track's fields arrive one item each between `mdst` and `mden`; the
    remote-control address arrives in three items over the session.
    """

    def __init__(self):
        self._track = {}
        self._client_ip = self._port = self._active_remote = None

    def decode(self, item: Item) -> Optional[Event]:
        if item.kind == "core":
            if item.code in _TRACK_FIELDS:
                self._track[_TRACK_FIELDS[item.code]] = _text(item.data)
            return None
        if item.kind != "ssnc":
            return None
        if item.code in _SIMPLE:
            return _SIMPLE[item.code]()
        if item.code == "mdst":
            self._track = {}
        elif item.code == "mden" and self._track:
            return TrackChanged(
                title=self._track.get("title", ""),
                artist=self._track.get("artist", ""),
                album=self._track.get("album", ""),
            )
        elif item.code == "PICT" and item.data:
            return Artwork(item.data)
        elif item.code == "prgr":
            return self._progress(_text(item.data))
        elif item.code == "pvol":
            return _volume(_text(item.data))
        elif item.code == "snam":
            return SenderNamed(_text(item.data))
        elif item.code == "styp":
            return StreamType(_text(item.data))
        elif item.code == "sdsc":
            return _stream_format(_text(item.data))
        elif item.code == "odsc":
            return _output_format(_text(item.data))
        elif item.code in ("clip", "dapo", "acre"):
            return self._remote(item.code, _text(item.data))
        return None

    def _progress(self, text: str) -> Optional[Progress]:
        try:
            start, current, end = (int(part) for part in text.split("/"))
        except ValueError:
            return None
        to_ms = 1000 / PROGRESS_RATE
        return Progress(
            position_ms=round(((current - start) % RTP_WRAP) * to_ms),
            duration_ms=round(((end - start) % RTP_WRAP) * to_ms),
        )

    def _remote(self, code: str, text: str) -> RemoteChanged:
        if code == "clip":
            self._client_ip = text or None
        elif code == "acre":
            self._active_remote = text or None
        else:
            self._port = int(text) if text.isdigit() and int(text) > 0 else None
        return RemoteChanged(self._client_ip, self._port, self._active_remote)


def _text(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").strip("\0").strip()


def _stream_format(text: str) -> Optional[StreamFormat]:
    """ALAC/44100/S16_LE/2 or AAC/48000/F24/5.1; "None (0)" between streams."""
    parts = text.split("/")
    if len(parts) != 4 or not parts[1].isdigit():
        return None
    codec, rate, sample, channels = parts
    depth = re.search(r"\d+", sample)
    if depth is None or _channels(channels) == 0:
        return None
    return StreamFormat(codec, int(rate), min(int(depth.group()), 24), _channels(channels))


def _output_format(text: str) -> Optional[OutputFormat]:
    """44100/S24_3LE/2."""
    parts = text.split("/")
    if len(parts) != 3 or not parts[0].isdigit() or _channels(parts[2]) == 0:
        return None
    return OutputFormat(int(parts[0]), parts[1], _channels(parts[2]))


def _channels(text: str) -> int:
    """2, or 5.1 and 7.1 as their channel counts; 0 when it is no count."""
    parts = text.split(".")
    if not parts or not all(part.isdigit() for part in parts):
        return 0
    return sum(int(part) for part in parts)


def _volume(text: str) -> Optional[VolumeChanged]:
    try:
        airplay_volume = float(text.split(",")[0])
    except ValueError:
        return None
    return VolumeChanged(None if airplay_volume <= -144 else airplay_volume)
