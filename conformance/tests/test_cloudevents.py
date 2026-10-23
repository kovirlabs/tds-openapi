"""CloudEvents binary-mode rendering of webhook deliveries (SPEC.md §9.1)."""

import pytest

from tds_conform import cloudevents
from tds_conform.cloudevents import (
    cloudevents_headers,
    cloudevents_problems,
    dedupe_key,
    event_type_from_ce,
    header_value,
    parse_header_value,
    render,
)
from tds_conform.events import dedupe
from tds_conform.mock.provider import Provider
from tds_conform.runner import run_scenario

EVENT = {
    "event_id": "evt-0001",
    "event_type": "TRIP_STATUS_CHANGED",
    "occurred_at": "2026-11-03T09:05:00-05:00",
    "provider_id": "aaata-a-ride",
    "trip_id": "tr-20261103-0001",
    "leg_id": "leg-1",
    "schema_version": "0.6.0",
    "payload": {"status": "IN_PROGRESS", "previous_status": "SCHEDULED"},
}


def test_render_maps_the_envelope():
    assert render(EVENT) == {
        "specversion": "1.0",
        "id": "evt-0001",
        "source": "urn:tds:provider:aaata-a-ride",
        "type": "org.tds-openapi.trip_status_changed",
        "time": "2026-11-03T09:05:00-05:00",
        "subject": "trips/tr-20261103-0001/legs/leg-1",
        "dataschema": "https://tds-openapi.org/schemas/0.6.0/core/event-trip-status-changed.yaml",
        "providerid": "aaata-a-ride",
        "schemaversion": "0.6.0",
    }


def test_extension_names_are_legal_cloudevents_attribute_names():
    # CloudEvents 1.0.2: lower-case a-z and 0-9 only, SHOULD be at most 20 characters.
    for name in cloudevents.ATTRIBUTES:
        assert name.isascii() and name.isalnum() and name == name.lower() and len(name) <= 20


def test_subject_without_leg_and_without_trip():
    assert render(EVENT | {"leg_id": None})["subject"] == "trips/tr-20261103-0001"
    no_trip = {k: v for k, v in EVENT.items() if k not in ("trip_id", "leg_id")}
    assert "subject" not in render(no_trip)


def test_transfer_events_point_at_their_profile_schema():
    milestone = EVENT | {"event_type": "MILESTONE"}
    assert render(milestone)["dataschema"].endswith("/0.6.0/transfer/event-milestone.yaml")
    assert render(milestone)["type"] == "org.tds-openapi.milestone"
    assert event_type_from_ce("org.tds-openapi.milestone") == "MILESTONE"
    assert event_type_from_ce("com.example.milestone") is None


def test_ids_are_uri_encoded_and_header_values_percent_encoded():
    odd = EVENT | {"provider_id": "Bus Co/1", "trip_id": "tr é"}
    attrs = render(odd)
    assert attrs["source"] == "urn:tds:provider:Bus%20Co%2F1"
    assert attrs["subject"] == "trips/tr%20%C3%A9/legs/leg-1"
    headers = cloudevents_headers(odd)
    assert headers["ce-providerid"] == "Bus%20Co/1"
    assert parse_header_value(headers["ce-providerid"]) == "Bus Co/1"
    assert header_value('a"b%c') == "a%22b%25c"
    assert all(v.isascii() and " " not in v for v in headers.values())


def test_problems_none_without_ce_headers_and_none_when_correct():
    assert cloudevents_problems({"Content-Type": "application/json"}, EVENT) == []
    headers = {"Content-Type": "application/json", **cloudevents_headers(EVENT)}
    assert cloudevents_problems(headers, EVENT) == []
    upper = {k.upper(): v for k, v in headers.items()}
    assert cloudevents_problems(upper, EVENT) == []


def test_problems_flag_wrong_missing_and_structured_attributes():
    headers = {"Content-Type": "application/json", **cloudevents_headers(EVENT)}
    wrong = headers | {"ce-type": "org.tds-openapi.milestone"}
    assert any("ce-type" in p for p in cloudevents_problems(wrong, EVENT))
    missing = {k: v for k, v in headers.items() if k != "ce-source"}
    assert "missing ce-source" in cloudevents_problems(missing, EVENT)
    extra = headers | {"ce-datacontenttype": "application/json"}
    assert any("datacontenttype" in p for p in cloudevents_problems(extra, EVENT))


def test_dedupe_is_per_source_and_id():
    other = EVENT | {"provider_id": "smart-flex"}
    assert dedupe_key(EVENT) != dedupe_key(other)
    assert len(dedupe([EVENT, other, dict(EVENT)])) == 2


class ClaimsCloudEvents(Provider):
    """Says cloudevents: true but delivers without ce-* headers."""

    def _subscribe(self, requester, body):
        status, sub = super()._subscribe(requester, body)
        self._subscriptions[sub["subscription_id"]] = (requester, sub | {"cloudevents": False})
        return status, sub


def _webhook_run(serve, provider):
    from tds_conform.receiver import Receiver

    settings, client = serve(provider)
    with Receiver() as receiver:
        settings.receiver = receiver
        return run_scenario("webhook_delivery", settings, client, 0), receiver


def test_reference_provider_sends_matching_ce_headers(serve):
    result, receiver = _webhook_run(serve, Provider())
    failures = [(c.name, c.detail) for s in result.steps for c in s.checks if not c.passed]
    assert result.outcome == "PASS", failures
    assert receiver.deliveries and all("ce-id" in {k.lower() for k in d.headers} for d in receiver.deliveries)


@pytest.mark.parametrize(
    ("fault", "failing_check"),
    [
        ("claims", "subscription says cloudevents: true, so every delivery carries ce-* headers"),
        ("wrong_type", "ce-* headers, when sent, match the envelope (SPEC.md §9.1)"),
    ],
)
def test_kit_catches_cloudevents_faults(serve, monkeypatch, fault, failing_check):
    from tds_conform.mock import network

    if fault == "wrong_type":
        real = network.cloudevents_headers
        monkeypatch.setattr(network, "cloudevents_headers", lambda e: real(e) | {"ce-type": "org.example.trip"})
    result, _ = _webhook_run(serve, ClaimsCloudEvents() if fault == "claims" else Provider())
    assert result.outcome == "FAIL", result.note
    assert failing_check in {c.name for s in result.steps for c in s.checks if not c.passed}
