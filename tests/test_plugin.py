import asyncio
from types import SimpleNamespace

import pytest
from fakes import Direct
from kalinka_plugin_sdk.live_capture import LiveCapture
from pydantic import ValidationError

from kalinka_plugin_airplay import AirPlayConfig, KalinkaPluginAirPlay
from kalinka_plugin_airplay.metadata import Artwork


def test_defaults():
    config = AirPlayConfig()
    assert not config.enabled and config.airplay2
    assert config.device_name.startswith("Kalinka (")
    assert config.executable == "/usr/libexec/kalinka-plugin-airplay/shairport-sync"


def test_a_saved_sample_rate_setting_from_0_1_is_ignored():
    assert AirPlayConfig(output_rate="48 kHz").enabled is False


@pytest.mark.parametrize("name", ["", "x" * 51, "two\nlines"])
def test_a_name_airplay_cannot_advertise_is_refused(name):
    with pytest.raises(ValidationError):
        AirPlayConfig(device_name=name)


async def test_disabled_starts_nothing():
    plugin = KalinkaPluginAirPlay()
    await plugin.setup(SimpleNamespace(config=AirPlayConfig()))
    assert plugin.receiver is None
    assert (await plugin.get_state()).state.value == "disabled"
    assert await plugin.resolve_dynamic_field("receiver_status") == "Disabled"
    assert await plugin.get_interface().get_content_info("anything") is None
    await plugin.shutdown()


async def test_enabled_needs_direct_playback():
    plugin = KalinkaPluginAirPlay()
    with pytest.raises(RuntimeError, match="direct playback"):
        await plugin.setup(SimpleNamespace(config=AirPlayConfig(enabled=True), direct_playback=None))


@pytest.fixture
async def running(tmp_path, monkeypatch):
    monkeypatch.setenv("KALINKA_PREFIX", str(tmp_path))
    plugin = KalinkaPluginAirPlay()
    config = AirPlayConfig(enabled=True, executable=str(tmp_path / "missing"))
    await plugin.setup(SimpleNamespace(config=config, direct_playback=Direct()))
    yield plugin
    await plugin.shutdown()


async def test_a_missing_receiver_is_an_error_with_its_reason(running):
    async with asyncio.timeout(5):
        while running.receiver.error is None:
            await asyncio.sleep(0.01)
    state = await running.get_state()
    assert state.state.value == "error"
    assert "shairport-sync is missing" in state.message


async def test_cover_art_is_content(running):
    await running.service.event(Artwork(b"\xff\xd8 jpeg"))
    [asset_id] = list(running.service.artwork._images)
    info = await running.get_interface().get_content_info(asset_id)
    assert info.mime_type == "image/jpeg"
    assert info.reader().read() == b"\xff\xd8 jpeg" and info.size == 7


async def test_the_playing_segment_is_live_flac(running, tmp_path):
    capture = LiveCapture(tmp_path)
    running.service._segment = SimpleNamespace(id=capture.id, capture=capture)
    try:
        info = await running.get_interface().get_content_info(capture.id)
        assert info.mime_type == "audio/flac" and info.live is capture
        assert await running.get_interface().get_content_info("older") is None
    finally:
        running.service._segment = None
        await capture.retire()
