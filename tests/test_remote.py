import httpx
import pytest

from kalinka_plugin_airplay.metadata import RemoteChanged
from kalinka_plugin_airplay.remote import PAUSE, SenderRemote


def remote_with(handler):
    requests = []

    def record(request):
        requests.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return SenderRemote(client), requests


async def test_a_command_carries_the_senders_token():
    remote, requests = remote_with(lambda r: httpx.Response(204))
    remote.update(RemoteChanged("192.168.1.20", 52341, "1234567"))
    assert remote.available
    assert await remote.send(PAUSE)
    [request] = requests
    assert str(request.url) == "http://192.168.1.20:52341/ctrl-int/1/pause"
    assert request.headers["Active-Remote"] == "1234567"


async def test_an_ipv6_sender():
    remote, requests = remote_with(lambda r: httpx.Response(204))
    remote.update(RemoteChanged("fe80::1", 3689, "1"))
    await remote.send(PAUSE)
    assert requests[0].url.host == "fe80::1"


@pytest.mark.parametrize(
    "remote_changed",
    [
        RemoteChanged("192.168.1.20", None, "1234567"),
        RemoteChanged("192.168.1.20", 52341, None),
        RemoteChanged(None, 52341, "1234567"),
    ],
)
async def test_nothing_is_sent_without_a_full_address(remote_changed):
    remote, requests = remote_with(lambda r: httpx.Response(204))
    remote.update(remote_changed)
    assert not remote.available
    assert not await remote.send(PAUSE)
    assert requests == []


async def test_a_refusal_or_an_absent_sender_is_not_an_error():
    remote, _ = remote_with(lambda r: httpx.Response(403))
    remote.update(RemoteChanged("192.168.1.20", 52341, "1"))
    assert not await remote.send(PAUSE)

    def unreachable(request):
        raise httpx.ConnectError("no route")

    remote, _ = remote_with(unreachable)
    remote.update(RemoteChanged("192.168.1.20", 52341, "1"))
    assert not await remote.send(PAUSE)


async def test_forgetting_the_sender():
    remote, _ = remote_with(lambda r: httpx.Response(204))
    remote.update(RemoteChanged("192.168.1.20", 52341, "1"))
    remote.forget()
    assert not remote.available
