"""Several reference providers in one process, with a ticker and signed webhook delivery."""

import heapq
import itertools
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from tds_conform.cloudevents import cloudevents_headers
from tds_conform.events import sign, sign_standard
from tds_conform.mock.provider import Provider, ProviderConfig


class Dispatcher:
    """At-least-once webhook delivery with exponential backoff (SPEC.md §9)."""

    def __init__(self, retry_base_s: float = 1.0, max_attempts: int = 6, timeout_s: float = 5.0):
        self.retry_base_s, self.max_attempts, self.timeout_s = retry_base_s, max_attempts, timeout_s
        self.log: list[dict] = []
        self._queue: list = []
        self._seq = itertools.count()
        self._cv = threading.Condition()
        self._stopped = False
        self._thread = threading.Thread(target=self._run, name="tds-mock-webhooks", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        with self._cv:
            self._stopped = True
            self._cv.notify()

    def submit(self, subscription: dict, event: dict) -> None:
        job = (time.monotonic(), next(self._seq), 1, subscription, json.dumps(event).encode(), event["event_id"])
        with self._cv:
            heapq.heappush(self._queue, job)
            self._cv.notify()

    def _run(self) -> None:
        with httpx.Client(timeout=self.timeout_s) as client:
            while True:
                with self._cv:
                    while not self._stopped and (not self._queue or self._queue[0][0] > time.monotonic()):
                        self._cv.wait(timeout=None if not self._queue else self._queue[0][0] - time.monotonic())
                    if self._stopped:
                        return
                    due, _, attempt, sub, body, event_id = heapq.heappop(self._queue)
                outcome = self._send(client, sub, body)
                self.log.append(
                    {
                        "event_id": event_id,
                        "subscription_id": sub["subscription_id"],
                        "attempt": attempt,
                        "outcome": outcome,
                        "at": datetime.now(UTC).isoformat(timespec="seconds"),
                    }
                )
                if not str(outcome).startswith("2") and attempt < self.max_attempts:
                    retry_at = time.monotonic() + self.retry_base_s * 2 ** (attempt - 1)
                    with self._cv:
                        heapq.heappush(self._queue, (retry_at, next(self._seq), attempt + 1, sub, body, event_id))

    @staticmethod
    def _send(client: httpx.Client, sub: dict, body: bytes) -> str:
        event = json.loads(body)
        sent_at = int(time.time())
        headers = {
            "Content-Type": "application/json",
            **sign(sub["signing_secret"], body, sent_at),
            **sign_standard(sub["signing_secret"], event["event_id"], body, sent_at),
        }
        if sub.get("cloudevents"):
            headers.update(cloudevents_headers(event))
        try:
            return str(client.post(sub["callback_url"], content=body, headers=headers).status_code)
        except httpx.HTTPError as exc:
            return f"error: {type(exc).__name__}"


class Network:
    """Providers keyed by provider_id; the first is the default for un-prefixed URLs."""

    def __init__(
        self,
        configs: list[ProviderConfig],
        clock: Callable[[], datetime] | None = None,
        tick_s: float = 0.25,
        dispatcher: Dispatcher | None = None,
    ):
        self.dispatcher = dispatcher or Dispatcher()
        self.tick_s = tick_s
        self.providers: dict[str, Provider] = {}
        for config in configs:
            provider = Provider(config) if clock is None else Provider(config, clock=clock)
            provider.sink = self._sink_for(provider)
            self.providers[provider.provider_id] = provider
        self.default = next(iter(self.providers.values()))
        self._stop = threading.Event()

    @classmethod
    def of(cls, *providers: Provider, tick_s: float = 0.25, dispatcher: Dispatcher | None = None) -> "Network":
        """Wrap already-built providers (e.g. ones with a fake clock or a fault injected)."""
        network = cls.__new__(cls)
        network.dispatcher, network.tick_s, network._stop = dispatcher or Dispatcher(), tick_s, threading.Event()
        network.providers = {}
        for provider in providers:
            provider.sink = network._sink_for(provider)
            network.providers[provider.provider_id] = provider
        network.default = providers[0]
        return network

    def _sink_for(self, provider: Provider):
        def sink(requester: str, event: dict) -> None:
            for sub in provider.subscriptions_for(requester, event["event_type"]):
                self.dispatcher.submit(sub, event)

        return sink

    def start(self) -> None:
        self.dispatcher.start()
        threading.Thread(target=self._tick, name="tds-mock-ticker", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self.dispatcher.stop()

    def _tick(self) -> None:
        while not self._stop.wait(self.tick_s):
            for provider in self.providers.values():
                provider.tick()
