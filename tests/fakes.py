"""Stand-ins for what the service talks to: the server's hold, segments, the sender."""

import time

from kalinka_plugin_sdk.datamodel import PlaybackState, PlayerStateEnum
from kalinka_plugin_sdk.direct_playback import HoldEnded, OutputUnavailable


class Segment:
    count = 0

    def __init__(self, format):
        Segment.count += 1
        self.id = f"segment-{Segment.count}"
        self.format = format
        self.fed_ms = 0.0
        self.failure = None
        self.started = self.ended = False

    async def start(self):
        self.started = True

    async def feed(self, pcm):
        self.fed_ms += len(pcm) / 6 / (self.format.rate / 1000)

    async def end(self):
        self.ended = True


class Hold:
    def __init__(self):
        self.active = True
        self.no_volume = False
        self.calls = []
        self.state = PlaybackState(
            state=PlayerStateEnum.PLAYING, position=0, timestamp_ns=time.monotonic_ns()
        )

    def _record(self, *call):
        if not self.active:
            raise HoldEnded("ended")
        self.calls.append(call)

    async def play(self, source, track):
        self._record("play", source, track)

    async def show(self, track, position_ms):
        self._record("show", track, position_ms)

    async def pause(self):
        self._record("pause")

    async def set_volume(self, percent):
        self._record("volume", percent)
        if self.no_volume:
            raise OutputUnavailable("nothing controls this renderer's volume")

    async def release(self):
        self._record("release")
        self.active = False

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


class Direct:
    def __init__(self):
        self.holds = []
        self.unavailable = None

    async def acquire(self, title, listener, *, seekable=True):
        assert title == "AirPlay" and not seekable
        if self.unavailable:
            raise OutputUnavailable(self.unavailable)
        self.holds.append(Hold())
        return self.holds[-1]

    @property
    def hold(self):
        return self.holds[-1]


class Remote:
    def __init__(self, available=True):
        self.available = available
        self.sent = []
        self.updates = []

    def update(self, remote):
        self.updates.append(remote)

    def forget(self):
        self.updates.append(None)

    async def send(self, command):
        self.sent.append(command)
        return self.available

    async def aclose(self):
        pass
