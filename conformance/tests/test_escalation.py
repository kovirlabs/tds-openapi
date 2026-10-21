"""ESCALATED exceptions (SPEC.md §10.6): schema, stream rules, and the kit catching a provider that sends one."""

import copy

import pytest

import tds_conform.scenarios  # noqa: F401  (registers scenarios)
from tds_conform.data import schema_errors
from tds_conform.events import broker_event_problems, escalation_problems
from tds_conform.examples import load_example
from tds_conform.mock.provider import Provider
from tds_conform.receiver import Receiver
from tds_conform.runner import run_scenario

ESCALATION = {
    "exception_id": "ex-9",
    "severity": "AT_RISK",
    "type": "ESCALATED",
    "escalates_exception_id": "ex-1",
    "escalation_level": 1,
    "detected_at": "2026-11-10T09:10:00-05:00",
}


@pytest.mark.parametrize(
    "change",
    [
        lambda x: x.pop("escalates_exception_id"),
        lambda x: x.update(severity="INFO"),
        lambda x: x.update(escalation_level=0),
        lambda x: x.update(type="HOLD_DENIED"),  # escalation fields on a condition exception
    ],
)
def test_schema_refuses(change):
    exception = copy.deepcopy(ESCALATION)
    change(exception)
    assert schema_errors(exception, "transfer/exception")


def test_schema_accepts_escalation():
    assert schema_errors(ESCALATION, "transfer/exception") == []
    assert schema_errors(ESCALATION | {"severity": "CRITICAL", "escalation_level": 3}, "transfer/exception") == []


def stream() -> list[dict]:
    return copy.deepcopy(load_example("exceptions", "events.json"))


def raised(event_id: str, occurred_at: str, exception: dict, provider_id: str = "throughride-demo") -> dict:
    return {
        "event_id": event_id,
        "event_type": "EXCEPTION_RAISED",
        "occurred_at": occurred_at,
        "provider_id": provider_id,
        "schema_version": "0.5.1",
        "payload": {"exception": exception},
    }


def test_example_stream_is_clean():
    events = stream()
    assert broker_event_problems(events) == [] and escalation_problems(events) == []


@pytest.mark.parametrize(
    ("extra", "problem"),
    [
        (ESCALATION | {"escalates_exception_id": "ex-never"}, "has not raised before"),
        (ESCALATION | {"escalates_exception_id": "ex-rt-2-esc-2", "escalation_level": 3}, "another ESCALATED"),
        (ESCALATION | {"escalates_exception_id": "ex-rt-2", "escalation_level": 2}, "does not rise above 2"),
    ],
)
def test_escalation_rules(extra, problem):
    events = stream() + [raised("evt-x", "2026-11-10T10:00:00-05:00", extra)]
    assert any(problem in p for p in escalation_problems(events))


def test_escalation_must_be_resolved_with_its_condition():
    events = [e for e in stream() if e["event_id"] != "evt-ex-rp-1-esc-1-resolved"]
    assert any("ex-rp-1 was resolved but its escalation was not" in p for p in escalation_problems(events))


def test_providers_never_send_exception_events():
    provider_stream = load_example("two-provider-transfer", "events.json")
    leak = raised("evt-leak", "2026-11-10T09:00:00-05:00", ESCALATION, provider_id="aaata-a-ride")
    assert any("aaata-a-ride" in p and "ESCALATED" in p for p in broker_event_problems(provider_stream + [leak]))


class EscalatingProvider(Provider):
    """A provider that sends EXCEPTION_RAISED (ESCALATED) to everyone, subscribed or not."""

    def subscriptions_for(self, requester, event_type):
        return [sub for owner, sub in self._subscriptions.values() if owner == requester]

    def _create_trip(self, requester, body):
        trip = super()._create_trip(requester, body)
        exception = ESCALATION | {"escalates_exception_id": "ex-0", "exception_id": f"ex-{trip.body['trip_id']}"}
        self._emit(trip, "EXCEPTION_RAISED", {"exception": exception})
        return trip


def test_kit_catches_a_provider_sending_exception_events(serve):
    settings, client = serve(EscalatingProvider())
    with Receiver() as receiver:
        settings.receiver = receiver
        result = run_scenario("webhook_delivery", settings, client, 0)
    assert result.outcome == "FAIL", result.note
    failed = {c.name for s in result.steps for c in s.checks if not c.passed}
    assert "provider sends no broker events (EXCEPTION_RAISED, EXCEPTION_RESOLVED)" in failed
