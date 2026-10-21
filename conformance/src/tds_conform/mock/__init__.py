"""Stateful reference providers (`tds-mock`) for developing and testing requesters.

Public names for projects that run the mock in-process (e.g. a broker's integration tests):

    from tds_conform.mock import Network, Provider, ProviderConfig, make_server

`Provider(config, clock=...)` is one provider; call its methods directly with a fake clock,
or wrap several in a `Network` and serve them with `make_server(network, port=0)`
(base URLs `/p/<provider_id>`, plus `/_sim/...` controls). See docs/demo.md.
"""

from tds_conform.mock.network import Network
from tds_conform.mock.provider import Provider, ProviderConfig
from tds_conform.mock.server import make_server

__all__ = ["Network", "Provider", "ProviderConfig", "make_server"]
