"""The reference provider's state machine, idempotency and validation (no HTTP)."""

import copy

import pytest

from tds_conform.data import example, schema_errors
from tds_conform.mock.provider import Provider, ProviderConfig

REQ = "throughride-demo"


def trip_request(**overrides) -> dict:
    trip = example("single-trip", "01-request-trip.request.json")
    return trip | overrides


@pytest.fixture
def provider(clock):
    return Provider(ProviderConfig(), clock=clock)


def test_sync_request_offers_with_scheduled_times(provider):
    status, body = provider.request_trip(REQ, "k1", trip_request())
    assert status == 201 and body["status"] == "OFFERED"
    assert body["legs"][0]["scheduled_pickup"] and body["provider_trip_id"]
    assert schema_errors(body, "trip") == []


def test_async_request_is_decided_after_delay(clock):
    provider = Provider(ProviderConfig(decision="async", decision_delay_s=10), clock=clock)
    status, body = provider.request_trip(REQ, "k1", trip_request())
    assert (status, body["status"]) == (202, "REQUESTED")
    clock.advance(5)
    assert provider.get_trip(REQ, body["trip_id"])[1]["status"] == "REQUESTED"
    clock.advance(6)
    assert provider.get_trip(REQ, body["trip_id"])[1]["status"] == "OFFERED"
    assert provider.health()[1]["typical_response_s"] == 10


def test_confirm_then_scheduled_with_vehicle(provider, clock):
    trip_id = provider.request_trip(REQ, "k1", trip_request())[1]["trip_id"]
    assert provider.confirm_trip(REQ, "k2", trip_id)[1]["status"] == "CONFIRMED"
    clock.advance(2)
    status, body = provider.get_trip(REQ, trip_id)
    assert body["status"] == "SCHEDULED" and body["legs"][0]["vehicle_ref"]["vehicle_id"]
    assert schema_errors(body, "trip") == []


def test_terminal_states_refuse_changes(provider):
    trip_id = provider.request_trip(REQ, "k1", trip_request())[1]["trip_id"]
    status, body = provider.cancel_trip(REQ, "k2", trip_id, {"reason": "RIDER_REQUEST"})
    assert status == 200 and body["cancellation"]["cancelled_by"] == "REQUESTER"
    assert schema_errors(body, "trip") == []
    for call in (
        lambda: provider.cancel_trip(REQ, "k3", trip_id, {"reason": "RIDER_REQUEST"}),
        lambda: provider.confirm_trip(REQ, "k4", trip_id),
        lambda: provider.modify_trip(REQ, "k5", trip_id, {"notes_to_driver": "x"}),
    ):
        status, body = call()
        assert (status, body["code"]) == (409, "INVALID_STATE")


def test_modify_reoffers_and_clears_schedule(provider, clock):
    trip_id = provider.request_trip(REQ, "k1", trip_request())[1]["trip_id"]
    provider.confirm_trip(REQ, "k2", trip_id)
    clock.advance(2)
    window = {"earliest": "2026-11-03T10:00:00-05:00", "latest": "2026-11-03T10:30:00-05:00"}
    status, body = provider.modify_trip(REQ, "k3", trip_id, {"dropoff_window": window})
    assert (status, body["status"]) == (200, "OFFERED")
    assert "vehicle_ref" not in body["legs"][0] and body["legs"][0]["dropoff_window"] == window


def test_idempotency(provider):
    trip = trip_request()
    first = provider.request_trip(REQ, "k1", trip)
    assert provider.request_trip(REQ, "k1", copy.deepcopy(trip)) == first
    assert provider.request_trip(REQ, "k1", trip | {"notes_to_driver": "x"})[1]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert provider.request_trip(REQ, "k2", trip)[1]["code"] == "TRIP_ID_IN_USE"
    assert provider.request_trip(REQ, None, trip)[0] == 400
    # Keys are per requester: another client may reuse "k1".
    assert provider.request_trip("other", "k1", trip | {"requester_id": "other"})[0] == 201


def test_trips_are_scoped_to_requester(provider):
    trip_id = provider.request_trip(REQ, "k1", trip_request())[1]["trip_id"]
    assert provider.get_trip("other", trip_id)[0] == 404


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"requester_id": "someone-else"}, "INVALID_REQUEST"),
        ({"legs": []}, "INVALID_REQUEST"),
    ],
)
def test_invalid_requests(provider, change, code):
    status, body = provider.request_trip(REQ, "k1", trip_request(**change))
    assert (status, body["code"]) == (400, code)


def test_window_order_is_enforced(provider):
    trip = trip_request()
    trip["legs"][0]["dropoff_window"] = {"earliest": "2026-11-03T11:00:00-05:00", "latest": "2026-11-03T10:00:00-05:00"}
    status, body = provider.request_trip(REQ, "k1", trip)
    assert (status, body["code"]) == (422, "INVALID_WINDOW")
    assert provider.get_trip(REQ, trip["trip_id"])[1]["status"] == "REJECTED"


def test_unsupported_requirement_is_refused(clock):
    provider = Provider(ProviderConfig(unsupported_requirements=frozenset({"WHEELCHAIR_LIFT"})), clock=clock)
    status, body = provider.request_trip(REQ, "k1", trip_request())
    assert (status, body["code"]) == (422, "UNSUPPORTED_REQUIREMENT")


def test_health_matches_schema(provider):
    assert schema_errors(provider.health()[1], "health") == []


def test_subscription_returns_secret_once(provider):
    req = example("subscriptions", "01-subscribe.request.json")
    status, body = provider.subscribe(REQ, "k1", req)
    assert status == 201 and len(body["signing_secret"]) >= 32
    assert schema_errors(body, "subscription") == []
