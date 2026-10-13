"""AirPlay receiver input for Kalinka."""

import io
import socket
from pathlib import Path
from typing import ClassVar

from kalinka_plugin_sdk import paths
from kalinka_plugin_sdk.datamodel import EmptyList
from kalinka_plugin_sdk.dynamic_fields import DynamicFieldDecl
from kalinka_plugin_sdk.inputmodule import ContentInfo, InputModule
from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_plugin_sdk.module_health import ModuleHealthState, ModuleState
from kalinka_plugin_sdk.plugin import InputModulePlugin
from pydantic import ConfigDict, Field

from .receiver import Receiver, ReceiverOptions
from .remote import SenderRemote
from .segment import segment_factory
from .service import MODULE, TITLE, AirPlayService

LIBEXEC = "/usr/libexec/kalinka-plugin-airplay"


class AirPlayConfig(ModuleConfig):
    model_config = ConfigDict(use_enum_values=True)

    __module_icon__: ClassVar[str] = "speaker_outlined"
    __module_icon_color__: ClassVar[str] = "#0A84FF"

    name: str = Field(
        default=MODULE,
        title=TITLE,
        description="Play from an iPhone, iPad or Mac through the renderer selected in Kalinka.",
        frozen=True,
        exclude=True,
    )
    enabled: bool = Field(
        default=False,
        title="Enable AirPlay",
        json_schema_extra={"importance": "simple"},
    )
    device_name: str = Field(
        default_factory=lambda: f"Kalinka ({socket.gethostname()})",
        min_length=1,
        max_length=50,
        pattern=r"^[^\x00-\x1f]+$",
        title="AirPlay speaker name",
        description="Name shown in the AirPlay menu. Choose the output in Kalinka's renderer selector.",
        json_schema_extra={"importance": "simple"},
    )
    airplay2: bool = Field(
        default=True,
        title="Offer AirPlay 2",
        json_schema_extra={
            "help": (
                "AirPlay 2 groups with other AirPlay 2 speakers and needs UDP ports "
                "319 and 320. Off, or when those ports are taken, Kalinka offers "
                "classic AirPlay, which also takes play, pause and skip from Kalinka."
            ),
        },
    )
    port: int = Field(default=7000, ge=1024, le=65535, title="Service port")
    prebuffer_ms: int = Field(
        default=500, ge=100, le=1500, title="Buffer before playing (ms)"
    )
    renderer_delay_ms: int = Field(
        default=300,
        ge=0,
        le=1000,
        title="Renderer delay (ms)",
        json_schema_extra={
            "help": "How long the renderer takes to sound what it receives; tune it for lip sync with video."
        },
    )
    cache_limit_mib: int = Field(
        default=32, ge=1, le=128, title="Stream buffer limit (MiB)"
    )
    executable: str = Field(default=f"{LIBEXEC}/shairport-sync", title="shairport-sync executable")
    nqptp_executable: str = Field(default=f"{LIBEXEC}/nqptp", title="nqptp executable")
    flac_executable: str = Field(default="/usr/bin/flac", title="FLAC encoder")


class AirPlayInput(InputModule):
    def __init__(self, plugin: "KalinkaPluginAirPlay"):
        self._plugin = plugin

    def module_name(self):
        return MODULE

    def display_name(self):
        return TITLE

    async def browse(self, entity_id, offset=0, limit=50, filter=None):
        return EmptyList(offset, limit)

    async def search(self, type, query, offset=0, limit=50):
        return EmptyList(offset, limit)

    async def get_track_info(self, track_ids):
        return []

    async def get_content_info(self, asset_id):
        service = self._plugin.service
        if service is None:
            return None
        segment = service.segment
        if segment is not None and segment.id == asset_id:
            return ContentInfo(mime_type="audio/flac", live=segment.capture, cacheable=False)
        artwork = service.artwork.get(asset_id)
        if artwork is None:
            return None
        image, mime_type = artwork
        return ContentInfo(
            mime_type=mime_type, reader=lambda: io.BytesIO(image), size=len(image)
        )


class KalinkaPluginAirPlay(InputModulePlugin):
    PLUGIN_ID = MODULE
    REQUIRES_SDK = ">=3.11,<4"
    CONFIG_MODEL = AirPlayConfig
    DYNAMIC_FIELDS: ClassVar[dict[str, DynamicFieldDecl]] = {
        "receiver_status": DynamicFieldDecl(section_id="", label="Receiver status", widget="text"),
    }

    def __init__(self):
        self.service = None
        self.receiver = None
        self.enabled = False
        self.interface = AirPlayInput(self)

    def get_interface(self):
        return self.interface

    async def setup(self, context):
        config = AirPlayConfig(**context.config.model_dump())
        self.enabled = config.enabled
        if not config.enabled:
            return
        if context.direct_playback is None:
            raise RuntimeError("AirPlay requires Kalinka direct playback")
        cache = Path(paths.cache_dir()) / MODULE
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.service = AirPlayService(
            context.direct_playback,
            segment_factory(config.flac_executable, cache, config.cache_limit_mib * 1024 * 1024),
            SenderRemote(),
            prebuffer_ms=config.prebuffer_ms,
        )
        self.receiver = Receiver(
            ReceiverOptions(
                executable=config.executable,
                nqptp_executable=config.nqptp_executable,
                flac_executable=config.flac_executable,
                name=config.device_name,
                port=config.port,
                airplay2=config.airplay2,
                # Audio leaves early by as much as Kalinka delays it, for lip sync.
                latency_offset_s=-(config.prebuffer_ms + config.renderer_delay_ms) / 1000,
            ),
            self.service,
            Path(paths.state_dir()) / MODULE,
        )
        self.receiver.start()

    async def resolve_dynamic_field(self, path):
        if path == "receiver_status":
            return self.receiver.status if self.receiver else "Disabled"
        raise KeyError(path)

    async def get_state(self):
        if not self.enabled:
            return ModuleState(state=ModuleHealthState.DISABLED)
        if self.receiver is None:
            return ModuleState(state=ModuleHealthState.ERROR, message="Not started")
        if self.receiver.error:
            return ModuleState(state=ModuleHealthState.ERROR, message=self.receiver.error)
        warning = self.service.output_error or self.receiver.notice
        if warning:
            return ModuleState(state=ModuleHealthState.WARNING, message=warning)
        return ModuleState(state=ModuleHealthState.READY, message=self.receiver.status)

    async def shutdown(self):
        if self.receiver:
            await self.receiver.stop()
        if self.service:
            await self.service.close()
