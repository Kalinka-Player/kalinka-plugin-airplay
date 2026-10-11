from pathlib import Path

import pytest

from kalinka_plugin_airplay.child import ChildError
from kalinka_plugin_airplay.shairport import (
    ReceiverSettings,
    parse_version,
    probe,
    render_config,
)

AIRPLAY2 = "5.5.2-AirPlay2-smi10-FFmpeg-OpenSSL-Avahi-stdout-pipe-soxr-metadata-sysconfdir:/etc"


def script(tmp_path: Path, output: str) -> str:
    path = tmp_path / "shairport-sync"
    path.write_text(f"#!/bin/sh\necho '{output}'\n")
    path.chmod(0o755)
    return str(path)


def test_version_and_features():
    build = parse_version(AIRPLAY2 + "\n")
    assert build.version == "5.5.2"
    assert build.airplay2 and build.remote_control
    assert "sysconfdir:/etc" not in build.features


def test_a_classic_build_without_avahi():
    build = parse_version("4.3.7-OpenSSL-tinysvcmdns-stdout-metadata")
    assert not build.airplay2 and not build.remote_control


async def test_probe_accepts_a_build_with_stdout_and_metadata(tmp_path):
    assert (await probe(script(tmp_path, AIRPLAY2))).airplay2


@pytest.mark.parametrize(
    "version, missing",
    [
        ("4.3.7-OpenSSL-Avahi-ALSA-metadata", "stdout"),
        ("4.3.7-OpenSSL-Avahi-ALSA", "stdout and metadata"),
    ],
)
async def test_probe_refuses_what_kalinka_cannot_use(tmp_path, version, missing):
    with pytest.raises(ChildError, match=f"without {missing} support"):
        await probe(script(tmp_path, version))


async def test_probe_says_when_the_executable_is_missing(tmp_path):
    with pytest.raises(ChildError, match="missing"):
        await probe(str(tmp_path / "nothing"))


def settings(**changes) -> ReceiverSettings:
    values = dict(
        name="Kalinka (pi)",
        port=7000,
        airplay2=True,
        output_rate=44100,
        latency_offset_s=-0.8,
        metadata_pipe=Path("/var/lib/kalinka/airplay/metadata"),
    )
    return ReceiverSettings(**(values | changes))


def test_config_pins_the_output_and_routes_metadata():
    text = render_config(settings())
    for line in (
        'name = "Kalinka (pi)";',
        "port = 7000;",
        'service_type = "airplay2";',
        'output_backend = "stdout";',
        'ignore_volume_control = "yes";',
        "audio_backend_latency_offset_in_seconds = -0.800;",
        "output_rate = 44100;",
        'output_format = "S24_3LE";',
        "output_channels = 2;",
        'include_cover_art = "yes";',
        'pipe_name = "/var/lib/kalinka/airplay/metadata";',
    ):
        assert line in text


def test_config_for_classic_airplay():
    assert 'service_type = "classic";' in render_config(settings(airplay2=False))


def test_a_name_cannot_break_out_of_its_string():
    text = render_config(settings(name='Den "TV" \\ room'))
    assert 'name = "Den \\"TV\\" \\\\ room";' in text
