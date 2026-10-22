"""The kit end to end against the reference provider over HTTP, including that it catches faults."""

import json

import pytest

from tds_conform import report
from tds_conform.cli import conform_main
from tds_conform.mock.provider import Provider, ProviderConfig
from tds_conform.runner import run_scenario


@pytest.mark.parametrize("mode", ["sync_provider", "async_provider"])
@pytest.mark.parametrize("scenario_id", ["book_confirm_cancel", "idempotency"])
def test_reference_provider_passes(request, serve, mode, scenario_id):
    provider = request.getfixturevalue(mode)
    settings, client = serve(provider)
    typical = provider.health()[1].get("typical_response_s", 0)
    result = run_scenario(scenario_id, settings, client, typical)
    failures = [(s.label, c.name, c.detail) for s in result.steps for c in s.checks if not c.passed]
    assert result.outcome == "PASS", (result.note, failures)


class NoIdempotency(Provider):
    """Forgets keys: every request is handled fresh."""

    def _idempotent(self, requester, key, operation, body, handler):
        if not key:
            return 400, {"type": "about:blank", "title": "x", "status": 400, "code": "INVALID_REQUEST"}
        with self._lock:
            return handler()


class CancelTwice(Provider):
    """Lets terminal trips be cancelled again."""

    def _cancel(self, requester, trip_id, body):
        trip = self._find(requester, trip_id)
        if trip and trip.body["status"] == "CANCELLED":
            return 200, trip.body
        return super()._cancel(requester, trip_id, body)


@pytest.mark.parametrize(
    ("provider", "scenario_id", "failing_check"),
    [
        (NoIdempotency(), "idempotency", "replay returns the original response body"),
        (CancelTwice(), "book_confirm_cancel", "code is INVALID_STATE"),
    ],
)
def test_kit_catches_faults(serve, provider, scenario_id, failing_check):
    settings, client = serve(provider)
    result = run_scenario(scenario_id, settings, client, 0)
    assert result.outcome == "FAIL"
    assert failing_check in {c.name for s in result.steps for c in s.checks if not c.passed}


def test_refused_test_trip_is_inconclusive(serve):
    settings, client = serve(Provider(ProviderConfig(unsupported_requirements=frozenset({"WHEELCHAIR_LIFT"}))))
    assert run_scenario("book_confirm_cancel", settings, client, 0).outcome == "INCONCLUSIVE"


def test_cli_writes_reports(serve, sync_provider, tmp_path, capsys):
    settings, _ = serve(sync_provider)
    assert conform_main(["run", "--url", settings.url, "--report-dir", str(tmp_path)]) == 0
    data = json.loads((tmp_path / "report.json").read_text())
    assert data["conformant"] and data["summary"]["PASS"] == 4
    assert "Conformant: Core" in (tmp_path / "report.html").read_text()


def test_html_escapes_provider_content():
    from tds_conform.runner import Check, ScenarioResult, Step

    step = Step(
        "x",
        {"method": "GET", "path": "/<script>"},
        {"status": 200, "body": "<img onerror=1>"},
        [Check("c", False, "<b>")],
    )
    built = report.build(
        {"profile": "core", "target": "t", "spec_version": "s", "kit_version": "k", "started_at": "now"},
        [ScenarioResult("s", "t", "FAIL", [step])],
    )
    html = report.render_html(built)
    assert "<script>" not in html and "<img" not in html and "&lt;b&gt;" in html


@pytest.mark.parametrize("mode", ["sync_provider", "async_provider"])
@pytest.mark.parametrize("scenario_id", ["tender_deadline", "hold_request", "webhook_delivery"])
def test_reference_provider_passes_v04_scenarios(request, serve, mode, scenario_id):
    from tds_conform.receiver import Receiver

    provider = request.getfixturevalue(mode)
    settings, client = serve(provider)
    typical = provider.health()[1].get("typical_response_s", 0)
    with Receiver() as receiver:
        settings.receiver = receiver
        result = run_scenario(scenario_id, settings, client, typical)
    failures = [(s.label, c.name, c.detail) for s in result.steps for c in s.checks if not c.passed]
    assert result.outcome == "PASS", (result.note, failures)


class StuckTenders(Provider):
    """Never answers a tender and never lets it expire."""

    def _tick_transfer(self):
        pass

    def _create_tender(self, requester, body):
        self.config.tender_decision = "IGNORE"
        return super()._create_tender(requester, body)


class HoldWithoutMoving(Provider):
    """Grants holds but leaves the trip's pickup time unchanged."""

    def _decide_hold(self, state):
        state["hold"].update(
            status="GRANTED",
            scheduled_pickup=state["hold"]["requested_until"],
            responded_at=state["hold"]["created_at"],
        )


class UnsignedWebhooks(Provider):
    """Delivers events signed with the wrong secret."""

    def _subscribe(self, requester, body):
        status, sub = super()._subscribe(requester, body)
        self._subscriptions[sub["subscription_id"]] = (requester, sub | {"signing_secret": "wrong-secret-0123456789"})
        return status, sub


@pytest.mark.parametrize(
    ("provider", "scenario_id", "failing_check"),
    [
        (HoldWithoutMoving(), "hold_request", "trip scheduled_pickup matches the hold"),
        (UnsignedWebhooks(), "webhook_delivery", "every delivery is signed (X-TDS-Signature, X-TDS-Timestamp)"),
    ],
)
def test_kit_catches_v04_faults(serve, provider, scenario_id, failing_check):
    from tds_conform.receiver import Receiver

    settings, client = serve(provider)
    with Receiver() as receiver:
        settings.receiver = receiver
        result = run_scenario(scenario_id, settings, client, 0)
    assert result.outcome == "FAIL", result.note
    assert failing_check in {c.name for s in result.steps for c in s.checks if not c.passed}


def test_kit_catches_stuck_tenders(serve, monkeypatch):
    from tds_conform.scenarios import transfer

    monkeypatch.setattr(transfer, "GRACE_S", 1)
    monkeypatch.setattr(
        transfer,
        "_deadline",
        lambda ctx: (
            __import__("datetime").datetime.now(__import__("datetime").UTC)
            + __import__("datetime").timedelta(seconds=1)
        ),
    )
    settings, client = serve(StuckTenders())
    result = run_scenario("tender_deadline", settings, client, 0)
    assert result.outcome == "FAIL"
    assert "answered or EXPIRED by respond_by" in {c.name for s in result.steps for c in s.checks if not c.passed}


def test_check_events_cli(tmp_path, capsys):
    from tds_conform.data import data_root

    folder = data_root() / "examples" / "supervised-rider-custody-handoff"
    args = ["check-events", str(folder / "events.json")]
    for name in ("01-tender-feeder.request.json", "03-tender-onward.request.json"):
        args += ["--interchange", str(folder / name)]
    assert conform_main(args) == 0
    events = [e for e in json.loads((folder / "events.json").read_text()) if e["event_type"] != "CUSTODY_HANDOFF"]
    (tmp_path / "no-custody.json").write_text(json.dumps(events))
    args[1] = str(tmp_path / "no-custody.json")
    assert conform_main(args) == 1
    assert "no custody handoff at pickup" in capsys.readouterr().out
