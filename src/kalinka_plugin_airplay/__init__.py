"""AirPlay receiver input for Kalinka."""

import asyncio
import io
from pathlib import Path
from typing import ClassVar

from kalinka_plugin_sdk import paths
from kalinka_plugin_sdk.datamodel import EmptyList
from kalinka_plugin_sdk.dynamic_fields import DynamicFieldDecl
from kalinka_plugin_sdk.inputmodule import ContentInfo, InputModule
from kalinka_plugin_sdk.live_audio import LivePolicy
from kalinka_plugin_sdk.module_config import ModuleConfig
from kalinka_plugin_sdk.module_health import ModuleHealthState, ModuleState
from kalinka_plugin_sdk.plugin import InputModulePlugin
from kalinka_plugin_sdk.receiver_control import (
    ReceiverDescriptor,
    ReceiverError,
    ReceiverSnapshot,
)
from pydantic import ConfigDict, Field, model_validator

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
        json_schema_extra={"importance": "simple", "applies_live": True},
    )
    device_name: str = Field(
        default="",
        max_length=50,
        pattern=r"^[^\x00-\x1f\x7f]*$",
        title="AirPlay speaker name",
        description="Leave empty to follow the streamer name. Changing it ends the current AirPlay session.",
        json_schema_extra={"importance": "simple", "applies_live": True},
    )
    password: str = Field(
        default="", max_length=128, pattern=r"^[^\x00-\x1f\x7f]*$", repr=False,
        title="AirPlay password",
        description="Leave empty for no password. Applying a change restarts AirPlay and ends its current session; Kalinka keeps running.",
        json_schema_extra={"widget": "password", "importance": "simple", "applies_live": True},
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
    target_latency_ms: int = Field(default=60, ge=20, le=500, title="Live audio target (ms)")
    max_buffer_ms: int = Field(default=250, ge=21, le=2000, title="Maximum live lag (ms)")
    renderer_delay_ms: int = Field(
        default=0, ge=0, le=1000, title="Additional output delay (ms)",
        description="Extra downstream DSP/device delay for lip sync; excludes Kalinka's live target.",
    )

    @model_validator(mode="after")
    def validate_latency(self):
        LivePolicy(self.target_latency_ms, self.max_buffer_ms)
        return self

    executable: str = Field(default=f"{LIBEXEC}/shairport-sync", title="shairport-sync executable")
    nqptp_executable: str = Field(default=f"{LIBEXEC}/nqptp", title="nqptp executable")


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
        artwork = service.artwork.get(asset_id)
        if artwork is None:
            return None
        image, mime_type = artwork
        return ContentInfo(
            mime_type=mime_type, reader=lambda: io.BytesIO(image), size=len(image)
        )


class KalinkaPluginAirPlay(InputModulePlugin):
    PLUGIN_ID = MODULE
    REQUIRES_SDK = ">=3.23,<4"
    SETUP_WHEN_DISABLED = True
    RECEIVER = ReceiverDescriptor(
        title="AirPlay", kind="airplay",
        instructions="Choose this speaker in your device's AirPlay menu on the same network.",
    )
    CONFIG_MODEL = AirPlayConfig
    DYNAMIC_FIELDS: ClassVar[dict[str, DynamicFieldDecl]] = {
        "receiver_status": DynamicFieldDecl(section_id="", label="Receiver status", widget="text"),
    }

    def __init__(self):
        self.service = None
        self.receiver = None
        self._password = ""
        self.enabled = False
        self.context = None
        self._lock = asyncio.Lock()
        self._watcher = None
        self._name = None
        self._transition = None
        self._configure_task = None
        self._config_dirty = False
        self._closing = False
        self._lifecycle_error = None
        self.interface = AirPlayInput(self)

    def get_interface(self):
        return self.interface

    def get_receiver_control(self):
        return self

    def _notify(self):
        callback = getattr(self.context, "receiver_changed", None)
        if callback:
            callback()

    def _desired_name(self):
        read = getattr(self.context, "server_name", None)
        name = self.context.config.device_name.strip() or (read() if read else "Kalinka")
        return "".join(c for c in name if ord(c) >= 32 and ord(c) != 127)[:50] or "Kalinka"

    async def setup(self, context):
        self.context = context
        await self.config_changed(frozenset({"enabled"}))

    async def config_changed(self, changed):
        if not {"enabled", "device_name", "password"} & changed or self._closing:
            return
        self._config_dirty = True
        if self._configure_task is None or self._configure_task.done():
            self._configure_task = asyncio.create_task(self._configure())
            # Retrieve an orphaned failure after Core's callback budget expires;
            # snapshot/get_state retain its reason for the next client read.
            self._configure_task.add_done_callback(
                lambda task: None if task.cancelled() else task.exception()
            )
        await asyncio.shield(self._configure_task)

    async def _configure(self):
        async with self._lock:
            while self._config_dirty and not self._closing:
                self._config_dirty = False
                enabled = self.context.config.enabled
                name = self._desired_name()
                if enabled == self.enabled and (not enabled or (name == self._name and self.context.config.password == self._password)):
                    continue
                self.enabled = enabled
                self._transition = "starting" if enabled else "stopping"
                self._lifecycle_error = None
                self._notify()
                try:
                    await self._stop()
                    # A newer saved setting may have arrived during shutdown.
                    self.enabled = self.context.config.enabled and not self._closing
                    if self.enabled:
                        self._start(self._desired_name())
                except Exception as error:
                    self._lifecycle_error = str(error)
                    raise
                finally:
                    self._transition = None
                    self._notify()

    async def server_name_changed(self):
        if not self.context.config.device_name.strip():
            await self.config_changed(frozenset({"device_name"}))

    def _start(self, name):
        context = self.context
        config = AirPlayConfig(**context.config.model_dump())
        if context.direct_playback is None:
            raise RuntimeError("AirPlay requires Kalinka direct playback")
        if context.live_audio is None:
            raise RuntimeError("AirPlay requires Kalinka live PCM support (SDK 3.23)")
        self._name = name
        self._password = config.password
        self.service = AirPlayService(
            context.direct_playback,
            segment_factory(context.live_audio),
            SenderRemote(),
            policy=LivePolicy(config.target_latency_ms, config.max_buffer_ms),
        )
        self.receiver = Receiver(
            ReceiverOptions(
                executable=config.executable,
                nqptp_executable=config.nqptp_executable,
                name=name,
                password=config.password,
                port=config.port,
                airplay2=config.airplay2,
                # Audio leaves early by as much as Kalinka delays it, for lip sync.
                latency_offset_s=-(config.target_latency_ms + config.renderer_delay_ms) / 1000,
            ),
            self.service,
            Path(paths.state_dir()) / MODULE,
        )
        self.receiver.start()
        self._watcher = asyncio.create_task(self._watch())

    async def _watch(self):
        # Observe only control state, never PCM or playback-position updates.
        previous = None
        while True:
            snapshot = await self.snapshot()
            if snapshot != previous:
                previous = snapshot
                self._notify()
            await asyncio.sleep(1)

    async def snapshot(self):
        receiver = self.receiver
        error = self._lifecycle_error or (receiver.error if receiver else None)
        state = self._transition or (
            "disabled" if not self.enabled else
            "error" if error or receiver is None else
            "ready" if receiver.running else "starting"
        )
        session = self.service.receiver_session if self.service else None
        return ReceiverSnapshot(
            enabled=self.enabled, state=state,
            message=error or (self.service.output_error if self.service else None),
            effective_name=self._name if self.enabled else self._desired_name(),
            name_inherited=not bool(self.context.config.device_name.strip()),
            # A running child is not confirmation of mDNS advertisement.
            visibility="unknown" if self.enabled else "hidden",
            sessions=[session] if session else [],
            password_required=bool(self.context.config.password),
            password_reveal_available=state == "ready" and bool(self._password),
        )

    async def reveal_password(self):
        if self._transition or not self.enabled or not self.receiver or not self.receiver.running:
            raise ReceiverError("unavailable", "AirPlay is not ready")
        return self._password

    async def execute(self, command):
        raise ReceiverError("unsupported_operation", "AirPlay availability is controlled by its enabled setting")

    async def resolve_dynamic_field(self, path):
        if path == "receiver_status":
            return self.receiver.status if self.receiver else "Disabled"
        raise KeyError(path)

    async def get_state(self):
        if not self.enabled:
            return ModuleState(state=ModuleHealthState.DISABLED)
        if self._lifecycle_error:
            return ModuleState(state=ModuleHealthState.ERROR, message=self._lifecycle_error)
        if self.receiver is None:
            return ModuleState(state=ModuleHealthState.ERROR, message="Not started")
        if self.receiver.error:
            return ModuleState(state=ModuleHealthState.ERROR, message=self.receiver.error)
        warning = self.service.output_error or self.receiver.notice
        if warning:
            return ModuleState(state=ModuleHealthState.WARNING, message=warning)
        return ModuleState(state=ModuleHealthState.READY, message=self.receiver.status)

    async def _stop(self):
        if self._watcher:
            self._watcher.cancel()
            await asyncio.gather(self._watcher, return_exceptions=True)
            self._watcher = None
        if self.receiver:
            await self.receiver.stop()
        if self.service:
            await self.service.close()
        self.receiver = self.service = None

    async def shutdown(self):
        self._closing = True
        if self._configure_task:
            await asyncio.gather(self._configure_task, return_exceptions=True)
        async with self._lock:
            self.enabled = False
            await self._stop()
        self._notify()
