"""Transfer-profile behaviour of the reference provider: tenders, holds, milestones, custody, heartbeats."""

import copy

import pytest

from tds_conform.data import example
from tds_conform.events import custody_problems, event_errors, heartbeat_problems, milestone_problems
from tds_conform.mock.provider import Provider, ProviderConfig

REQ = "throughride-demo"
SCENARIO = "two-provider-transfer"


def tender_body(n: int = 7, **interchange_overrides) -> dict:
    """The onward-leg tender from the generated example, re-dated to the fake clock's day."""
    body = example(SCENARIO, "07-tender-onward.request.json")
    body["interchange"] |= interchange_overrides
    return body


@pytest.fixture
def onward(clock):
    clock.now = clock.now.replace(year=2026, month=11, day=10, hour=12)  # 07:00 EST on the trip day
    return Provider(ProviderConfig(provider_id="oakland-go"), clock=clock)


def all_events_valid(provider: Provider) -> list[str]:
    return [m for e in provider.events for m in event_errors(e["event"])]


def test_sync_tender_accepts_creates_trip_and_caps_terms(onward):
    status, tender = onward.create_tender(REQ, "k1", tender_body(max_hold_s=3600, auto_hold_s=3600))
    assert (status, tender["status"]) == (201, "ACCEPTED")
    assert tender["accepted_terms"] == {"auto_hold_s": 300, "max_hold_s": 900}  # provider caps
    status, trip = onward.get_trip(REQ, tender["trip_id"])
    assert (status, trip["status"]) == (200, "CONFIRMED")
    assert [e["event"]["event_type"] for e in onward.events][-1] == "TENDER_RESPONSE"
    assert all_events_valid(onward) == []


def test_rejected_tender_creates_no_trip(onward):
    onward.config.tender_decision = "REJECT"
    status, tender = onward.create_tender(REQ, "k1", tender_body())
    assert (tender["status"], tender["reason"]) == ("REJECTED", "NO_CAPACITY")
    assert onward.get_trip(REQ, tender["trip_id"])[0] == 404


def test_unanswered_tender_expires_at_respond_by(onward, clock):
    onward.config.tender_decision = "IGNORE"
    status, tender = onward.create_tender(REQ, "k1", tender_body())
    assert (status, tender["status"]) == (202, "OFFERED")
    clock.advance(24 * 3600)
    onward.tick()
    assert onward.get_tender(REQ, tender["tender_id"])[1]["status"] == "EXPIRED"
    assert onward.events[-1]["event"]["payload"]["tender"]["status"] == "EXPIRED"


def test_async_tender_is_decided_later(clock):
    clock.now = clock.now.replace(year=2026, month=11, day=10, hour=12)
    provider = Provider(ProviderConfig(provider_id="oakland-go", decision="async", decision_delay_s=5), clock=clock)
    assert provider.create_tender(REQ, "k1", tender_body())[1]["status"] == "OFFERED"
    clock.advance(6)
    provider.tick()
    assert provider.get_tender(REQ, "tn-tpt-b")[1]["status"] == "ACCEPTED"


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"to": {"provider_id": "oakland-go", "trip_id": "someone-else", "leg_id": "leg-1"}}, "neither"),
        ({"auto_hold_s": 1000, "max_hold_s": 900}, "auto_hold_s"),
    ],
)
def test_bad_interchanges_are_refused(onward, change, fragment):
    status, body = onward.create_tender(REQ, "k1", tender_body(**change))
    assert status == 400 and fragment in body["detail"]


def hold(onward, minutes: int, key: str = "h1", hold_id: str = "hd-1") -> dict:
    trip = onward.get_trip(REQ, "tr-tpt-b")[1]
    from datetime import datetime, timedelta

    pickup = datetime.fromisoformat(trip["legs"][0]["scheduled_pickup"])
    body = {
        "hold_id": hold_id,
        "interchange_id": "ix-tpt",
        "action": "HOLD",
        "requested_until": (pickup + timedelta(minutes=minutes)).isoformat(),
        "respond_by": (pickup + timedelta(hours=1)).isoformat(),
    }
    return onward.request_hold(REQ, key, "tr-tpt-b", body)


@pytest.mark.parametrize(
    ("policy", "minutes", "status", "moved_minutes"),
    [("AUTO", 10, "GRANTED", 10), ("AUTO", 30, "PARTIAL", 15), ("PARTIAL", 10, "PARTIAL", 5), ("DENY", 5, "DENIED", 0)],
)
def test_hold_decisions(onward, policy, minutes, status, moved_minutes):
    onward.create_tender(REQ, "k1", tender_body())
    before = onward.get_trip(REQ, "tr-tpt-b")[1]["legs"][0]["scheduled_pickup"]
    onward.config.hold_decision = policy
    code, body = hold(onward, minutes)
    assert (code, body["status"]) == (201, status)
    after = onward.get_trip(REQ, "tr-tpt-b")[1]["legs"][0]["scheduled_pickup"]
    from datetime import datetime

    assert (datetime.fromisoformat(after) - datetime.fromisoformat(before)).total_seconds() == moved_minutes * 60
    assert all_events_valid(onward) == []


def test_unanswered_hold_expires(onward, clock):
    onward.create_tender(REQ, "k1", tender_body())
    onward.config.hold_decision = "IGNORE"
    assert hold(onward, 5)[1]["status"] == "PENDING"
    clock.advance(24 * 3600)
    assert onward.get_hold(REQ, "tr-tpt-b", "hd-1")[1]["status"] == "EXPIRED"


def test_hold_refused_after_pickup_and_on_feeder(onward):
    onward.create_tender(REQ, "k1", tender_body())
    onward.sim_advance("tr-tpt-b", {"until": "RIDER_ONBOARD"})
    assert hold(onward, 5)[1]["code"] == "INVALID_STATE"


def test_supervised_leg_records_custody_and_valid_milestones(onward):
    body = tender_body(supervision_required=True)
    onward.create_tender(REQ, "k1", body)
    status, result = onward.sim_advance("tr-tpt-b", {"until": "LEG_COMPLETED"})
    assert result["trip"]["status"] == "COMPLETED"
    events = [e["event"] for e in onward.events]
    handoffs = [e["payload"]["handoff"] for e in events if e["event_type"] == "CUSTODY_HANDOFF"]
    assert [(h["handoff_from"]["role"], h["handoff_to"]["role"]) for h in handoffs] == [
        ("DRIVER", "DRIVER"),  # from the feeder driver at the hub
        ("DRIVER", "FACILITY_STAFF"),
    ]
    assert milestone_problems(events) == []
    assert custody_problems(events, {("oakland-go", "tr-tpt-b", "leg-1")}) == []
    assert all_events_valid(onward) == []


def test_feeder_heartbeats_while_inbound(clock):
    clock.now = clock.now.replace(year=2026, month=11, day=10, hour=12)
    feeder = Provider(ProviderConfig(provider_id="aaata-a-ride"), clock=clock)
    feeder.create_tender(REQ, "k1", example(SCENARIO, "05-tender-feeder.request.json"))
    feeder.sim_advance("tr-tpt-a", {"until": "EN_ROUTE_TO_DROPOFF"})
    for _ in range(30):
        clock.advance(60)
        feeder.tick()
    feeder.sim_advance("tr-tpt-a", {"until": "LEG_COMPLETED"})
    events = [e["event"] for e in feeder.events]
    etas = [e for e in events if e["event_type"] == "ETA_UPDATE"]
    assert len(etas) >= 6
    assert heartbeat_problems(events, {("aaata-a-ride", "tr-tpt-a", "leg-1"): 300}) == []


def test_sim_end_and_policy(onward):
    onward.create_tender(REQ, "k1", tender_body())
    assert onward.sim_end("tr-tpt-b", "no-show", None)[0] == 409  # needs ARRIVED_PICKUP
    onward.sim_advance("tr-tpt-b", {"until": "ARRIVED_PICKUP"})
    status, body = onward.sim_end("tr-tpt-b", "no-show", None)
    assert body["trip"]["status"] == "NO_SHOW"
    assert onward.sim_policy({"bogus": 1})[0] == 400
    assert onward.sim_policy({"hold_decision": "DENY"})[1]["hold_decision"] == "DENY"


def test_events_have_strictly_increasing_times(onward):
    onward.create_tender(REQ, "k1", copy.deepcopy(tender_body()))
    onward.sim_advance("tr-tpt-b", {"until": "LEG_COMPLETED"})
    times = [e["event"]["occurred_at"] for e in onward.events]
    assert times == sorted(times) and len(set(times)) == len(times)
