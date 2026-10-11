"""A process the plugin starts, owns, and takes down with its whole group."""

import asyncio
import logging
import os
import signal
from collections import deque
from typing import Optional

logger = logging.getLogger(__name__)

# Children get only what they need from the server's environment.
_ENVIRONMENT = ("PATH", "LANG", "LC_ALL", "TZ")
_MAX_LOG_LINE = 500


class ChildError(RuntimeError):
    """A child could not start or stopped on its own; the message is fit to show."""


class Child:
    """
    @brief One owned subprocess: its own process group, its stderr in the log.

    stdin and stdout are pipes when asked for and DEVNULL otherwise. The last
    stderr lines are kept to explain an exit.
    """

    def __init__(
        self, name: str, argv: list[str], *, stdin: bool = False, stdout: bool = False
    ):
        self.name = name
        self.argv = argv
        self._pipe_stdin = stdin
        self._pipe_stdout = stdout
        self._process: Optional[asyncio.subprocess.Process] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self.last_errors: deque[str] = deque(maxlen=5)

    @property
    def stdin(self) -> asyncio.StreamWriter:
        return self._process.stdin

    @property
    def stdout(self) -> asyncio.StreamReader:
        return self._process.stdout

    @property
    def returncode(self) -> Optional[int]:
        return self._process.returncode if self._process else None

    async def start(self) -> None:
        pipe, devnull = asyncio.subprocess.PIPE, asyncio.subprocess.DEVNULL
        try:
            self._process = await asyncio.create_subprocess_exec(
                *self.argv,
                stdin=pipe if self._pipe_stdin else devnull,
                stdout=pipe if self._pipe_stdout else devnull,
                stderr=pipe,
                start_new_session=True,
                env={k: os.environ[k] for k in _ENVIRONMENT if k in os.environ},
            )
        except OSError as error:
            raise ChildError(f"{self.name} could not start: {error.strerror}") from None
        self._stderr_task = asyncio.create_task(self._log_stderr())

    async def _log_stderr(self) -> None:
        async for raw in self._process.stderr:
            line = raw.decode("utf-8", errors="replace").rstrip()[:_MAX_LOG_LINE]
            if line:
                self.last_errors.append(line)
                logger.info("%s: %s", self.name, line)

    def exit_reason(self) -> str:
        detail = self.last_errors[-1] if self.last_errors else f"code {self.returncode}"
        return f"{self.name} stopped: {detail}"

    async def wait(self) -> int:
        code = await self._process.wait()
        if self._stderr_task:
            await self._stderr_task
        return code

    async def stop(self, grace_s: float = 2.0) -> None:
        """SIGTERM to the group, then SIGKILL once the grace period is over."""
        if self._process is None or self._process.returncode is not None:
            if self._stderr_task:
                await self._stderr_task
            return
        for sig, wait_s in ((signal.SIGTERM, grace_s), (signal.SIGKILL, None)):
            try:
                os.killpg(self._process.pid, sig)
            except ProcessLookupError:
                break
            try:
                await asyncio.wait_for(asyncio.shield(self._process.wait()), wait_s)
                break
            except TimeoutError:
                continue
        await self.wait()
