"""The AirPlay sender drives; Kalinka's renderer plays what it sends."""

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from typing import Awaitable, Callable, Optional

from kalinka_plugin_sdk.datamodel import (
    Album,
    Artist,
    CoverImage,
    EntityId,
    EntityType,
    PlaybackState,
    PlayerStateEnum,
    Track,
)
from kalinka_plugin_sdk.direct_playback import (
    DirectPlayback,
    DirectPlaybackSession,
    HoldEnded,
    OutputUnavailable,
    RevokeReason,
    TransportKind,
    TransportRequest,
)
from kalinka_plugin_sdk.inputmodule import ModuleAsset, TrackSource

from .metadata import (
    PROGRESS_RATE,
    Artwork,
    Event,
    Interrupted,
    OutputFormat,
    PlayBegan,
    PlayEnded,
    Progress,
    RemoteChanged,
    Resumed,
    SenderNamed,
    StreamFormat,
    StreamType,
    TrackChanged,
    VolumeChanged,
    rtp_distance,
)
from .remote import NEXT, PAUSE, PLAY, PREVIOUS, SenderRemote
from .segment import Segment, SegmentFactory, SegmentFormat
from .shairport import CHANNELS, OUTPUT_FORMAT

logger = logging.getLogger(__name__)

MODULE = "airplay"
TITLE = "AirPlay"
AIRPLAY_MIN_VOLUME = -30.0
# Someone else chose to play: the sender must wait until it is played again.
_TAKEN_OVER = {RevokeReason.QUEUE_PLAY, RevokeReason.TAKEN_BACK, RevokeReason.OTHER_HOLDER}
_STREAM_KINDS = {
    "Classic": "classic AirPlay",
    "Realtime": "AirPlay 2 realtime",
    "Buffered": "AirPlay 2 buffered",
}
# A flush this close to the track's end, on the sender's 44.1 kHz progress
# clock, is the sender leaving the track rather than seeking within it.
TRACK_END_SLACK = 44100


class ArtworkStore:
    """The sender's last few cover images, served as `/content/airplay/art-<id>`."""

    PREFIX = "art-"

    def __init__(self, keep: int = 4):
        self._images: OrderedDict[str, bytes] = OrderedDict()
        self._keep = keep

    def put(self, image: bytes) -> str:
        asset_id = self.PREFIX + hashlib.sha256(image).hexdigest()[:24]
        self._images[asset_id] = image
        self._images.move_to_end(asset_id)
        while len(self._images) > self._keep:
            self._images.popitem(last=False)
        return asset_id

    def get(self, asset_id: str) -> Optional[tuple[bytes, str]]:
        image = self._images.get(asset_id)
        if image is None:
            return None
        return image, "image/png" if image.startswith(b"\x89PNG") else "image/jpeg"


def airplay_volume_to_percent(airplay_volume: Optional[float]) -> int:
    """AirPlay's slider runs linearly from -30 to 0; None is mute."""
    if airplay_volume is None:
        return 0
    fraction = (airplay_volume - AIRPLAY_MIN_VOLUME) / -AIRPLAY_MIN_VOLUME
    return round(min(1.0, max(0.0, fraction)) * 100)


class AirPlayService:
    """
    @brief Plays one AirPlay receiver's audio through the output Kalinka plays through.

    The receiver hands it PCM as the sender plays and the sender's events as
    they happen; it takes the output with DirectPlayback, serves the audio as
    live FLAC segments, and shows the sender's track. It is the listener of
    the hold it takes.

    A Kalinka client that takes the output, or pauses, drops the sender's
    audio until the sender plays again: after a flush, a resume or a new
    session. A classic sender is told to pause as well; AirPlay 2 takes no
    remote control, so its sender keeps playing to nobody. An output lost
    to idling or to a renderer change is taken again as soon as the
    sender's audio flows.

    Each segment is encoded at the sender's own rate and depth, which the
    receiver announces before the first frame and at every switch. Audio
    that arrives before an announcement is dropped rather than misread, and
    a switch ends the segment so that no byte is read as another format.

    @note All methods run on the event loop; listener callbacks return at
        once and do their work in tasks.
    """

    def __init__(
        self,
        direct: DirectPlayback,
        new_segment: SegmentFactory,
        remote: SenderRemote,
        *,
        prebuffer_ms: int,
    ):
        self._direct = direct
        self._new_segment = new_segment
        self._remote = remote
        self._prebuffer_ms = prebuffer_ms
        self.artwork = ArtworkStore()
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._hold: Optional[DirectPlaybackSession] = None
        self._segment: Optional[Segment] = None
        self._segment_playing = False
        self._format: Optional[SegmentFormat] = None
        self._stream: Optional[StreamFormat] = None
        self._stream_type: Optional[str] = None
        self._track_end_rtp: Optional[int] = None
        self._dropped = 0
        # Set when Kalinka took the output back or paused, until the sender plays again.
        self._suspended = False
        self._volume: Optional[int] = None
        self._sender: Optional[str] = None
        self._meta: Optional[TrackChanged] = None
        self._duration_ms = 0
        self._artwork_id: Optional[str] = None
        self._track_number = 0
        self.output_error: Optional[str] = None

    @property
    def segment(self) -> Optional[Segment]:
        return self._segment

    @property
    def status(self) -> str:
        if self._hold is not None and self._hold.active:
            playing = f"Playing from {self._sender or 'an AirPlay sender'}"
            return f"{playing} ({self.stream_description})" if self._stream else playing
        return "Waiting for an AirPlay sender"

    @property
    def stream_description(self) -> str:
        """What plays, as in "AirPlay 2, ALAC 24-bit 48 kHz"; empty before anything does."""
        if self._stream is None:
            return ""
        kind = _STREAM_KINDS.get(self._stream_type, "AirPlay")
        return f"{kind}, {self._stream.codec} {SegmentFormat(self._stream.rate, self._stream.bits).describe}"

    async def audio(self, pcm: bytes) -> None:
        """PCM from the receiver, in real time while the sender plays."""
        async with self._lock:
            if self._suspended:
                return
            if self._format is None:
                # Until the receiver has said what it writes, the bytes cannot be read.
                self._dropped += len(pcm)
                return
            if self._segment is None and not await self._begin_segment():
                return
            await self._segment.feed(pcm)
            if self._segment.failure:
                await self._lose_output(self._segment.failure)
            elif not self._segment_playing and self._segment.fed_ms >= self._prebuffer_ms:
                await self._play_segment()

    async def event(self, event: Event) -> None:
        async with self._lock:
            await self._event(event)

    async def _event(self, event: Event) -> None:
        match event:
            case PlayBegan():
                self._suspended = False
                # The session's format comes with its first frame.
                self._format = self._stream = None
            case Resumed():
                self._suspended = False
            case StreamType():
                self._stream_type = event.kind
            case StreamFormat():
                self._stream = event
                await self._use_format(SegmentFormat(event.rate, event.bits))
            case OutputFormat():
                await self._output_declared(event)
            case Interrupted():
                self._suspended = False
                if self._flush_at_track_end(event):
                    logger.info("AirPlay track change inside the stream; it plays on")
                else:
                    logger.info("AirPlay stream cut: %s", self._cut_reason(event))
                    await self._interrupt()
            case PlayEnded():
                await self._end_session()
            case TrackChanged():
                self._meta, self._duration_ms = event, 0
                self._track_number += 1
                await self._show(0)
            case Progress():
                self._duration_ms = event.duration_ms
                if event.end_rtp is not None:
                    self._track_end_rtp = event.end_rtp
                await self._show(event.position_ms)
            case Artwork():
                self._artwork_id = self.artwork.put(event.image)
                await self._show(self._position_now())
            case VolumeChanged():
                self._volume = airplay_volume_to_percent(event.airplay_volume)
                await self._apply_volume()
            case RemoteChanged():
                self._remote.update(event)
            case SenderNamed():
                self._sender = event.name

    async def receiver_lost(self) -> None:
        """The receiver stopped: whatever it was playing has ended."""
        async with self._lock:
            await self._end_session()

    async def close(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.receiver_lost()
        await self._remote.aclose()

    async def _use_format(self, format: SegmentFormat) -> None:
        """Encode from here on at this rate and depth; a running segment ends first."""
        if format == self._format:
            return
        if self._dropped:
            logger.debug("AirPlay: %d bytes arrived before their format", self._dropped)
            self._dropped = 0
        if self._segment is not None:
            logger.info("AirPlay stream is now %s", format.describe)
            await self._interrupt()
        self._format = format

    async def _output_declared(self, output: OutputFormat) -> None:
        """The receiver names its PCM at the first frame; its rate is what the bytes are."""
        if output.sample_format != OUTPUT_FORMAT or output.channels != CHANNELS:
            self.output_error = (
                f"The AirPlay receiver writes {output.sample_format} audio in "
                f"{output.channels} channels, which Kalinka cannot read"
            )
            logger.error("%s", self.output_error)
            await self._end_segment()
            self._format = None
            return
        bits = self._format.bits if self._format else 24
        await self._use_format(SegmentFormat(output.rate, bits))

    async def _begin_segment(self) -> bool:
        if self._hold is None or not self._hold.active:
            try:
                self._hold = await self._direct.acquire(TITLE, self, seekable=False)
            except OutputUnavailable as error:
                self._hold = None
                self._suspended = True
                self.output_error = str(error)
                logger.warning("AirPlay cannot play: %s", error)
                return False
            await self._apply_volume()
        segment = self._new_segment(self._format)
        await segment.start()
        self._segment, self._segment_playing = segment, False
        return True

    async def _play_segment(self) -> None:
        source = TrackSource(
            source=ModuleAsset(module=MODULE, asset_id=self._segment.id),
            format="audio/flac",
            sequential=True,
        )
        track = self._track()
        if await self._on_hold(lambda hold: hold.play(source, track)):
            self._segment_playing = True
        else:
            await self._end_segment()

    def _flush_at_track_end(self, event: Interrupted) -> bool:
        """A flush up to the current track's end is the sender moving to the
        next track: what it already sent is the end of this one, and the next
        follows in the same stream. A flush anywhere else is a seek or a skip,
        and a pause names no point at all; both cut the stream."""
        if event.flush_rtp is None or self._track_end_rtp is None:
            return False
        return abs(rtp_distance(event.flush_rtp, self._track_end_rtp)) <= TRACK_END_SLACK

    def _cut_reason(self, event: Interrupted) -> str:
        if event.flush_rtp is None:
            return "the sender paused"
        if self._track_end_rtp is None:
            return f"the sender flushed to {event.flush_rtp} before reporting the track's end"
        seconds = rtp_distance(event.flush_rtp, self._track_end_rtp) / PROGRESS_RATE
        return f"the sender flushed to {event.flush_rtp}, {seconds:+.1f} s from the track's end"

    async def _interrupt(self) -> None:
        """What reached Kalinka ahead of the sender's pause or seek must not be heard.

        The stream is stopped, not paused: a stop drops what the renderer
        holds of it at once, and the next audio starts a new stream. A pause
        that reaches the renderer while it still prepares a stream parks it
        until the next transport command, which a new stream is not.
        """
        if self._segment_playing:
            await self._on_hold(lambda hold: hold.stop())
        await self._end_segment()

    async def _end_segment(self) -> None:
        segment, self._segment = self._segment, None
        self._segment_playing = False
        if segment is not None:
            await segment.end()

    async def _end_session(self) -> None:
        await self._end_segment()
        hold, self._hold = self._hold, None
        if hold is not None and hold.active:
            try:
                await hold.release()
            except HoldEnded:
                pass
        self._suspended = False
        self._remote.forget()
        self._sender = self._meta = self._artwork_id = None
        self._format = self._stream = self._stream_type = self._track_end_rtp = None
        self._duration_ms = self._dropped = 0

    async def _lose_output(self, reason: str) -> None:
        logger.warning("AirPlay playback stopped: %s", reason)
        self.output_error = reason
        self._suspended = True
        await self._end_segment()
        self._spawn(self._remote.send(PAUSE))

    async def _show(self, position_ms: int) -> None:
        if not self._segment_playing:
            return
        track = self._track()
        try:
            await self._on_hold(lambda hold: hold.show(track, max(0, position_ms)))
        except ValueError:
            logger.debug("AirPlay track not shown: nothing plays")

    async def _apply_volume(self) -> None:
        if self._volume is None:
            return
        try:
            await self._on_hold(lambda hold: hold.set_volume(self._volume))
        except OutputUnavailable as error:
            logger.info("AirPlay volume not applied: %s", error)

    async def _on_hold(
        self, call: Callable[[DirectPlaybackSession], Awaitable[None]]
    ) -> bool:
        """Run a call on the hold; False when there is none or it has ended."""
        if self._hold is None:
            return False
        try:
            await call(self._hold)
            return True
        except HoldEnded:
            self._hold = None
            return False

    def _position_now(self) -> int:
        if self._hold is None or not self._hold.active:
            return 0
        state = self._hold.state
        position = state.position or 0
        if state.state == PlayerStateEnum.PLAYING:
            position += (time.monotonic_ns() - state.timestamp_ns) // 1_000_000
        return position

    def _track(self) -> Track:
        meta = self._meta or TrackChanged(title="", artist="", album="")

        def entity(kind: EntityType, value: str) -> EntityId:
            return EntityId(source=MODULE, type=kind, id=value or "unknown")

        artist = Artist(id=entity(EntityType.ARTIST, meta.artist), name=meta.artist)
        url = f"/content/{MODULE}/{self._artwork_id}" if self._artwork_id else ""
        return Track(
            id=entity(EntityType.TRACK, str(self._track_number)),
            title=meta.title or self._sender or TITLE,
            duration=self._duration_ms // 1000,
            performer=artist,
            album=Album(
                id=entity(EntityType.ALBUM, meta.album),
                title=meta.album,
                artist=artist,
                image=CoverImage(small=url, thumbnail=url, large=url),
            ),
        )

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def on_state(self, state: PlaybackState) -> None:
        if state.state == PlayerStateEnum.ERROR:
            self._spawn(self._renderer_failed(state.message or "the renderer failed"))
        elif state.state == PlayerStateEnum.PLAYING:
            self.output_error = None

    async def _renderer_failed(self, message: str) -> None:
        async with self._lock:
            if self._segment_playing:
                await self._lose_output(message)

    def on_finished(self) -> None:
        pass

    def on_next_started(self, track: Track) -> None:
        pass

    def on_volume(self, volume) -> None:
        pass

    def on_command(self, request: TransportRequest) -> None:
        self._spawn(self._command(request))

    async def _command(self, request: TransportRequest) -> None:
        match request.kind:
            case TransportKind.PAUSE:
                async with self._lock:
                    self._suspended = True
                    await self._interrupt()
                await self._remote.send(PAUSE)
            case TransportKind.RESUME:
                async with self._lock:
                    self._suspended = False
                await self._remote.send(PLAY)
            case TransportKind.NEXT:
                await self._remote.send(NEXT)
            case TransportKind.PREV:
                await self._remote.send(PREVIOUS)

    def on_revoked(self, reason: RevokeReason) -> None:
        self._spawn(self._revoked(reason))

    async def _revoked(self, reason: RevokeReason) -> None:
        """A takeover silences the sender; an idle or lost output is taken again on the next audio."""
        taken_over = reason in _TAKEN_OVER
        async with self._lock:
            self._hold = None
            await self._end_segment()
            self._suspended |= taken_over
        if taken_over:
            await self._remote.send(PAUSE)
