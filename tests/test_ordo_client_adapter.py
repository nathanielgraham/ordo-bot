from ordo_bot.ordo_client import OrdoClient
from ordo_wsagent import AsyncOrdoClient


def test_adapter_is_async_client():
    c = OrdoClient(url="wss://example/websocket", token="t")
    assert isinstance(c, AsyncOrdoClient)
    assert c.url.endswith("/websocket")
    assert c.token == "t"
    assert not c.is_logged_in
