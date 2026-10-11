"""The AirPlay receiver: shairport-sync, and nqptp for AirPlay 2, kept running."""

import asyncio
import logging
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .child import Child, ChildError
from .metadata import EventDecoder, ItemParser
from .segment import check_encoder
from .service import AirPlayService
from .shairport import ReceiverSettings, probe, render_config

logger = logging.getLogger(__name__)

_READ_BYTES = 64 * 1024
# nqptp exits at once when its ports or shared memory are taken.
_NQPTP_SETTLE_S = 0.5


@dataclass(frozen=True)
class ReceiverOptions:
    executable: str
    nqptp_executable: str
    flac_executable: str
    name: str
    port: int
    airplay2: bool
    output_rate: int
    latency_offset_s: float


class MetadataPipe:
    """
    @brief The FIFO shairport-sync writes metadata to, read without blocking.

    The plugin holds a write end of its own open, so the read end never sees
    end-of-file when shairport-sync closes and reopens the pipe.
    """

    def __init__(self, path: Path):
        self.path = path
        self._parser = ItemParser()
        self._decoder = EventDecoder()
        self._queue: asyncio.Queue = asyncio.Queue()
        self._fds: list[int] = []

    def __enter__(self) -> "MetadataPipe":
        if self.path.exists() and not stat.S_ISFIFO(self.path.stat().st_mode):
            self.path.unlink()
        if not self.path.exists():
            os.mkfifo(self.path, 0o600)
        read_fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        self._fds = [read_fd, os.open(self.path, os.O_WRONLY | os.O_NONBLOCK)]
        asyncio.get_running_loop().add_reader(read_fd, self._readable, read_fd)
        return self

    def __exit__(self, *exc) -> None:
        asyncio.get_running_loop().remove_reader(self._fds[0])
        for fd in self._fds:
            os.close(fd)

    def _readable(self, fd: int) -> None:
        try:
            data = os.read(fd, _READ_BYTES)
        except BlockingIOError:
            return
        for item in self._parser.feed(data):
            event = self._decoder.decode(item)
            if event is not None:
                self._queue.put_nowait(event)

    async def next_event(self):
        return await self._queue.get()


class Receiver:
    """
    @brief Keeps the AirPlay receiver running and feeds what it plays to the service.

    A failed or exited child brings the whole receiver down and up again,
    after 1, 2, 4 … up to 30 seconds; a minute of running resets the wait.
    """

    def __init__(
        self,
        options: ReceiverOptions,
        service: AirPlayService,
        state_dir: Path,
        *,
        retry_delay_s: float = 1,
        max_retry_delay_s: float = 30,
        stable_after_s: float = 60,
    ):
        self._options = options
        self._service = service
        self._state_dir = state_dir
        self._retry_delay_s = retry_delay_s
        self._max_retry_delay_s = max_retry_delay_s
        self._stable_after_s = stable_after_s
        self._task: Optional[asyncio.Task] = None
        self.error: Optional[str] = None
        self.notice: Optional[str] = None
        self.running = False

    @property
    def status(self) -> str:
        if self.error:
            return f"{self.error}; retrying"
        if not self.running:
            return "Starting"
        return self._service.status

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run(self) -> None:
        delay = self._retry_delay_s
        while True:
            started = time.monotonic()
            try:
                await self._run_once()
            except ChildError as error:
                self.error = str(error)
            except Exception:
                logger.exception("AirPlay receiver failed")
                self.error = "The AirPlay receiver failed unexpectedly"
            self.running = False
            logger.warning("AirPlay receiver stopped: %s", self.error)
            if time.monotonic() - started >= self._stable_after_s:
                delay = self._retry_delay_s
            await asyncio.sleep(delay)
            delay = min(delay * 2, self._max_retry_delay_s)

    async def _run_once(self) -> None:
        options = self._options
        build = await probe(options.executable)
        await check_encoder(options.flac_executable)
        self._state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        children: list[Child] = []
        try:
            airplay2 = options.airplay2 and build.airplay2
            self.notice = None if build.remote_control else (
                "This shairport-sync build cannot take remote control commands"
            )
            if airplay2:
                airplay2 = await self._start_nqptp(children)
            pipe_path = self._state_dir / "metadata"
            config = self._write_config(airplay2, pipe_path)
            shairport = Child(
                "shairport-sync", [options.executable, "-c", str(config)], stdout=True
            )
            with MetadataPipe(pipe_path) as pipe:
                await shairport.start()
                children.append(shairport)
                self.error, self.running = None, True
                logger.info(
                    "AirPlay receiver %r is up (%s)",
                    options.name,
                    "AirPlay 2" if airplay2 else "classic AirPlay",
                )
                await self._serve(children, shairport, pipe)
        finally:
            for child in reversed(children):
                await child.stop()
            await self._service.receiver_lost()

    async def _start_nqptp(self, children: list[Child]) -> bool:
        nqptp = Child("nqptp", [self._options.nqptp_executable])
        await nqptp.start()
        children.append(nqptp)
        await asyncio.sleep(_NQPTP_SETTLE_S)
        if nqptp.returncode is None:
            return True
        children.remove(nqptp)
        await nqptp.stop()
        self.notice = (
            f"AirPlay 2 is off, offering classic AirPlay: {nqptp.exit_reason()}. "
            "It needs UDP ports 319 and 320 to itself"
        )
        logger.warning("%s", self.notice)
        return False

    def _write_config(self, airplay2: bool, pipe_path: Path) -> Path:
        options = self._options
        path = self._state_dir / "shairport-sync.conf"
        text = render_config(
            ReceiverSettings(
                name=options.name,
                port=options.port,
                airplay2=airplay2,
                output_rate=options.output_rate,
                latency_offset_s=options.latency_offset_s,
                metadata_pipe=pipe_path,
            )
        )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as file:
            file.write(text)
        return path

    async def _serve(
        self, children: list[Child], shairport: Child, pipe: MetadataPipe
    ) -> None:
        tasks = [
            asyncio.create_task(self._pump_audio(shairport)),
            asyncio.create_task(self._pump_events(pipe)),
            *(asyncio.create_task(child.wait()) for child in children),
        ]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        stopped = next((c for c in children if c.returncode is not None), shairport)
        raise ChildError(stopped.exit_reason())

    async def _pump_audio(self, shairport: Child) -> None:
        while data := await shairport.stdout.read(_READ_BYTES):
            await self._service.audio(data)

    async def _pump_events(self, pipe: MetadataPipe) -> None:
        while True:
            await self._service.event(await pipe.next_event())
