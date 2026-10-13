import pytest
from pipe_items import core, item, ssnc, track

from kalinka_plugin_airplay.metadata import (
    Artwork,
    EventDecoder,
    Interrupted,
    Item,
    ItemParser,
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

PICTURE = bytes(range(256)) * 40


def decode_all(stream: bytes):
    parser, decoder = ItemParser(), EventDecoder()
    events = [decoder.decode(i) for i in parser.feed(stream)]
    return [e for e in events if e is not None]


def test_items_survive_any_read_boundary():
    stream = ssnc("pbeg") + ssnc("PICT", PICTURE) + core("minm", "Ünïcode".encode())
    expected = [
        Item("ssnc", "pbeg"),
        Item("ssnc", "PICT", PICTURE),
        Item("core", "minm", "Ünïcode".encode()),
    ]
    for size in (1, 7, 76, 4096):
        parser = ItemParser()
        items = []
        for start in range(0, len(stream), size):
            items += parser.feed(stream[start : start + size])
        assert items == expected


def test_an_abandoned_item_does_not_hide_the_next():
    truncated = ssnc("PICT", PICTURE)[:200]
    stream = truncated + b"noise" + ssnc("pend")
    assert ItemParser().feed(stream) == [Item("ssnc", "pend")]


def test_an_item_whose_length_lies_is_dropped():
    lying = ssnc("snam", b"phone").replace(b"<length>5<", b"<length>9<")
    assert ItemParser().feed(lying + ssnc("pend")) == [Item("ssnc", "pend")]


def test_an_oversized_item_is_skipped():
    parser = ItemParser(max_item_bytes=1000)
    assert parser.feed(ssnc("PICT", PICTURE)[:2000]) == []
    assert parser.feed(b"more" * 100 + ssnc("pbeg")) == [Item("ssnc", "pbeg")]


def test_play_state_events():
    stream = ssnc("pbeg") + ssnc("pfls", b"1234") + ssnc("paus") + ssnc("pres")
    stream += ssnc("prsm") + ssnc("pend") + ssnc("abeg")
    assert decode_all(stream) == [
        PlayBegan(),
        Interrupted(flush_rtp=1234),
        Interrupted(),
        Resumed(),
        Resumed(),
        PlayEnded(),
    ]


def test_a_track_is_reported_once_its_fields_are_all_in():
    events = decode_all(track("Song", "Singer", "Record"))
    assert events == [TrackChanged("Song", "Singer", "Record")]


def test_a_new_track_does_not_inherit_the_last_ones_fields():
    stream = track("Song", "Singer", "Record")
    stream += ssnc("mdst") + core("minm", b"Podcast") + ssnc("mden")
    assert decode_all(stream)[-1] == TrackChanged("Podcast", "", "")


def test_progress_counts_from_the_track_start_across_the_wrap():
    start = (1 << 32) - 44100
    stream = ssnc("prgr", f"{start}/{start + 88200 - (1 << 32)}/{441000 - 44100}".encode())
    assert decode_all(stream) == [
        Progress(position_ms=2000, duration_ms=10000, end_rtp=441000 - 44100)
    ]


@pytest.mark.parametrize(
    "payload, volume",
    [(b"-15.00,-30.00,-96.30,0.00", -15.0), (b"-144.00,-96.30,-96.30,0.00", None)],
)
def test_volume(payload, volume):
    assert decode_all(ssnc("pvol", payload)) == [VolumeChanged(volume)]


def test_the_remote_address_builds_up_over_the_session():
    events = decode_all(
        ssnc("clip", b"192.168.1.20") + ssnc("acre", b"1234567") + ssnc("dapo", b"52341")
    )
    assert events[-1] == RemoteChanged("192.168.1.20", 52341, "1234567")
    assert events[0] == RemoteChanged("192.168.1.20", None, None)
    assert "1234567" not in repr(events[-1])


def test_a_withdrawn_port_is_no_port():
    events = decode_all(ssnc("clip", b"10.0.0.2") + ssnc("dapo", b"0"))
    assert events[-1].port is None


def test_cover_art_and_sender_name():
    events = decode_all(ssnc("PICT", PICTURE) + ssnc("PICT") + ssnc("snam", b"Jo's iPhone"))
    assert events == [Artwork(PICTURE), SenderNamed("Jo's iPhone")]


@pytest.mark.parametrize(
    "name, expected",
    [
        (b"ALAC/44100/S16_LE/2", StreamFormat("ALAC", 44100, 16, 2)),
        (b"ALAC/48000/S24_LE/2", StreamFormat("ALAC", 48000, 24, 2)),
        (b"AAC/44100/F24/2", StreamFormat("AAC", 44100, 24, 2)),
        (b"AAC/48000/F24/5.1", StreamFormat("AAC", 48000, 24, 6)),
        (b"AAC/48000/F24/7.1", StreamFormat("AAC", 48000, 24, 8)),
    ],
)
def test_the_stream_format_as_the_receiver_names_it(name, expected):
    assert decode_all(ssnc("sdsc", name)) == [expected]


def test_a_stream_description_that_is_no_format_is_ignored():
    assert decode_all(ssnc("sdsc", b"None (0)") + ssnc("odsc", b"what/ever")) == []


def test_the_stream_type_and_output_format():
    events = decode_all(ssnc("styp", b"Buffered") + ssnc("odsc", b"48000/S24_3LE/2"))
    assert events == [StreamType("Buffered"), OutputFormat(48000, "S24_3LE", 2)]


def test_other_items_are_ignored():
    stream = item("core", "asgn", b"Pop") + ssnc("svip", b"10.0.0.1") + item("abcd", "efgh")
    assert decode_all(stream) == []
