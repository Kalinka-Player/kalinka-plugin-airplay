"""Sending play, pause, next and previous back to the AirPlay sender over DACP."""

import ipaddress
import logging
from typing import Optional

import httpx

from .metadata import RemoteChanged

logger = logging.getLogger(__name__)

PLAY, PAUSE, NEXT, PREVIOUS = "play", "pause", "nextitem", "previtem"


class SenderRemote:
    """
    @brief The sender's remote-control endpoint, once its address and token are known.

    Classic AirPlay senders announce one; AirPlay 2 senders do not, and
    `available` then stays False. A failed command is logged and dropped:
    the sender is a phone that may have walked away.
    """

    def __init__(self, client: Optional[httpx.AsyncClient] = None):
        self._client = client or httpx.AsyncClient(timeout=2.0)
        self._base_url: Optional[str] = None
        self._active_remote: Optional[str] = None

    @property
    def available(self) -> bool:
        return self._base_url is not None and self._active_remote is not None

    def update(self, remote: RemoteChanged) -> None:
        self._active_remote = remote.active_remote
        self._base_url = (
            _base_url(remote.client_ip, remote.port)
            if remote.client_ip and remote.port
            else None
        )

    def forget(self) -> None:
        self._base_url = self._active_remote = None

    async def send(self, command: str) -> bool:
        """@return Whether the sender accepted the command."""
        if not self.available:
            return False
        try:
            response = await self._client.get(
                f"{self._base_url}/ctrl-int/1/{command}",
                headers={"Active-Remote": self._active_remote},
            )
        except httpx.HTTPError as error:
            logger.info("AirPlay sender did not take %s: %s", command, type(error).__name__)
            return False
        if response.is_success:
            return True
        logger.info("AirPlay sender refused %s: HTTP %s", command, response.status_code)
        return False

    async def aclose(self) -> None:
        await self._client.aclose()


def _base_url(ip: str, port: int) -> str:
    try:
        host = f"[{ip}]" if ipaddress.ip_address(ip).version == 6 else ip
    except ValueError:
        host = ip
    return f"http://{host}:{port}"
