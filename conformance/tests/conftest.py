import threading
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from tds_conform.mock.network import Dispatcher, Network
from tds_conform.mock.provider import Provider, ProviderConfig
from tds_conform.mock.server import make_server
from tds_conform.runner import Settings, default_trip_template


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds: float):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def serve():
    """Start a provider on a free port; yields a function returning (settings, httpx client)."""
    servers = []

    def start(*providers: Provider, tick_s: float = 0.05):
        network = Network.of(*providers, tick_s=tick_s, dispatcher=Dispatcher(retry_base_s=0.05))
        network.start()
        server = make_server(network, port=0, quiet=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append((server, network))
        url = f"http://127.0.0.1:{server.server_address[1]}"
        template = default_trip_template()
        settings = Settings(
            url, template["requester_id"], template["requester_id"], template["provider_id"], template, max_wait_s=5
        )
        return settings, httpx.Client(base_url=url, timeout=5)

    yield start
    for server, network in servers:
        server.shutdown()
        network.stop()


@pytest.fixture
def sync_provider():
    return Provider(ProviderConfig())


@pytest.fixture
def async_provider():
    return Provider(ProviderConfig(decision="async", decision_delay_s=0.3, schedule_delay_s=0.1))
