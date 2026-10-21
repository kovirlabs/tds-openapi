"""Webhook events: signing, verification, schema checks and sequence rules (SPEC.md §9, §10.3, §10.7, §12)."""

import hashlib
import hmac
import time
from collections import defaultdict
from datetime import UTC, datetime

from tds_conform.data import schema_errors

SIGNATURE_HEADER = "X-TDS-Signature"
TIMESTAMP_HEADER = "X-TDS-Timestamp"
MAX_SKEW_S = 300

EVENT_SCHEMAS = {
    "TRIP_STATUS_CHANGED": "core/event-trip-status-changed",
    "TRIP_SCHEDULED": "core/event-trip-scheduled",
    "TRIP_CANCELLED": "core/event-trip-cancelled",
    "TENDER_RESPONSE": "transfer/event-tender-response",
    "HOLD_RESPONSE": "transfer/event-hold-response",
    "MILESTONE": "transfer/event-milestone",
    "ETA_UPDATE": "transfer/event-eta-update",
    "CUSTODY_HANDOFF": "transfer/event-custody-handoff",
}

# Allowed next milestones (SPEC.md §10.3). None = no milestone yet.
NEXT_MILESTONES = {
    None: {"EN_ROUTE_TO_PICKUP", "ARRIVED_PICKUP"},
    "EN_ROUTE_TO_PICKUP": {"ARRIVED_PICKUP"},
    "ARRIVED_PICKUP": {"RIDER_ONBOARD", "RIDER_NO_SHOW"},
    "RIDER_ONBOARD": {"EN_ROUTE_TO_DROPOFF"},
    "EN_ROUTE_TO_DROPOFF": {"ARRIVED_DROPOFF"},
    "ARRIVED_DROPOFF": {"RIDER_HANDED_OFF", "LEG_COMPLETED"},
    "RIDER_HANDED_OFF": {"LEG_COMPLETED"},
}
TERMINAL_MILESTONES = {"LEG_COMPLETED", "RIDER_NO_SHOW", "LEG_FAILED"}


def sign(secret: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    ts = str(int(time.time()) if timestamp is None else timestamp)
    mac = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()
    return {SIGNATURE_HEADER: f"sha256={mac}", TIMESTAMP_HEADER: ts}


def verify(secret: str, headers, body: bytes, now: float | None = None) -> str | None:
    """None if the signature and timestamp are good, else what is wrong."""
    ts, sig = headers.get(TIMESTAMP_HEADER), headers.get(SIGNATURE_HEADER)
    if not ts or not sig:
        return f"missing {TIMESTAMP_HEADER} or {SIGNATURE_HEADER}"
    if not ts.isdigit() or abs((time.time() if now is None else now) - int(ts)) > MAX_SKEW_S:
        return f"{TIMESTAMP_HEADER} {ts!r} is more than {MAX_SKEW_S}s from now"
    expected = sign(secret, body, int(ts))[SIGNATURE_HEADER]
    return None if hmac.compare_digest(expected, sig) else "signature does not match"


def event_errors(event) -> list[str]:
    if not isinstance(event, dict):
        return ["event is not a JSON object"]
    schema = EVENT_SCHEMAS.get(event.get("event_type"))
    if schema is None:
        return schema_errors(event, "core/event") or [f"unknown event_type {event.get('event_type')!r}"]
    return schema_errors(event, schema)


def dedupe(events: list[dict]) -> list[dict]:
    """Drop redeliveries (same event_id) and order by occurred_at."""
    seen, unique = set(), []
    for event in events:
        if event.get("event_id") not in seen:
            seen.add(event.get("event_id"))
            unique.append(event)
    return sorted(unique, key=_when)


def _when(event: dict) -> datetime:
    try:
        return datetime.fromisoformat(event["occurred_at"])
    except (KeyError, TypeError, ValueError):
        return datetime.min.replace(tzinfo=UTC)


def _by_leg(events: list[dict]) -> dict[tuple, list[dict]]:
    legs = defaultdict(list)
    for event in dedupe(events):
        legs[(event.get("provider_id"), event.get("trip_id"), event.get("leg_id"))].append(event)
    return legs


def milestone_problems(events: list[dict]) -> list[str]:
    """milestone_sequence: each leg's milestones follow SPEC.md §10.3."""
    problems = []
    for leg, leg_events in _by_leg(events).items():
        last = None
        for event in leg_events:
            if event.get("event_type") != "MILESTONE":
                continue
            milestone = event["payload"]["milestone"]
            if last in TERMINAL_MILESTONES:
                problems.append(f"{leg}: {milestone} after terminal {last}")
            elif milestone != "LEG_FAILED" and milestone not in NEXT_MILESTONES.get(last, set()):
                problems.append(f"{leg}: {milestone} cannot follow {last or 'the start'}")
            last = milestone
    return problems


def custody_problems(events: list[dict], supervised: set[tuple]) -> list[str]:
    """custody_required: a supervised leg that completes has a handoff at pickup and at dropoff.

    A handoff before EN_ROUTE_TO_DROPOFF counts for pickup; one after ARRIVED_DROPOFF counts
    for dropoff. `supervised` holds (provider_id, trip_id, leg_id) keys of legs needing custody.
    """
    problems = []
    for leg, leg_events in _by_leg(events).items():
        if leg not in supervised:
            continue
        milestones = [e["payload"]["milestone"] for e in leg_events if e.get("event_type") == "MILESTONE"]
        if "LEG_COMPLETED" not in milestones:
            continue
        stage, handoffs = "pickup", set()
        for event in leg_events:
            if event.get("event_type") == "MILESTONE":
                stage = {"EN_ROUTE_TO_DROPOFF": "transit", "ARRIVED_DROPOFF": "dropoff"}.get(
                    event["payload"]["milestone"], stage
                )
            elif event.get("event_type") == "CUSTODY_HANDOFF":
                handoffs.add(stage)
        if "RIDER_HANDED_OFF" not in milestones:
            problems.append(f"{leg}: supervised leg completed without RIDER_HANDED_OFF")
        problems += [
            f"{leg}: no custody handoff at {where}" for where in ("pickup", "dropoff") if where not in handoffs
        ]
    return problems


def heartbeat_problems(events: list[dict], feeders: dict[tuple, int]) -> list[str]:
    """eta_heartbeat_staleness: while a feeder leg is in progress toward a PROTECTED interchange,
    no gap between ETA updates exceeds 2 × heartbeat_s (SPEC.md §10.4).

    `feeders` maps (provider_id, trip_id, leg_id) of each such leg to its heartbeat_s. The
    window runs from EN_ROUTE_TO_PICKUP (or ARRIVED_PICKUP) to ARRIVED_DROPOFF.
    """
    problems = []
    legs = _by_leg(events)
    for leg, heartbeat in feeders.items():
        start = end = None
        etas = []
        for event in legs.get(leg, []):
            when = _when(event)
            if event.get("event_type") == "MILESTONE":
                milestone = event["payload"]["milestone"]
                if start is None and milestone in ("EN_ROUTE_TO_PICKUP", "ARRIVED_PICKUP"):
                    start = when
                if start and end is None and (milestone == "ARRIVED_DROPOFF" or milestone in TERMINAL_MILESTONES):
                    end = when
            elif event.get("event_type") == "ETA_UPDATE" and start and not end:
                etas.append(when)
        if start is None:
            continue
        if end is None:
            problems.append(f"{leg}: leg never reached ARRIVED_DROPOFF; heartbeat window is open")
            continue
        marks = [start, *etas, end]
        worst = max((b - a).total_seconds() for a, b in zip(marks, marks[1:], strict=False))
        if worst > 2 * heartbeat:
            problems.append(f"{leg}: {worst:.0f}s without an ETA update (limit {2 * heartbeat}s)")
    return problems
