"""Back-pressure (SPEC.md §7.2): Retry-After / retry_after_s parsing, tds-mock's 429 and 503,
the kit honouring them, and webhook senders honouring a receiver's Retry-After."""

import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from tds_conform.backpressure import backpressure_problems, parse_retry_after, retry_after_s
from tds_conform.data import schema_errors
from tds_conform.events import new_standard_secret
from tds_conform.mock.common import problem
from tds_conform.mock.network import Dispatcher
from tds_conform.mock.provider import Provider, ProviderConfig
from tds_conform.runner import run_scenario
from tds_conform.scenarios import core as _core_scenarios  # noqa: F401  (registers scenarios)

NOW = datetime(2026, 10, 24, 14, 0, 0, tzinfo=UTC)
AUTH = {"Authorization": "Bearer throughride-demo"}


# --- parsing ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("20", 20),
        (" 0 ", 0),
        ("Sat, 24 Oct 2026 14:02:00 GMT", 120),
        ("Sat, 24 Oct 2026 13:00:00 GMT", 0),  # in the past
        ("-5", None),
        ("1.5", None),
        ("soon", None),
        (None, None),
    ],
)
def test_parse_retry_after(value, expected):
    assert parse_retry_after(value, NOW) == expected


def test_retry_after_s_takes_the_larger_hint_and_ignores_bad_fields():
    assert retry_after_s({"retry-after": "5"}, {"retry_after_s": 30}) == 30
    assert retry_after_s({"Retry-After": "45"}, {"retry_after_s": 30}) == 45
    assert retry_after_s({}, {"retry_after_s": 7}) == 7
    assert retry_after_s(None, {"retry_after_s": True}) is None
    assert retry_after_s(None, {"retry_after_s": -1}) is None
    assert retry_after_s({}, "not json") is None


def test_backpressure_problems():
    good = {"code": "RATE_LIMITED", "retry_after_s": 20}
    assert backpressure_problems(429, {"Retry-After": "20"}, good) == []
    assert backpressure_problems(503, {"Retry-After": "20"}, good | {"code": "TEMPORARILY_UNAVAILABLE"}) == []
    assert backpressure_problems(422, {}, {"code": "NO_CAPACITY"}) == []
    assert backpressure_problems(429, {}, good) == ["429 without a Retry-After header"]
    assert backpressure_problems(429, {"Retry-After": "later"}, good) == [
        "Retry-After 'later' is neither delta-seconds nor an HTTP-date"
    ]
    assert backpressure_problems(429, {"Retry-After": "60"}, good) == [
        "Retry-After (60 s) and retry_after_s (20 s) disagree"
    ]
    assert backpressure_problems(503, {"Retry-After": "20"}, good) == [
        "503 with code 'RATE_LIMITED', expected TEMPORARILY_UNAVAILABLE"
    ]
    assert "429 without retry_after_s in the Problem Details" in backpressure_problems(
        429, {"Retry-After": "20"}, {"code": "RATE_LIMITED"}
    )


# --- tds-mock ----------------------------------------------------------------------------


def test_rate_limit_answers_429_and_the_retry_is_a_first_attempt(clock):
    provider = Provider(ProviderConfig(rate_limit={"requests": 2, "per_s": 60}), clock=clock)
    assert provider.admit("a") is None
    clock.advance(10)
    assert provider.admit("a") is None
    status, body = provider.admit("a")
    assert status == 429 and body["code"] == "RATE_LIMITED" and body["retry_after_s"] == 50
    assert not schema_errors(body, "core/error")
    assert provider.admit("b") is None, "limits are per requester"
    assert provider.admit(None) is None, "unauthenticated /health is not rate limited"
    clock.advance(50)
    assert provider.admit("a") is None


def test_mock_sends_retry_after_on_429_and_nothing_is_processed(serve):
    provider = Provider(ProviderConfig(rate_limit={"requests": 1, "per_s": 60}))
    settings, client = serve(provider)
    trip = {"trip_id": "t-bp"}  # never reaches validation
    assert client.get("/trips/x", headers=AUTH).status_code == 404
    resp = client.post("/trips", json=trip, headers=AUTH | {"Idempotency-Key": "k-backpressure"})
    assert resp.status_code == 429
    assert resp.headers["content-type"] == "application/problem+json"
    assert backpressure_problems(429, resp.headers, resp.json()) == []
    assert not provider._idempotency, "a refused call must not be stored under its key"


def test_sim_unavailable_answers_503_everywhere_until_it_ends(serve):
    settings, client = serve(Provider(ProviderConfig()))
    assert client.post("/_sim/mock-provider/unavailable", json={"for_s": 120}).status_code == 200
    for resp in (client.get("/health"), client.get("/trips/x", headers=AUTH)):
        assert resp.status_code == 503
        assert backpressure_problems(503, resp.headers, resp.json()) == []
        assert 119 <= int(resp.headers["Retry-After"]) <= 120
    assert client.post("/_sim/mock-provider/unavailable", json={"for_s": 0}).json() == {"unavailable_until": None}
    assert client.get("/health").status_code == 200
    assert client.post("/_sim/mock-provider/unavailable", json={"for_s": "soon"}).status_code == 400


def test_rate_limit_policy_is_validated(serve):
    settings, client = serve(Provider(ProviderConfig()))
    assert client.post("/_sim/mock-provider/policy", json={"rate_limit": {"requests": 0}}).status_code == 400
    ok = client.post("/_sim/mock-provider/policy", json={"rate_limit": {"requests": 5, "per_s": 1}})
    assert ok.status_code == 200 and ok.json()["rate_limit"] == {"requests": 5, "per_s": 1}


# --- the kit -----------------------------------------------------------------------------


class BusyOnce(Provider):
    """Answers the first authenticated call with a 503, then works; `hint` controls the wait."""

    def __init__(self, hint: int | None = 1, **kwargs):
        super().__init__(**kwargs)
        self.hint, self.refused = hint, 0

    def admit(self, requester):
        if requester is None or self.refused:
            return None
        self.refused += 1
        status, body = problem(503, "TEMPORARILY_UNAVAILABLE", "busy")
        return status, body if self.hint is None else body | {"retry_after_s": self.hint}


def _advisory(result):
    return [c for s in result.steps for c in s.checks if c.advisory]


def test_kit_waits_and_retries_with_the_same_key(serve):
    settings, client = serve(BusyOnce(hint=1))
    started = time.monotonic()
    result = run_scenario("idempotency", settings, client, 0)
    assert result.outcome == "PASS", result.note
    assert time.monotonic() - started >= 1
    throttled = [s for s in result.steps if "back-pressure 503" in s.label]
    assert len(throttled) == 1
    retried = result.steps[result.steps.index(throttled[0]) + 1]
    assert retried.request["idempotency_key"] == throttled[0].request["idempotency_key"]
    assert [c.passed for c in _advisory(result)] == [True]


def test_missing_hint_is_a_warning_not_a_failure(serve):
    settings, client = serve(BusyOnce(hint=None))
    settings.backpressure_default_wait_s = 0.1
    result = run_scenario("idempotency", settings, client, 0)
    assert result.outcome == "PASS", result.note
    warnings = [c for c in _advisory(result) if not c.passed]
    assert len(warnings) == 1 and "without a Retry-After header" in warnings[0].detail


def test_long_waits_are_inconclusive(serve):
    settings, client = serve(BusyOnce(hint=3600))
    result = run_scenario("idempotency", settings, client, 0)
    assert result.outcome == "INCONCLUSIVE" and "3600" in result.note


def test_report_counts_warnings(serve):
    from tds_conform import report

    settings, client = serve(BusyOnce(hint=None))
    settings.backpressure_default_wait_s = 0.1
    result = run_scenario("idempotency", settings, client, 0)
    built = report.build({"profile": "core"}, [result])
    assert built["conformant"] and built["summary"]["warnings"] == 1


# --- webhook senders ---------------------------------------------------------------------


def test_dispatcher_waits_for_a_receivers_retry_after():
    arrivals: list[float] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            arrivals.append(time.monotonic())
            if len(arrivals) == 1:
                self.send_response(503)
                self.send_header("Retry-After", "1")
            else:
                self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    dispatcher = Dispatcher(retry_base_s=0.01)
    dispatcher.start()
    try:
        sub = {
            "subscription_id": "sub-1",
            "callback_url": f"http://127.0.0.1:{server.server_address[1]}/events",
            "signing_secret": new_standard_secret(),
        }
        dispatcher.submit(sub, {"event_id": "ev-1", "event_type": "TRIP_STATUS_CHANGED"})
        deadline = time.monotonic() + 5
        while len(arrivals) < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        dispatcher.stop()
        server.shutdown()
    assert len(arrivals) == 2
    assert arrivals[1] - arrivals[0] >= 0.95
    assert dispatcher.log[0]["outcome"] == "503" and dispatcher.log[0]["retry_after_s"] == 1
    assert dispatcher.log[1]["outcome"] == "204"


def test_httpx_headers_are_read_case_insensitively():
    resp = httpx.Response(429, headers={"retry-after": "3"}, json={"code": "RATE_LIMITED", "retry_after_s": 3})
    assert retry_after_s(resp.headers, resp.json()) == 3
