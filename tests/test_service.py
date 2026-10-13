import asyncio

import pytest
from fakes import Direct, Remote, Segment
from kalinka_plugin_sdk.datamodel import PlaybackState, PlayerStateEnum
from kalinka_plugin_sdk.direct_playback import (
    RevokeReason,
    TransportKind,
    TransportRequest,
)

from kalinka_plugin_airplay.metadata import (
    Artwork,
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
)
from kalinka_plugin_airplay.remote import NEXT, PAUSE, PLAY, PREVIOUS
from kalinka_plugin_airplay.segment import SegmentFormat
from kalinka_plugin_airplay.service import AirPlayService, airplay_volume_to_percent

CHUNK = b"\0" * 6 * 4410  # 100 ms of S24_3LE stereo at 44.1 kHz
CD = StreamFormat("ALAC", 44100, 16, 2)
HIRES = StreamFormat("ALAC", 48000, 24, 2)


@pytest.fixture
def direct():
    return Direct()


@pytest.fixture
def segments():
    made = []

    def new(format):
        made.append(Segment(format))
        return made[-1]

    return made, new


@pytest.fixture
def remote():
    return Remote()


@pytest.fixture
def service(direct, segments, remote):
    return AirPlayService(direct, segments[1], remote, prebuffer_ms=300)


async def play(service, seconds=0.5, stream=CD):
    """Audio as the receiver sends it: its format announced, then PCM."""
    await service.event(stream)
    for _ in range(round(seconds * 10)):
        await service.audio(CHUNK)


async def settle(service):
    await asyncio.sleep(0)
    await asyncio.gather(*list(service._tasks))


async def test_audio_takes_the_output_and_plays_once_the_buffer_is_full(
    service, direct, segments
):
    await service.event(PlayBegan())
    await service.event(CD)
    await service.audio(CHUNK)
    hold = direct.hold
    assert hold.named("play") == []
    await play(service, 0.2)
    [(_, source, track)] = hold.named("play")
    assert source.sequential and source.format == "audio/flac"
    assert source.source.module == "airplay"
    assert source.source.asset_id == segments[0][0].id == service.segment.id
    assert track.title == "AirPlay"
    await play(service, 1)
    assert len(hold.named("play")) == 1


async def test_no_renderer_means_no_audio_until_the_sender_plays_again(
    service, direct, segments
):
    direct.unavailable = "no renderer is connected"
    await play(service)
    assert service.output_error == "no renderer is connected"
    direct.unavailable = None
    await play(service)
    assert direct.holds == [] and segments[0] == []
    await service.event(PlayBegan())
    await play(service)
    assert len(direct.hold.named("play")) == 1


async def test_a_sender_pause_or_seek_drops_what_kalinka_already_has(
    service, direct, segments
):
    await play(service)
    first = segments[0][0]
    await service.event(Interrupted())
    assert direct.hold.named("pause") and first.ended
    assert service.segment is None
    await play(service)
    [_, (_, source, _)] = direct.hold.named("play")
    assert source.source.asset_id == segments[0][1].id != first.id
    assert len(direct.holds) == 1


async def test_the_session_ending_gives_the_output_back(service, direct, segments, remote):
    await service.event(SenderNamed("Jo's iPhone"))
    await service.event(StreamType("Buffered"))
    await play(service, stream=HIRES)
    assert service.status == "Playing from Jo's iPhone (AirPlay 2, ALAC 24-bit 48 kHz)"
    await service.event(PlayEnded())
    assert direct.hold.named("release") and segments[0][0].ended
    assert service.status == "Waiting for an AirPlay sender"
    assert service.stream_description == ""
    assert remote.updates[-1] is None


async def test_audio_before_its_format_is_known_is_dropped(service, direct, segments):
    await service.event(PlayBegan())
    await service.audio(CHUNK)
    assert direct.holds == [] and segments[0] == []
    await play(service)
    assert segments[0][0].format == SegmentFormat(44100, 16)
    assert len(direct.hold.named("play")) == 1


async def test_a_format_switch_ends_the_segment_and_the_next_is_in_the_new_format(
    service, direct, segments
):
    await play(service)
    await service.event(HIRES)
    first = segments[0][0]
    assert first.ended and direct.hold.named("pause") and service.segment is None
    await play(service, stream=HIRES)
    [_, (_, source, _)] = direct.hold.named("play")
    second = segments[0][1]
    assert source.source.asset_id == second.id
    assert second.format == SegmentFormat(48000, 24)
    assert service.status.endswith("(AirPlay, ALAC 24-bit 48 kHz)")


async def test_the_same_format_announced_again_changes_nothing(service, direct, segments):
    await play(service)
    await service.event(CD)
    await service.event(StreamFormat("AAC", 44100, 16, 2))
    assert direct.hold.named("pause") == [] and len(segments[0]) == 1


async def test_a_new_session_waits_for_its_own_format(service, direct, segments):
    await play(service)
    await service.event(PlayEnded())
    await service.event(PlayBegan())
    await service.audio(CHUNK)
    assert len(direct.holds) == 1 and len(segments[0]) == 1
    await play(service, stream=HIRES)
    assert len(direct.holds) == 2 and segments[0][1].format == SegmentFormat(48000, 24)


async def test_the_receivers_output_rate_is_what_its_bytes_are(service, segments):
    await service.event(CD)
    await service.event(OutputFormat(48000, "S24_3LE", 2))
    await service.audio(CHUNK)
    assert segments[0][0].format == SegmentFormat(48000, 16)


async def test_an_output_kalinka_cannot_read_is_dropped_with_a_reason(
    service, direct, segments
):
    await play(service)
    await service.event(OutputFormat(44100, "S32_LE", 2))
    assert "S32_LE" in service.output_error and segments[0][0].ended
    await service.audio(CHUNK)
    assert len(segments[0]) == 1


async def test_the_sender_track_is_shown_without_restarting_the_stream(service, direct):
    await play(service)
    await service.event(TrackChanged("Song", "Singer", "Record"))
    await service.event(Progress(position_ms=12000, duration_ms=200000))
    hold = direct.hold
    assert len(hold.named("play")) == 1
    (_, track, position), (_, timed, at) = hold.named("show")
    assert (track.title, track.performer.name, track.album.title) == ("Song", "Singer", "Record")
    assert position == 0
    assert (timed.duration, at) == (200, 12000)


async def test_cover_art_is_served_and_shown(service, direct):
    await play(service)
    await service.event(Artwork(b"\x89PNG fake"))
    (_, track, _) = direct.hold.named("show")[-1]
    url = track.album.image.large
    asset_id = url.rsplit("/", 1)[1]
    assert url == f"/content/airplay/{asset_id}"
    assert service.artwork.get(asset_id) == (b"\x89PNG fake", "image/png")


async def test_metadata_before_playback_is_used_when_it_starts(service, direct):
    await service.event(TrackChanged("Song", "Singer", "Record"))
    await service.event(VolumeChanged(-15.0))
    await play(service)
    hold = direct.hold
    assert hold.named("show") == []
    assert hold.named("play")[0][2].title == "Song"
    assert hold.named("volume") == [("volume", 50)]


async def test_the_senders_volume_sets_kalinkas(service, direct):
    await play(service)
    await service.event(VolumeChanged(-7.5))
    await service.event(VolumeChanged(None))
    assert direct.hold.named("volume") == [("volume", 75), ("volume", 0)]


async def test_a_renderer_without_volume_control_still_plays(service, direct):
    await play(service)
    direct.hold.no_volume = True
    await service.event(VolumeChanged(-15.0))
    await play(service)
    assert len(direct.hold.named("play")) == 1


@pytest.mark.parametrize("volume, percent", [(0.0, 100), (-30.0, 0), (-40.0, 0), (None, 0)])
def test_airplay_volume_to_percent(volume, percent):
    assert airplay_volume_to_percent(volume) == percent


async def test_pause_from_kalinka_pauses_the_sender_and_drops_its_audio(
    service, direct, segments, remote
):
    await play(service)
    service.on_command(TransportRequest(TransportKind.PAUSE))
    await settle(service)
    assert remote.sent == [PAUSE]
    assert direct.hold.named("pause") and segments[0][0].ended
    await play(service)
    assert len(segments[0]) == 1

    service.on_command(TransportRequest(TransportKind.RESUME))
    await settle(service)
    assert remote.sent == [PAUSE, PLAY]
    await play(service)
    assert len(direct.hold.named("play")) == 2


async def test_a_flush_after_kalinkas_pause_lets_the_sender_resume(service, direct):
    await play(service)
    service.on_command(TransportRequest(TransportKind.PAUSE))
    await settle(service)
    await service.event(Interrupted())
    await play(service)
    assert len(direct.hold.named("play")) == 2


async def test_skipping_goes_to_the_sender(service, remote):
    for kind in (TransportKind.NEXT, TransportKind.PREV):
        service.on_command(TransportRequest(kind))
    await settle(service)
    assert remote.sent == [NEXT, PREVIOUS]


async def test_taking_the_output_back_pauses_the_sender(service, direct, segments, remote):
    await play(service)
    service.on_revoked(RevokeReason.QUEUE_PLAY)
    await settle(service)
    assert remote.sent == [PAUSE] and segments[0][0].ended
    await play(service)
    assert len(direct.holds) == 1
    await service.event(Resumed())
    await play(service)
    assert len(direct.holds) == 2


@pytest.mark.parametrize(
    "reason", [RevokeReason.IDLE, RevokeReason.OUTPUT_LOST, RevokeReason.SHUTDOWN]
)
async def test_an_output_lost_without_a_takeover_is_taken_again(
    service, direct, remote, reason
):
    await play(service)
    service.on_revoked(reason)
    await settle(service)
    assert remote.sent == []
    await play(service)
    assert len(direct.holds) == 2


async def test_a_renderer_error_stops_the_stream_and_pauses_the_sender(
    service, direct, segments, remote
):
    await play(service)
    service.on_state(PlaybackState(state=PlayerStateEnum.ERROR, message="Unsupported stream format"))
    await settle(service)
    assert service.output_error == "Unsupported stream format"
    assert segments[0][0].ended and remote.sent == [PAUSE]
    service.on_state(PlaybackState(state=PlayerStateEnum.PLAYING))
    assert service.output_error is None


async def test_an_encoder_failure_stops_the_stream(service, segments, remote):
    await service.event(CD)
    await service.audio(CHUNK)
    segments[0][0].failure = "flac stopped"
    await service.audio(CHUNK)
    await settle(service)
    assert service.output_error == "flac stopped" and segments[0][0].ended


async def test_a_hold_ended_under_the_service_is_taken_again(service, direct):
    await play(service)
    direct.hold.active = False
    await service.event(Interrupted())
    await play(service)
    assert len(direct.holds) == 2


async def test_the_remote_learns_where_the_sender_listens(service, remote):
    update = RemoteChanged("10.0.0.2", 3689, "token")
    await service.event(update)
    assert remote.updates == [update]


async def test_closing_ends_everything(service, direct, segments):
    await play(service)
    await service.close()
    assert direct.hold.named("release") and segments[0][0].ended
