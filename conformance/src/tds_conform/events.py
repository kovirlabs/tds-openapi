"""Webhook events: signing, verification, schema checks and sequence rules (SPEC.md §9, §10.3, §10.7, §12)."""

import base64
import binascii
import hashlib
import hmac
import json
import os
import time
from collections import defaultdict
from datetime import UTC, datetime

from tds_conform.data import schema_errors

SIGNATURE_HEADER = "X-TDS-Signature"  # deprecated alias since 0.6.0, still sent (SPEC.md §12)
TIMESTAMP_HEADER = "X-TDS-Timestamp"  # deprecated alias since 0.6.0, still sent
# Standard Webhooks headers (standardwebhooks.com), SPEC.md §12.
WEBHOOK_ID_HEADER = "webhook-id"
WEBHOOK_TIMESTAMP_HEADER = "webhook-timestamp"
WEBHOOK_SIGNATURE_HEADER = "webhook-signature"
SECRET_PREFIX = "whsec_"
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
    "EXCEPTION_RAISED": "transfer/event-exception-raised",
    "EXCEPTION_RESOLVED": "transfer/event-exception-resolved",
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


def standard_key(secret: str) -> bytes | None:
    """HMAC key of a Standard Webhooks secret (`whsec_` + base64 of 24 to 64 bytes), else None."""
    if not secret.startswith(SECRET_PREFIX):
        return None
    try:
        key = base64.b64decode(secret[len(SECRET_PREFIX) :], validate=True)
    except (binascii.Error, ValueError):
        return None
    return key if 24 <= len(key) <= 64 else None


def new_standard_secret(n_bytes: int = 32) -> str:
    """A fresh signing secret usable for both X-TDS-Signature and webhook-signature."""
    return SECRET_PREFIX + base64.b64encode(os.urandom(n_bytes)).decode()


def _as_list(secrets) -> list[str]:
    return [secrets] if isinstance(secrets, str) else list(secrets)


def sign_standard(secrets, event_id: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    """Standard Webhooks headers: one `v1,<base64>` per secret, newest first, space-separated.

    `secrets` is one secret or several (during rotation). Secrets not in `whsec_` form are
    skipped; with none left, no headers are returned (send X-TDS-Signature only).
    """
    ts = str(int(time.time()) if timestamp is None else timestamp)
    content = f"{event_id}.{ts}.".encode() + body
    sigs = [
        "v1," + base64.b64encode(hmac.new(key, content, hashlib.sha256).digest()).decode()
        for key in filter(None, map(standard_key, _as_list(secrets)))
    ]
    if not sigs:
        return {}
    return {WEBHOOK_ID_HEADER: event_id, WEBHOOK_TIMESTAMP_HEADER: ts, WEBHOOK_SIGNATURE_HEADER: " ".join(sigs)}


def verify_standard(secrets, headers, body: bytes, now: float | None = None) -> str | None:
    """None if `webhook-signature` holds a valid v1 signature for any of `secrets`, else what is wrong.

    Unknown signature versions (e.g. `v1a`) are ignored; comparison is constant-time.
    """
    msg_id = get_header(headers, WEBHOOK_ID_HEADER)
    ts = get_header(headers, WEBHOOK_TIMESTAMP_HEADER)
    sig = get_header(headers, WEBHOOK_SIGNATURE_HEADER)
    if not msg_id or not ts or not sig:
        return f"missing {WEBHOOK_ID_HEADER}, {WEBHOOK_TIMESTAMP_HEADER} or {WEBHOOK_SIGNATURE_HEADER}"
    if not ts.isdigit() or abs((time.time() if now is None else now) - int(ts)) > MAX_SKEW_S:
        return f"{WEBHOOK_TIMESTAMP_HEADER} {ts!r} is more than {MAX_SKEW_S}s from now"
    keys = [k for k in map(standard_key, _as_list(secrets)) if k]
    if not keys:
        return "no Standard Webhooks secret (whsec_...) to verify webhook-signature with"
    content = f"{msg_id}.{ts}.".encode() + body
    expected = [base64.b64encode(hmac.new(k, content, hashlib.sha256).digest()).decode() for k in keys]
    for entry in sig.split():
        version, _, value = entry.partition(",")
        if version == "v1" and any(hmac.compare_digest(value, e) for e in expected):
            return None
    return f"no {WEBHOOK_SIGNATURE_HEADER} entry matches"


def verify_any(secrets, headers, body: bytes, now: float | None = None) -> str | None:
    """Receiver rule (SPEC.md §12): accept if webhook-signature or X-TDS-Signature verifies.

    `secrets` is one secret or several (a receiver keeps the old one during rotation).
    Returns None when the delivery is accepted, else why both checks failed.
    """
    problems = []
    if get_header(headers, WEBHOOK_SIGNATURE_HEADER) is not None:
        if (problem := verify_standard(secrets, headers, body, now)) is None:
            return None
        problems.append(problem)
    problem = "no signing secret"
    for secret in _as_list(secrets):
        if (problem := verify(secret, headers, body, now)) is None:
            return None
    problems.append(problem)
    return "; ".join(problems)


def verify(secret: str, headers, body: bytes, now: float | None = None) -> str | None:
    """None if X-TDS-Signature and X-TDS-Timestamp are good, else what is wrong."""
    ts, sig = get_header(headers, TIMESTAMP_HEADER), get_header(headers, SIGNATURE_HEADER)
    if not ts or not sig:
        return f"missing {TIMESTAMP_HEADER} or {SIGNATURE_HEADER}"
    if not ts.isdigit() or abs((time.time() if now is None else now) - int(ts)) > MAX_SKEW_S:
        return f"{TIMESTAMP_HEADER} {ts!r} is more than {MAX_SKEW_S}s from now"
    expected = sign(secret, body, int(ts))[SIGNATURE_HEADER]
    return None if hmac.compare_digest(expected, sig) else "signature does not match"


def get_header(headers, name: str) -> str | None:
    """Case-insensitive header lookup (HTTP header names are case-insensitive)."""
    wanted = name.lower()
    return next((v for k, v in headers.items() if k.lower() == wanted), None)


def delivery_problems(
    secret: str, headers, body: bytes, *, now: float | None = None, cloudevents: bool = False
) -> list[str]:
    """Everything wrong with one webhook delivery (SPEC.md §9, §9.1, §12); [] when it is good.

    Checks the body is a schema-valid event, X-TDS-Signature (senders still send it), the
    Standard Webhooks headers when present (`webhook-id` = `event_id`, a valid v1 signature),
    and any `ce-*` headers (required when the subscription has `cloudevents: true`).
    """
    from tds_conform.cloudevents import cloudevents_problems

    try:
        event = json.loads(body)
    except ValueError:
        return ["body is not JSON"]
    problems = event_errors(event)
    if problem := verify(secret, headers, body, now=now):
        problems.append(problem)
    if get_header(headers, WEBHOOK_SIGNATURE_HEADER) is not None:
        if problem := verify_standard(secret, headers, body, now=now):
            problems.append(problem)
        if isinstance(event, dict) and get_header(headers, WEBHOOK_ID_HEADER) != event.get("event_id"):
            problems.append(f"{WEBHOOK_ID_HEADER} is not the event_id")
    elif standard_key(secret) and any(get_header(headers, h) for h in (WEBHOOK_ID_HEADER, WEBHOOK_TIMESTAMP_HEADER)):
        problems.append(f"{WEBHOOK_ID_HEADER}/{WEBHOOK_TIMESTAMP_HEADER} sent without {WEBHOOK_SIGNATURE_HEADER}")
    if cloudevents and get_header(headers, "ce-id") is None:
        problems.append("subscription has cloudevents: true but the delivery has no ce-* headers")
    if isinstance(event, dict):
        problems += cloudevents_problems(headers, event)
    return problems


def event_errors(event) -> list[str]:
    if not isinstance(event, dict):
        return ["event is not a JSON object"]
    schema = EVENT_SCHEMAS.get(event.get("event_type"))
    if schema is None:
        return schema_errors(event, "core/event") or [f"unknown event_type {event.get('event_type')!r}"]
    return schema_errors(event, schema)


def dedupe(events: list[dict]) -> list[dict]:
    """Drop redeliveries (same provider_id and event_id, CloudEvents source+id) and order by occurred_at."""
    seen, unique = set(), []
    for event in events:
        key = (event.get("provider_id"), event.get("event_id"))
        if key not in seen:
            seen.add(key)
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


BROKER_EVENTS = {"EXCEPTION_RAISED", "EXCEPTION_RESOLVED"}


def broker_event_problems(events: list[dict]) -> list[str]:
    """Exception events (ESCALATED included) come only from brokers: no party sends both them and provider events."""
    providers = {e.get("provider_id") for e in events if e.get("event_type") not in BROKER_EVENTS}
    return sorted(
        {
            f"{e.get('provider_id')} sends provider events and {e['event_type']} "
            f"({e['payload']['exception'].get('type')}); exception events come only from brokers"
            for e in events
            if e.get("event_type") in BROKER_EVENTS and e.get("provider_id") in providers
        }
    )


def escalation_problems(events: list[dict]) -> list[str]:
    """ESCALATED exceptions (SPEC.md §10.6) name an earlier condition exception from the same broker, climb
    in level, and are resolved whenever their condition is."""
    problems = []
    raised: dict[tuple, dict] = {}
    resolved: set[tuple] = set()
    levels: dict[tuple, int] = {}
    for event in dedupe(events):
        if event.get("event_type") not in BROKER_EVENTS:
            continue
        exception = event["payload"]["exception"]
        key = (event.get("provider_id"), exception["exception_id"])
        if event["event_type"] == "EXCEPTION_RESOLVED":
            resolved.add(key)
            continue
        raised[key] = exception
        if exception["type"] != "ESCALATED":
            continue
        target = (event.get("provider_id"), exception["escalates_exception_id"])
        if target not in raised:
            problems.append(f"{key[1]}: escalates {target[1]}, which this broker has not raised before")
        elif raised[target]["type"] == "ESCALATED":
            problems.append(f"{key[1]}: escalates another ESCALATED exception ({target[1]}); name the condition")
        level = exception.get("escalation_level", 1)
        if level <= levels.get(target, 0):
            problems.append(f"{key[1]}: escalation_level {level} does not rise above {levels[target]}")
        levels[target] = max(level, levels.get(target, 0))
    for key, exception in raised.items():
        if exception["type"] == "ESCALATED":
            target = (key[0], exception["escalates_exception_id"])
            if target in resolved and key not in resolved:
                problems.append(f"{key[1]}: {target[1]} was resolved but its escalation was not")
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
