import asyncio
from types import SimpleNamespace

import pytest
from fakes import Direct, LiveAudio
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
    await plugin.setup(SimpleNamespace(config=config, direct_playback=Direct(), live_audio=LiveAudio()))
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


async def test_audio_is_owned_by_core_and_only_artwork_is_plugin_content(running):
    running.service._segment = SimpleNamespace(id="source")
    try:
        assert await running.get_interface().get_content_info("source") is None
    finally:
        running.service._segment = None


def test_old_encoder_and_prebuffer_settings_are_ignored():
    config = AirPlayConfig(flac_executable="/missing", prebuffer_ms=1500, cache_limit_mib=128)
    assert config.target_latency_ms == 60
    assert not {"flac_executable", "prebuffer_ms", "cache_limit_mib"} & config.model_dump().keys()


async def test_enabled_requires_core_live_support():
    with pytest.raises(RuntimeError, match="live PCM"):
        await KalinkaPluginAirPlay().setup(SimpleNamespace(
            config=AirPlayConfig(enabled=True), direct_playback=Direct(), live_audio=None
        ))
