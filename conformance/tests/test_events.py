"""Webhook signing and the event-sequence rules the kit applies to event logs."""

import json

from tds_conform.data import data_root
from tds_conform.events import custody_problems, milestone_problems, sign, verify

SECRET = "whsec-test"


def test_signature_round_trip_and_tamper():
    body = b'{"event_id": "e1"}'
    headers = sign(SECRET, body, timestamp=1_800_000_000)
    assert verify(SECRET, headers, body, now=1_800_000_010) is None
    assert verify(SECRET, headers, body + b" ", now=1_800_000_010) == "signature does not match"
    assert verify("other-secret", headers, body, now=1_800_000_010) == "signature does not match"
    assert "more than 300s" in verify(SECRET, headers, body, now=1_800_000_400)
    assert "missing" in verify(SECRET, {}, body)


def milestones(*names, trip="t1"):
    return [
        {
            "event_id": f"e{i}",
            "event_type": "MILESTONE",
            "occurred_at": f"2026-11-10T09:{i:02d}:00-05:00",
            "provider_id": "p",
            "trip_id": trip,
            "leg_id": "leg-1",
            "payload": {"milestone": n},
        }
        for i, n in enumerate(names)
    ]


def test_milestone_order():
    good = milestones("ARRIVED_PICKUP", "RIDER_ONBOARD", "EN_ROUTE_TO_DROPOFF", "ARRIVED_DROPOFF", "LEG_COMPLETED")
    assert milestone_problems(good) == []
    assert milestone_problems(milestones("RIDER_ONBOARD"))
    assert milestone_problems(milestones("ARRIVED_PICKUP", "LEG_COMPLETED"))
    assert milestone_problems(milestones("ARRIVED_PICKUP", "LEG_FAILED")) == []
    assert milestone_problems(milestones("ARRIVED_PICKUP", "RIDER_NO_SHOW", "RIDER_ONBOARD"))


def test_custody_needs_pickup_and_dropoff_records():
    leg = ("p", "t1", "leg-1")
    events = milestones(
        "ARRIVED_PICKUP", "RIDER_ONBOARD", "EN_ROUTE_TO_DROPOFF", "ARRIVED_DROPOFF", "RIDER_HANDED_OFF", "LEG_COMPLETED"
    )
    assert "no custody handoff at pickup" in " ".join(custody_problems(events, {leg}))
    assert custody_problems(events, set()) == []  # not supervised: nothing required


def test_example_streams_pass_every_rule():
    for stream in sorted((data_root() / "examples").glob("*/events.json")):
        events = json.loads(stream.read_text())
        assert milestone_problems(events) == [], stream
