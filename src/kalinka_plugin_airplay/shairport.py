"""shairport-sync as Kalinka runs it: what a build can do, and the config it gets."""

import asyncio
from dataclasses import dataclass
from pathlib import Path

from .child import ChildError

REQUIRED_FEATURES = ("stdout", "metadata")
CHANNELS = 2
SAMPLE_BYTES = 3  # S24_3LE: packed 24-bit little-endian
FRAME_BYTES = CHANNELS * SAMPLE_BYTES


@dataclass(frozen=True)
class Build:
    """What `shairport-sync -V` says a build is: its version and compiled-in features."""

    version: str
    features: frozenset[str]

    @property
    def airplay2(self) -> bool:
        return "AirPlay2" in self.features

    @property
    def remote_control(self) -> bool:
        # Only Avahi resolves the sender's control port into the metadata.
        return "Avahi" in self.features


def parse_version(text: str) -> Build:
    """Parse e.g. "5.5.2-AirPlay2-FFmpeg-OpenSSL-Avahi-stdout-metadata-sysconfdir:/etc"."""
    version, *features = text.strip().split("-")
    return Build(version, frozenset(f for f in features if ":" not in f))


async def probe(executable: str) -> Build:
    """@throw ChildError when the executable is missing or lacks what Kalinka needs."""
    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "-V",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            stdin=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        raise ChildError(f"shairport-sync is missing at {executable}") from None
    try:
        output, _ = await asyncio.wait_for(process.communicate(), 5)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ChildError("shairport-sync did not report its version") from None
    build = parse_version(output.decode("utf-8", errors="replace"))
    missing = [f for f in REQUIRED_FEATURES if f not in build.features]
    if missing:
        raise ChildError(
            f"shairport-sync {build.version} was built without {' and '.join(missing)} "
            "support; use the build this plugin ships"
        )
    return build


@dataclass(frozen=True)
class ReceiverSettings:
    """
    @brief Everything the generated configuration depends on.

    @param latency_offset_s Added to the sender's latency: negative, so that
        audio leaves early by as much as Kalinka's own path delays it.
    """

    name: str
    port: int
    airplay2: bool
    output_rate: int
    latency_offset_s: float
    metadata_pipe: Path


def render_config(settings: ReceiverSettings) -> str:
    """The libconfig text shairport-sync is started with."""
    service_type = "airplay2" if settings.airplay2 else "classic"
    return f"""general =
{{
  name = {_quote(settings.name)};
  port = {settings.port};
  service_type = "{service_type}";
  output_backend = "stdout";
  ignore_volume_control = "yes";
  audio_backend_latency_offset_in_seconds = {settings.latency_offset_s:.3f};
}};

stdout =
{{
  output_rate = {settings.output_rate};
  output_format = "S24_3LE";
  output_channels = {CHANNELS};
}};

metadata =
{{
  enabled = "yes";
  include_cover_art = "yes";
  cover_art_cache_directory = "";
  pipe_name = {_quote(str(settings.metadata_pipe))};
}};
"""


def _quote(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
