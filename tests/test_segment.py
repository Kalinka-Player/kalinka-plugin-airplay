"""The receiver's packed PCM reaches Core without narrowing or encoding."""

import pytest
from fakes import LiveAudio

from kalinka_plugin_airplay.segment import SegmentFormat, segment_factory


@pytest.mark.parametrize("rate,bits", [(44100, 16), (48000, 24)])
def test_all_pcm_bits_survive_arbitrary_pipe_boundaries(rate, bits):
    segment = segment_factory(LiveAudio())(SegmentFormat(rate, bits))
    pcm = bytes(range(256)) * 6
    for offset in range(0, len(pcm), 7):
        segment.feed(pcm[offset:offset + 7])
    assert bytes(segment.source.data) == pcm
    assert segment.source.format.sample_rate == rate
    assert segment.source.format.sample_format == "S24_3LE"
    segment.close()
    assert segment.source.closed


def test_partial_frame_is_discarded_at_interrupt():
    segment = segment_factory(LiveAudio())(SegmentFormat(44100, 16))
    segment.feed(b"123456xyz")
    segment.close()
    assert bytes(segment.source.data) == b"123456"
    successor = segment_factory(LiveAudio())(SegmentFormat(48000, 24))
    assert successor.id != segment.id
    successor.feed(b"abcdef")
    assert bytes(successor.source.data) == b"abcdef"
    successor.close()
