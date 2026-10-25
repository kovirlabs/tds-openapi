"""Scenario runner: HTTP calls with automatic status/schema checks, recorded for the report."""

import copy
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx

from tds_conform.backpressure import BACKPRESSURE_CODES, backpressure_problems, retry_after_s
from tds_conform.booking import booking_window_for, closes_at, trip_time
from tds_conform.data import example, schema_errors

# Schema each successful response must satisfy, by (operation, status). Error statuses
# always carry Problem Details (core/error). tests/ checks this against spec/openapi.yaml.
RESPONSE_SCHEMAS = {
    ("GET /health", 200): "core/health",
    ("POST /trips", 201): "core/trip",
    ("POST /trips", 202): "core/trip",
    ("GET /trips/{trip_id}", 200): "core/trip",
    ("PATCH /trips/{trip_id}", 200): "core/trip",
    ("PATCH /trips/{trip_id}", 202): "core/trip",
    ("POST /trips/{trip_id}/confirm", 200): "core/trip",
    ("POST /trips/{trip_id}/cancel", 200): "core/trip",
    ("POST /trips/{trip_id}/hold", 201): "transfer/hold",
    ("POST /trips/{trip_id}/hold", 202): "transfer/hold",
    ("GET /trips/{trip_id}/holds/{hold_id}", 200): "transfer/hold",
    ("POST /tenders", 201): "transfer/tender",
    ("POST /tenders", 202): "transfer/tender",
    ("GET /tenders/{tender_id}", 200): "transfer/tender",
    ("GET /subscriptions", 200): "core/subscription-list",
    ("POST /subscriptions", 201): "core/subscription",
    ("GET /subscriptions/{subscription_id}", 200): "core/subscription",
    ("PATCH /subscriptions/{subscription_id}", 200): "core/subscription",
    ("DELETE /subscriptions/{subscription_id}", 200): "core/subscription",
    ("POST /subscriptions/{subscription_id}/secrets", 201): "core/subscription-secret-list",
    ("GET /subscriptions/{subscription_id}/secrets", 200): "core/subscription-secret-list",
    ("DELETE /subscriptions/{subscription_id}/secrets/{secret_id}", 200): "core/subscription-secret",
}
# The relay mailbox (SPEC.md §9.4, spec/relay-mailbox.yaml), served by a requester; tests/ checks it too.
MAILBOX_RESPONSE_SCHEMAS = {
    ("GET /outbox", 200): "csv/mailbox-listing",
    ("POST /ack/{file_name}", 200): "csv/mailbox-ack",
    ("PUT /inbox/{file_name}", 201): "csv/mailbox-receipt",
    ("PUT /inbox/{file_name}", 200): "csv/mailbox-receipt",
}
REQUEST_SCHEMAS = {
    "POST /trips": "core/trip-request",
    "PATCH /trips/{trip_id}": "core/trip-modification",
    "POST /trips/{trip_id}/cancel": "core/cancel-request",
    "POST /trips/{trip_id}/hold": "transfer/hold-request",
    "POST /tenders": "transfer/tender-request",
    "POST /subscriptions": "core/subscription-request",
    "PATCH /subscriptions/{subscription_id}": "core/subscription-update",
    "POST /subscriptions/{subscription_id}/secrets": "core/secret-rotation-request",
}
AUTO_KEY = object()


class Abort(Exception):
    """Stop the scenario: a step it depends on failed."""


class Inconclusive(Exception):
    """Stop the scenario without a verdict, e.g. the provider legitimately refused the test trip."""


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""
    advisory: bool = False  # a SHOULD: reported as a warning, never fails the scenario


@dataclass
class Step:
    label: str
    request: dict
    response: dict | None = None
    checks: list[Check] = field(default_factory=list)

    @property
    def status(self) -> int | None:
        return self.response and self.response["status"]

    @property
    def body(self):
        return self.response and self.response["body"]

    def check(self, passed: bool, name: str, detail: str = "") -> bool:
        self.checks.append(Check(name, bool(passed), "" if passed else detail))
        return bool(passed)

    def advise(self, passed: bool, name: str, detail: str = "") -> bool:
        """Record a SHOULD-level check: a failure is a warning in the report, not a FAIL."""
        self.checks.append(Check(name, bool(passed), "" if passed else detail, advisory=True))
        return bool(passed)

    def require(self, passed: bool, name: str, detail: str = "") -> None:
        if not self.check(passed, name, detail):
            raise Abort(name)

    def check_equal(self, actual, expected, name: str) -> bool:
        return self.check(actual == expected, name, f"expected {expected!r}, got {actual!r}")


@dataclass
class ScenarioResult:
    id: str
    title: str
    outcome: str = "PASS"  # PASS | FAIL | ERROR | INCONCLUSIVE
    steps: list[Step] = field(default_factory=list)
    note: str = ""


@dataclass
class Settings:
    url: str
    token: str
    requester_id: str
    provider_id: str
    trip_template: dict
    max_wait_s: float | None = None  # None: derive from the provider's typical_response_s
    receiver: object | None = None  # tds_conform.receiver.Receiver, for webhook scenarios
    # Back-pressure (SPEC.md §7.2): an unexpected 429/503 is retried with the same Idempotency-Key
    # after the provider's Retry-After, up to this many times; a longer wait makes it INCONCLUSIVE.
    backpressure_retries: int = 3
    max_backpressure_wait_s: float = 30.0
    backpressure_default_wait_s: float = 2.0  # when a 429/503 carries no hint
    # Relay mailbox (SPEC.md §9.4): acknowledge listed request files. Off by default, because an
    # acknowledged file leaves the listing and the provider's real agent would not see it.
    take_files: bool = False


class Context:
    def __init__(self, settings: Settings, result: ScenarioResult, client: httpx.Client):
        self.settings, self.result, self.client = settings, result, client
        self.typical_response_s = 0
        self.health: dict = {}  # the provider's /health body, read once per run
        self.last: httpx.Response | None = None  # the last response, for raw bytes and headers

    def call(
        self,
        label: str,
        method: str,
        route: str,
        *,
        expect: tuple[int, ...],
        body=None,
        key=AUTO_KEY,
        auth: bool = True,
        content: bytes | None = None,
        headers: dict[str, str] | None = None,
        **params: str,
    ) -> Step:
        """Make one request, record it, and check status, media type and response schema.

        A 429 or 503 the scenario does not expect is back-pressure (SPEC.md §7.2): its hint is
        checked (advisory), and the same request is sent again with the same Idempotency-Key once
        the provider's Retry-After has passed. `content` sends raw bytes (a CSV upload) instead of
        a JSON `body`; `headers` adds request headers.
        """
        path = route.format(**params)
        headers = dict(headers or {})
        if auth:
            headers["Authorization"] = f"Bearer {self.settings.token}"
        if key is AUTO_KEY and method in ("POST", "PATCH", "DELETE"):
            key = f"conform-{uuid.uuid4()}"
        if key not in (AUTO_KEY, None):
            headers["Idempotency-Key"] = key
        for attempt in range(self.settings.backpressure_retries + 1):
            step, resp = self._send(label, method, path, headers, body, content)
            if resp.status_code not in BACKPRESSURE_CODES or resp.status_code in expect:
                break
            self._backpressure(step, resp, last=attempt == self.settings.backpressure_retries)
        step.check(resp.status_code in expect, f"status is {' or '.join(map(str, expect))}", f"got {resp.status_code}")
        if resp.status_code >= 400:
            step.check_equal(
                step.response["content_type"],
                "application/problem+json",
                "error media type is application/problem+json",
            )
            schema = "core/error"
        else:
            op = (f"{method} {route}", resp.status_code)
            schema = RESPONSE_SCHEMAS.get(op) or MAILBOX_RESPONSE_SCHEMAS.get(op)
        if schema:
            errors = schema_errors(step.body, schema)
            step.check(not errors, f"body matches {schema}.yaml", "; ".join(errors[:5]))
        if resp.status_code in BACKPRESSURE_CODES:
            self._advise_backpressure(step, resp)
        return step

    def _send(
        self, label: str, method: str, path: str, headers: dict, body, content: bytes | None = None
    ) -> tuple[Step, httpx.Response]:
        request = {"method": method, "path": path, "idempotency_key": headers.get("Idempotency-Key"), "body": body}
        if content is not None:
            request["content_bytes"] = len(content)
        step = Step(label, request)
        self.result.steps.append(step)
        try:
            if content is not None:
                resp = self.client.request(method, path, headers=headers, content=content)
            else:
                resp = self.client.request(method, path, headers=headers, json=body)
        except httpx.HTTPError as exc:
            step.require(False, "request completes", f"{type(exc).__name__}: {exc}")
        self.last = resp
        media = resp.headers.get("content-type", "").split(";")[0].strip()
        try:
            parsed = resp.text if media == "text/csv" else resp.json()
        except ValueError:
            parsed = resp.text
        step.response = {"status": resp.status_code, "content_type": media, "body": parsed}
        if retry_after := resp.headers.get("retry-after"):
            step.response["retry_after"] = retry_after
        if digest := resp.headers.get("x-relay-sha256"):
            step.response["x_relay_sha256"] = digest
        return step, resp

    def _advise_backpressure(self, step: Step, resp: httpx.Response) -> None:
        problems = backpressure_problems(resp.status_code, resp.headers, step.body)
        step.advise(not problems, "back-pressure hint: Retry-After and retry_after_s (SHOULD)", "; ".join(problems))

    def _backpressure(self, step: Step, resp: httpx.Response, last: bool) -> None:
        """Honour an unexpected 429/503: check its hint, then wait, or stop without a verdict."""
        step.label += f" (back-pressure {resp.status_code})"
        step.check_equal(
            step.response["content_type"], "application/problem+json", "error media type is application/problem+json"
        )
        errors = schema_errors(step.body, "core/error")
        step.check(not errors, "body matches core/error.yaml", "; ".join(errors[:5]))
        self._advise_backpressure(step, resp)
        wait = retry_after_s(resp.headers, step.body)
        if wait is None:
            wait = self.settings.backpressure_default_wait_s
        if last:
            raise Inconclusive(
                f"provider still answers {resp.status_code} after "
                f"{self.settings.backpressure_retries} retries; run again later"
            )
        if wait > self.settings.max_backpressure_wait_s:
            raise Inconclusive(
                f"provider asked to wait {wait} s ({resp.status_code}), longer than "
                f"{self.settings.max_backpressure_wait_s:.0f} s; run again later"
            )
        time.sleep(wait)

    @property
    def max_wait_s(self) -> float:
        """How long to wait for an asynchronous decision: 3x the declared typical, at least 30 s."""
        return self.settings.max_wait_s or max(30.0, 3 * self.typical_response_s)

    def poll(self, label: str, route: str, pending: str, deadline_s: float | None = None, **params) -> Step:
        """GET `route` until its status leaves `pending` or the deadline passes; returns the last step."""
        deadline = deadline_s or self.max_wait_s
        interval = min(10.0, max(0.5, self.typical_response_s / 10))
        start = time.monotonic()
        while True:
            step = self.call(label, "GET", route, expect=(200,), **params)
            status = isinstance(step.body, dict) and step.body.get("status")
            if status != pending or time.monotonic() - start > deadline:
                return step
            time.sleep(interval)

    def wait_for_decision(self, trip_id: str) -> Step:
        """Poll GET until the trip leaves REQUESTED, within the provider's declared response time."""
        step = self.poll("poll for decision", "/trips/{trip_id}", "REQUESTED", trip_id=trip_id)
        step.require(
            step.body.get("status") != "REQUESTED",
            f"decision arrives within {self.max_wait_s:.0f}s",
            f"still REQUESTED after {self.max_wait_s:.0f}s (typical_response_s={self.typical_response_s})",
        )
        return step

    def new_trip(self, tender: bool = False, **overrides) -> dict:
        """A fresh trip request from the published example, with a unique id and future dates.

        Dates move by whole days until the provider's advertised booking window (SPEC.md §7.1)
        accepts them; `tender` picks the window for tenders.
        """
        trip = copy.deepcopy(self.settings.trip_template)
        trip["trip_id"] = f"conform-{uuid.uuid4().hex[:12]}"
        trip["requester_id"] = self.settings.requester_id
        trip["provider_id"] = self.settings.provider_id
        _shift_to_future(trip["legs"])
        trip |= overrides
        self.fit_booking_window(trip, tender)
        return trip

    def fit_booking_window(self, trip: dict, tender: bool = False) -> None:
        """Move a trip by whole days (keeping times of day) past the window's notice limits."""
        window = booking_window_for(self.health, trip, tender) if isinstance(self.health, dict) else None
        if not window:
            return
        now = datetime.now(UTC)
        for _ in range(60):
            end = closes_at(window, trip_time(trip))
            if end is None or end >= now + timedelta(minutes=5):
                return
            shift_days(trip["legs"], 1)


def default_trip_template() -> dict:
    return example("single-trip", "01-request-trip.request.json")


def _shift_to_future(legs: list[dict]) -> None:
    """Move every window by whole days so the trip is tomorrow or later, keeping times and offsets."""
    windows = [leg[k] for leg in legs for k in ("pickup_window", "dropoff_window") if k in leg]
    first = min(datetime.fromisoformat(w["earliest"]) for w in windows)
    shift_days(legs, max(0, (date.today() + timedelta(days=1) - first.date()).days))


def shift_days(legs: list[dict], days: int) -> None:
    """Move every pickup and dropoff window by whole days (negative: earlier), keeping times and offsets."""
    shift_windows(legs, timedelta(days=days))


def shift_windows(legs: list[dict], delta: timedelta) -> None:
    """Move every pickup and dropoff window by `delta`, keeping each window's offset."""
    for leg in legs:
        for name in ("pickup_window", "dropoff_window"):
            if window := leg.get(name):
                for k in ("earliest", "preferred", "latest"):
                    if k in window:
                        window[k] = (datetime.fromisoformat(window[k]) + delta).isoformat()


Scenario = Callable[[Context], None]
SCENARIOS: dict[str, tuple[str, str, Scenario]] = {}


def scenario(id: str, profile: str, title: str):  # noqa: A002
    def register(fn: Scenario) -> Scenario:
        SCENARIOS[id] = (profile, title, fn)
        return fn

    return register


def run_scenario(
    id: str,  # noqa: A002
    settings: Settings,
    client: httpx.Client,
    typical_response_s: int,
    health: dict | None = None,
) -> ScenarioResult:
    _, title, fn = SCENARIOS[id]
    result = ScenarioResult(id, title)
    ctx = Context(settings, result, client)
    ctx.typical_response_s = typical_response_s
    ctx.health = health if isinstance(health, dict) else {}
    try:
        fn(ctx)
    except Abort as exc:
        result.note = f"stopped: {exc}"
    except Inconclusive as exc:
        result.outcome, result.note = "INCONCLUSIVE", str(exc)
        return result
    except Exception as exc:  # a bug in the kit or a wildly malformed response
        result.outcome, result.note = "ERROR", f"{type(exc).__name__}: {exc}"
        return result
    if any(not c.passed and not c.advisory for s in result.steps for c in s.checks):
        result.outcome = "FAIL"
    return result
