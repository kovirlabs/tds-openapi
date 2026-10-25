"""Several reference providers in one process, with a ticker and signed webhook delivery."""

import functools
import heapq
import itertools
import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

import httpx

from tds_conform.backpressure import BACKPRESSURE_CODES, retry_after_s
from tds_conform.cloudevents import cloudevents_headers
from tds_conform.events import sign, sign_standard
from tds_conform.mock.mailbox import Mailbox
from tds_conform.mock.provider import Provider, ProviderConfig


class Dispatcher:
    """At-least-once webhook delivery with exponential backoff (SPEC.md §9). A receiver's 429 or
    503 with Retry-After pushes the next attempt back to at least that wait (SPEC.md §7.2), up
    to `max_retry_after_s`."""

    def __init__(
        self,
        retry_base_s: float = 1.0,
        max_attempts: int = 6,
        timeout_s: float = 5.0,
        max_retry_after_s: float = 3600.0,
    ):
        self.retry_base_s, self.max_attempts, self.timeout_s = retry_base_s, max_attempts, timeout_s
        self.max_retry_after_s = max_retry_after_s
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

    def submit(self, subscription: dict, event: dict, secrets: Callable[[], list[str]] | None = None) -> None:
        """Queue one delivery. `secrets` gives the active secrets, newest first, at each attempt
        (SPEC.md §12.1); without it the subscription's `signing_secret` is used. An empty list
        means the subscription has ended (SPEC.md §9.3): the attempt is dropped, not retried."""
        body = json.dumps(event).encode()
        job = (time.monotonic(), next(self._seq), 1, subscription, body, event["event_id"], secrets)
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
                    due, _, attempt, sub, body, event_id, secrets = heapq.heappop(self._queue)
                signing = secrets() if secrets else [sub["signing_secret"]]
                if not signing:  # the subscription has ended (SPEC.md §9.3): no further attempt
                    self.log.append(
                        {
                            "event_id": event_id,
                            "subscription_id": sub["subscription_id"],
                            "attempt": attempt,
                            "outcome": "dropped: subscription ended",
                            "at": datetime.now(UTC).isoformat(timespec="seconds"),
                        }
                    )
                    continue
                outcome, hint = self._send(client, sub, body, signing)
                entry = {
                    "event_id": event_id,
                    "subscription_id": sub["subscription_id"],
                    "attempt": attempt,
                    "outcome": outcome,
                    "at": datetime.now(UTC).isoformat(timespec="seconds"),
                }
                if hint is not None:
                    entry["retry_after_s"] = hint
                self.log.append(entry)
                if not str(outcome).startswith("2") and attempt < self.max_attempts:
                    delay = self.retry_base_s * 2 ** (attempt - 1)
                    if hint is not None:
                        delay = max(delay, min(hint, self.max_retry_after_s))
                    retry_at = time.monotonic() + delay
                    with self._cv:
                        job = (retry_at, next(self._seq), attempt + 1, sub, body, event_id, secrets)
                        heapq.heappush(self._queue, job)

    @staticmethod
    def _send(client: httpx.Client, sub: dict, body: bytes, secrets: list[str]) -> tuple[str, int | None]:
        """One attempt: X-TDS-Signature with the newest secret, a v1 entry per active secret.

        Returns the outcome (status code or error) and, for a 429/503, the receiver's Retry-After."""
        event = json.loads(body)
        sent_at = int(time.time())
        headers = {
            "Content-Type": "application/json",
            **sign(secrets[0], body, sent_at),
            **sign_standard(secrets, event["event_id"], body, sent_at),
        }
        if sub.get("cloudevents"):
            headers.update(cloudevents_headers(event))
        try:
            resp = client.post(sub["callback_url"], content=body, headers=headers)
        except httpx.HTTPError as exc:
            return f"error: {type(exc).__name__}", None
        hint = retry_after_s(resp.headers) if resp.status_code in BACKPRESSURE_CODES else None
        return str(resp.status_code), hint


class Network:
    """Providers keyed by provider_id; the first is the default for un-prefixed URLs.

    Each provider also has a requester-side relay mailbox (SPEC.md §9.4) at /mailbox, keyed
    by the token that names it (the provider id); pass `mailboxes` to use others."""

    def __init__(
        self,
        configs: list[ProviderConfig],
        clock: Callable[[], datetime] | None = None,
        tick_s: float = 0.25,
        dispatcher: Dispatcher | None = None,
        mailboxes: list[Mailbox] | None = None,
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
        self._add_mailboxes(mailboxes, clock)

    @classmethod
    def of(
        cls,
        *providers: Provider,
        tick_s: float = 0.25,
        dispatcher: Dispatcher | None = None,
        mailboxes: list[Mailbox] | None = None,
    ) -> "Network":
        """Wrap already-built providers (e.g. ones with a fake clock or a fault injected)."""
        network = cls.__new__(cls)
        network.dispatcher, network.tick_s, network._stop = dispatcher or Dispatcher(), tick_s, threading.Event()
        network.providers = {}
        for provider in providers:
            provider.sink = network._sink_for(provider)
            network.providers[provider.provider_id] = provider
        network.default = providers[0]
        network._add_mailboxes(mailboxes, providers[0].clock)
        return network

    def _add_mailboxes(self, mailboxes: list[Mailbox] | None, clock: Callable[[], datetime] | None) -> None:
        if mailboxes is None:
            mailboxes = [Mailbox(pid) if clock is None else Mailbox(pid, clock=clock) for pid in self.providers]
        self.mailboxes: dict[str, Mailbox] = {m.provider_id: m for m in mailboxes}

    def _sink_for(self, provider: Provider):
        def sink(requester: str, event: dict) -> None:
            for sub in provider.subscriptions_for(requester, event["event_type"]):
                self.dispatcher.submit(sub, event, functools.partial(provider.signing_secrets, sub))

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
