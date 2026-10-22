"""Booking windows (SPEC.md §7.1): the shared rules, the reference provider, the kit scenario."""

import copy
from datetime import datetime, timedelta

import pytest

import tds_conform.scenarios  # noqa: F401  (registers the scenarios)
from tds_conform.booking import (
    booking_window_for,
    booking_window_problem,
    closes_at,
    health_booking_problems,
    opens_at,
    trip_time,
)
from tds_conform.data import example, schema_errors
from tds_conform.mock.provider import Provider, ProviderConfig
from tds_conform.runner import run_scenario, shift_days

REQ = "throughride-demo"
TWO_WEEKS = {"max_advance_s": 14 * 86400, "min_notice_s": 86400, "time_zone": "America/Detroit"}
NEXT_DAY = {"max_advance_s": 2 * 86400, "same_day": False, "time_zone": "America/Detroit"}
AT = datetime.fromisoformat("2026-11-18T13:00:00-05:00")


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


# --- shared rules -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("window", "now", "outcome"),
    [
        (TWO_WEEKS, "2026-11-10T14:00:00-05:00", None),
        (TWO_WEEKS, "2026-11-04T13:00:00-05:00", None),  # opens exactly 14 days ahead
        (TWO_WEEKS, "2026-11-04T12:59:59-05:00", "ahead"),
        (TWO_WEEKS, "2026-11-17T13:00:00-05:00", None),  # exactly 24 hours' notice
        (TWO_WEEKS, "2026-11-17T13:00:01-05:00", "notice"),
        (NEXT_DAY, "2026-11-17T23:59:59-05:00", None),
        (NEXT_DAY, "2026-11-18T00:00:00-05:00", "previous service day"),
        (NEXT_DAY, "2026-11-18T05:30:00Z", "previous service day"),  # 00:30 in Detroit
        ({}, "2026-01-01T00:00:00Z", None),
        ({"min_notice_s": 0}, "2026-11-18T12:00:00-05:00", None),
    ],
)
def test_booking_window_problem(window, now, outcome):
    problem = booking_window_problem(window, AT, at(now))
    if outcome is None:
        assert problem is None
    else:
        assert outcome in problem["detail"]


def test_too_early_says_when_to_retry():
    problem = booking_window_problem(TWO_WEEKS, AT, at("2026-11-01T09:00:00-05:00"))
    assert problem["bookable_from"] == "2026-11-04T13:00:00-05:00"
    assert "bookable_from" not in booking_window_problem(TWO_WEEKS, AT, at("2026-11-18T09:00:00-05:00"))


def test_opens_and_closes():
    assert opens_at(TWO_WEEKS, AT) == at("2026-11-04T13:00:00-05:00")
    assert closes_at(TWO_WEEKS, AT) == at("2026-11-17T13:00:00-05:00")
    assert closes_at(NEXT_DAY, AT) == at("2026-11-17T23:59:59-05:00")
    assert opens_at({}, AT) is None and closes_at({}, AT) is None


def test_same_day_uses_trip_offset_without_time_zone():
    window = {"same_day": False}
    assert booking_window_problem(window, AT, at("2026-11-17T23:30:00-05:00")) is None
    assert booking_window_problem(window, AT, at("2026-11-18T00:30:00-05:00")) is not None


def test_trip_time_uses_pickup_then_dropoff_window():
    dropoff_only = example("single-trip", "01-request-trip.request.json")
    assert trip_time(dropoff_only) == at("2026-11-03T10:15:00-05:00")
    pickup = example("rider-category-facts", "01-request-trip.request.json")
    assert trip_time(pickup) == at("2026-11-18T13:00:00-05:00")


def test_window_for_profile_replaces_default():
    health = {"booking_window": TWO_WEEKS, "booking_window_by_profile": {"paratransit": NEXT_DAY}}
    core = example("single-trip", "01-request-trip.request.json")
    para = example("paratransit-trip", "01-request-trip.request.json")
    assert booking_window_for(health, core) == TWO_WEEKS
    assert booking_window_for(health, para) == NEXT_DAY
    assert booking_window_for(health, core, tender=True) == TWO_WEEKS
    assert booking_window_for(health | {"booking_window_by_profile": {"transfer": NEXT_DAY}}, core, True) == NEXT_DAY
    assert booking_window_for({}, core) is None


def test_health_rules():
    assert health_booking_problems({"booking_window": TWO_WEEKS}) == []
    problems = health_booking_problems(
        {
            "booking_window": {"min_notice_s": 10, "max_advance_s": 5},
            "booking_window_by_profile": {"nemt": {"time_zone": "Mars/Olympus"}},
        }
    )
    assert problems == [
        "booking_window: min_notice_s 10 is more than max_advance_s 5",
        "booking_window_by_profile.nemt: time_zone 'Mars/Olympus' is not an IANA time zone",
    ]


def test_schemas():
    assert schema_errors(TWO_WEEKS, "booking-window") == []
    assert schema_errors({"max_advance_s": -1}, "booking-window")
    assert schema_errors({"same_day": "no"}, "booking-window")
    health = {"status": "OK", "spec_versions": ["0.5.3"], "profiles": ["CORE"], "operated_via": "DIRECT"}
    assert schema_errors(health | {"booking_window": TWO_WEEKS}, "health") == []
    assert schema_errors(health | {"booking_window_by_profile": {"dial_a_ride": TWO_WEEKS}}, "health")
    refusal = {"title": "x", "status": 422, "code": "OUTSIDE_BOOKING_WINDOW", "bookable_from": "2026-11-04T13:00:00Z"}
    assert schema_errors(refusal, "error") == []
    assert schema_errors(refusal | {"bookable_from": "tomorrow"}, "error")


# --- reference provider ---------------------------------------------------------------------


def trip_on(day_offset: float, clock) -> dict:
    """The single-trip example with its dropoff window `day_offset` days after the fake clock."""
    trip = example("single-trip", "01-request-trip.request.json")
    delta = clock() + timedelta(days=day_offset) - trip_time(trip)
    for k in ("earliest", "latest"):
        window = trip["legs"][0]["dropoff_window"]
        window[k] = (datetime.fromisoformat(window[k]) + delta).isoformat()
    return trip


@pytest.fixture
def windowed(clock):
    return Provider(ProviderConfig(booking_window=dict(TWO_WEEKS)), clock=clock)


def test_mock_advertises_the_window(windowed):
    status, health = windowed.health()
    assert status == 200 and health["booking_window"] == TWO_WEEKS
    assert schema_errors(health, "health") == []
    assert "booking_window" not in Provider(ProviderConfig()).health()[1]


def test_mock_refuses_too_early_with_bookable_from(windowed, clock):
    trip = trip_on(20, clock)
    status, body = windowed.request_trip(REQ, "k1", trip)
    assert (status, body["code"]) == (422, "OUTSIDE_BOOKING_WINDOW")
    assert at(body["bookable_from"]) == trip_time(trip) - timedelta(days=14)
    assert schema_errors(body, "error") == []
    assert windowed.get_trip(REQ, trip["trip_id"])[1]["status"] == "REJECTED"


def test_mock_refuses_too_little_notice(windowed, clock):
    status, body = windowed.request_trip(REQ, "k1", trip_on(0.5, clock))
    assert (status, body["code"]) == (422, "OUTSIDE_BOOKING_WINDOW")
    assert "bookable_from" not in body


def test_mock_accepts_inside_the_window(windowed, clock):
    status, body = windowed.request_trip(REQ, "k1", trip_on(3, clock))
    assert (status, body["status"]) == (201, "OFFERED")


def test_booking_window_comes_before_other_refusals(clock):
    config = ProviderConfig(booking_window=dict(TWO_WEEKS), unsupported_requirements=frozenset({"WHEELCHAIR_LIFT"}))
    status, body = Provider(config, clock=clock).request_trip(REQ, "k1", trip_on(20, clock))
    assert body["code"] == "OUTSIDE_BOOKING_WINDOW"


def test_mock_refuses_patch_outside_window_and_keeps_terms(windowed, clock):
    trip = trip_on(3, clock)
    windowed.request_trip(REQ, "k1", trip)
    moved = trip_on(30, clock)["legs"][0]["dropoff_window"]
    status, body = windowed.modify_trip(REQ, "k2", trip["trip_id"], {"dropoff_window": moved})
    assert (status, body["code"]) == (422, "OUTSIDE_BOOKING_WINDOW")
    after = windowed.get_trip(REQ, trip["trip_id"])[1]
    assert (after["status"], after["legs"][0]["dropoff_window"]) == ("OFFERED", trip["legs"][0]["dropoff_window"])


def test_mock_profile_window(clock):
    config = ProviderConfig(booking_window=dict(TWO_WEEKS), booking_window_by_profile={"paratransit": NEXT_DAY})
    provider = Provider(config, clock=clock)
    para = example("paratransit-trip", "01-request-trip.request.json")
    para["legs"] = trip_on(5, clock)["legs"]  # inside 14 days, beyond the paratransit 2 days
    status, body = provider.request_trip(REQ, "k1", para)
    assert (status, body["code"]) == (422, "OUTSIDE_BOOKING_WINDOW"), body
    core = trip_on(5, clock)
    assert provider.request_trip(REQ, "k2", core)[0] == 201


def test_mock_rejects_tender_outside_window(clock):
    clock.now = clock.now.replace(day=10, hour=12)  # 07:00 EST on the trip day
    body = example("two-provider-transfer", "07-tender-onward.request.json")
    provider = Provider(
        ProviderConfig(provider_id="oakland-go", booking_window_by_profile={"transfer": {"min_notice_s": 2 * 86400}}),
        clock=clock,
    )
    status, tender = provider.create_tender(REQ, "k1", body)
    assert (status, tender["status"], tender["reason"]) == (201, "REJECTED", "OUTSIDE_BOOKING_WINDOW")


def test_sim_policy_sets_window(sync_provider):
    status, values = sync_provider.sim_policy({"booking_window": NEXT_DAY})
    assert status == 200 and values["booking_window"] == NEXT_DAY
    assert sync_provider.health()[1]["booking_window"] == NEXT_DAY
    sync_provider.sim_policy({"booking_window": None})
    assert "booking_window" not in sync_provider.health()[1]


# --- kit scenario ---------------------------------------------------------------------------


class IgnoresWindow(Provider):
    """Advertises a window but books anything."""

    def _booking_refusal(self, trip_request, tender=False):
        return None


class WrongCode(Provider):
    """Refuses out-of-window trips as if they were outside service hours."""

    def _booking_refusal(self, trip_request, tender=False):
        refusal = super()._booking_refusal(trip_request, tender)
        if refusal:
            refusal[1]["code"] = "OUTSIDE_SERVICE_HOURS"
        return refusal


class NoRetryHint(Provider):
    """Refuses too-early trips without bookable_from."""

    def _booking_refusal(self, trip_request, tender=False):
        refusal = super()._booking_refusal(trip_request, tender)
        if refusal:
            refusal[1].pop("bookable_from", None)
        return refusal


class TooStrict(Provider):
    """Refuses every trip for timing."""

    def _booking_refusal(self, trip_request, tender=False):
        return 422, {"type": "about:blank", "title": "x", "status": 422, "code": "OUTSIDE_BOOKING_WINDOW"}


def _run(serve, provider):
    settings, client = serve(provider)
    health = provider.health()[1]
    return run_scenario("booking_window", settings, client, health.get("typical_response_s", 0), health)


def _failures(result):
    return [(s.label, c.name, c.detail) for s in result.steps for c in s.checks if not c.passed]


@pytest.mark.parametrize("window", [TWO_WEEKS, NEXT_DAY, {"max_advance_s": 7 * 86400}, {"min_notice_s": 3 * 86400}])
@pytest.mark.parametrize("decision", ["sync", "async"])
def test_reference_provider_passes(serve, window, decision):
    config = ProviderConfig(booking_window=dict(window), decision=decision, decision_delay_s=0.3)
    result = _run(serve, Provider(config))
    assert result.outcome == "PASS", (result.note, _failures(result))


def test_no_window_advertised_passes_with_note(serve, sync_provider):
    result = _run(serve, sync_provider)
    assert result.outcome == "PASS" and "nothing to check" in result.note


@pytest.mark.parametrize(
    ("cls", "failing_check"),
    [
        (IgnoresWindow, "status is 422"),
        (WrongCode, "refused with OUTSIDE_BOOKING_WINDOW"),
        (NoRetryHint, "bookable_from says when to retry"),
        (TooStrict, "a trip inside the advertised window is not refused for timing"),
    ],
)
def test_kit_catches_faults(serve, cls, failing_check):
    result = _run(serve, cls(ProviderConfig(booking_window=dict(TWO_WEEKS))))
    assert result.outcome == "FAIL"
    assert failing_check in {c.name for s in result.steps for c in s.checks if not c.passed}


def test_kit_moves_test_trips_into_the_window(serve):
    """Other scenarios still get a bookable trip from a provider that needs three days' notice."""
    provider = Provider(ProviderConfig(booking_window={"min_notice_s": 3 * 86400}))
    settings, client = serve(provider)
    health = provider.health()[1]
    result = run_scenario("book_confirm_cancel", settings, client, 0, health)
    assert result.outcome == "PASS", (result.note, _failures(result))


def test_shift_days_keeps_time_of_day():
    trip = copy.deepcopy(example("single-trip", "01-request-trip.request.json"))
    shift_days(trip["legs"], 2)
    assert trip_time(trip) == at("2026-11-05T10:15:00-05:00")
