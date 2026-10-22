"""Reference provider: the core trip state machine (SPEC.md §7–§9) with in-memory state.

Transport-free: every operation takes the authenticated requester and parsed inputs
and returns (http_status, body). Every change emits an event (SPEC.md §9) through
`sink`; the network layer delivers them as signed webhooks. Time-based transitions
(async decisions, scheduling, expiries, heartbeats) happen in `tick()`, which the
network calls periodically, and lazily whenever a trip is read, so tests can drive
time with a fake clock.

Transfer-profile behaviour (tenders, holds, milestones, custody) lives in
`TransferMixin` (transfer.py).
"""

import copy
import hashlib
import json
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from tds_conform import SPEC_VERSION
from tds_conform.booking import booking_window_for, booking_window_problem, trip_time
from tds_conform.data import schema_errors
from tds_conform.eligibility import trip_eligibility_problems
from tds_conform.mock.common import TERMINAL, Response, _invalid_state, _not_found, _ts, _window_problem, problem
from tds_conform.mock.transfer import TransferMixin
from tds_conform.profiles import profile_problems

__all__ = ["Provider", "ProviderConfig", "problem"]


@dataclass
class ProviderConfig:
    provider_id: str | None = None  # None: accept any provider_id
    decision: str = "sync"  # "sync": decide in the response; "async": 202, decided after decision_delay_s
    decision_delay_s: float = 2.0
    schedule_delay_s: float = 1.0
    operated_via: str = "DIRECT"
    unsupported_requirements: frozenset[str] = frozenset()
    # Eligibility acceptance policy (SPEC.md §6.5.1): attestations verified this way are refused
    # with ELIGIBILITY_NOT_ACCEPTED. "UNSTATED" stands for an attestation without verification_method.
    refused_verification_methods: frozenset[str] = frozenset()
    profiles: tuple[str, ...] = ("CORE", "TRANSFER", "PARATRANSIT", "NEMT")  # declared on /health
    # Booking windows (SPEC.md §7.1), advertised on /health and enforced with OUTSIDE_BOOKING_WINDOW.
    booking_window: dict | None = None
    booking_window_by_profile: dict | None = None
    offer_ttl_s: int | None = None  # set: offers carry offer_expires_at and EXPIRE if not confirmed
    # Transfer profile policies; adjustable at runtime through the /_sim API.
    tender_decision: str = "ACCEPT"  # ACCEPT | REJECT | IGNORE
    reject_reason: str = "NO_CAPACITY"
    hold_decision: str = "AUTO"  # AUTO | GRANT | PARTIAL | DENY | IGNORE
    max_hold_s: int = 900  # the most this provider will hold, whatever an interchange asks
    max_auto_hold_s: int = 300
    heartbeat_s: int | None = None  # override the interchange's heartbeat_s (e.g. faster demos)


@dataclass
class _Trip:
    requester: str
    body: dict
    decide_at: datetime | None = None
    schedule_at: datetime | None = None
    # Transfer profile
    interchange: dict | None = None
    role: str | None = None  # "from" (feeder) or "to" (onward)
    accepted_terms: dict | None = None
    milestone: str | None = None
    delay_s: int = 0
    delay_cause: str | None = None
    last_eta_at: datetime | None = None

    @property
    def leg(self) -> dict:
        return self.body["legs"][0]


@dataclass
class _Stored:
    fingerprint: str
    response: Response


@dataclass
class Provider(TransferMixin):
    config: ProviderConfig = field(default_factory=ProviderConfig)
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    sink: Callable[[str, dict], None] = lambda requester, event: None
    # Injectable so generated examples are reproducible.
    new_id: Callable[[str], str] = lambda prefix: f"{prefix}-{secrets.token_hex(4)}"
    new_secret: Callable[[], str] = lambda: secrets.token_urlsafe(32)
    events: list[dict] = field(default_factory=list)
    _trips: dict[tuple[str, str], _Trip] = field(default_factory=dict)
    _idempotency: dict[tuple[str, str], _Stored] = field(default_factory=dict)
    _subscriptions: dict[str, tuple[str, dict]] = field(default_factory=dict)
    _tenders: dict[tuple[str, str], dict] = field(default_factory=dict)
    _holds: dict[tuple[str, str], dict] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _last_event_at: datetime | None = None

    @property
    def provider_id(self) -> str:
        return self.config.provider_id or "mock-provider"

    # --- operations -------------------------------------------------------------

    def health(self) -> Response:
        body = {
            "status": "OK",
            "spec_versions": [SPEC_VERSION],
            "profiles": list(self.config.profiles),
            "operated_via": self.config.operated_via,
        }
        if self.config.decision == "async":
            body["typical_response_s"] = max(1, round(self.config.decision_delay_s))
        if self.config.booking_window:
            body["booking_window"] = copy.deepcopy(self.config.booking_window)
        if self.config.booking_window_by_profile:
            body["booking_window_by_profile"] = copy.deepcopy(self.config.booking_window_by_profile)
        return 200, body

    def request_trip(self, requester: str, key: str | None, body) -> Response:
        return self._idempotent(requester, key, "POST /trips", body, lambda: self._request_trip(requester, body))

    def get_trip(self, requester: str, trip_id: str) -> Response:
        with self._lock:
            trip = self._find(requester, trip_id)
            return (200, copy.deepcopy(trip.body)) if trip else _not_found("trip", trip_id)

    def confirm_trip(self, requester: str, key: str | None, trip_id: str) -> Response:
        return self._idempotent(
            requester, key, f"POST /trips/{trip_id}/confirm", None, lambda: self._confirm(requester, trip_id)
        )

    def modify_trip(self, requester: str, key: str | None, trip_id: str, body) -> Response:
        return self._idempotent(
            requester, key, f"PATCH /trips/{trip_id}", body, lambda: self._modify(requester, trip_id, body)
        )

    def cancel_trip(self, requester: str, key: str | None, trip_id: str, body) -> Response:
        return self._idempotent(
            requester, key, f"POST /trips/{trip_id}/cancel", body, lambda: self._cancel(requester, trip_id, body)
        )

    def subscribe(self, requester: str, key: str | None, body) -> Response:
        return self._idempotent(requester, key, "POST /subscriptions", body, lambda: self._subscribe(requester, body))

    def subscriptions_for(self, requester: str, event_type: str) -> list[dict]:
        return [
            sub
            for owner, sub in self._subscriptions.values()
            if owner == requester and event_type in sub["event_types"]
        ]

    def tick(self) -> None:
        """Apply every transition that has come due."""
        with self._lock:
            for trip in list(self._trips.values()):
                self._advance(trip)
            self._tick_transfer()

    # --- idempotency ------------------------------------------------------------

    def _idempotent(self, requester: str, key: str | None, operation: str, body, handler) -> Response:
        if not key:
            return problem(400, "INVALID_REQUEST", "Missing Idempotency-Key header.")
        fingerprint = hashlib.sha256(json.dumps([operation, body], sort_keys=True).encode()).hexdigest()
        with self._lock:
            stored = self._idempotency.get((requester, key))
            if stored:
                if stored.fingerprint != fingerprint:
                    return problem(
                        409, "IDEMPOTENCY_KEY_REUSED", "This Idempotency-Key was used with a different request."
                    )
                return copy.deepcopy(stored.response)
            response = handler()
            if response[0] < 500:
                self._idempotency[(requester, key)] = _Stored(fingerprint, copy.deepcopy(response))
            return response

    # --- handlers (called with the lock held) ------------------------------------

    def _request_trip(self, requester: str, body) -> Response:
        if refusal := self._check_trip_request(requester, body):
            return refusal
        trip = self._create_trip(requester, body)
        if refusal := self._refusal(trip.body):
            self._set_status(trip, "REJECTED")
            return refusal
        if self.config.decision == "async":
            trip.decide_at = self.clock() + timedelta(seconds=self.config.decision_delay_s)
            return 202, copy.deepcopy(trip.body)
        self._offer(trip)
        return 201, copy.deepcopy(trip.body)

    def _check_trip_request(self, requester: str, body) -> Response | None:
        if errors := schema_errors(body, "trip-request"):
            return problem(400, "INVALID_REQUEST", "; ".join(errors))
        if refusal := self._check_profiles(body):
            return refusal
        if errors := trip_eligibility_problems(body):
            return problem(400, "INVALID_REQUEST", "; ".join(errors))
        if body["requester_id"] != requester:
            return problem(400, "INVALID_REQUEST", "requester_id does not match the authenticated client.")
        if self.config.provider_id and body["provider_id"] != self.config.provider_id:
            return problem(400, "INVALID_REQUEST", f"This endpoint serves provider {self.config.provider_id!r}.")
        if (requester, body["trip_id"]) in self._trips:
            return problem(409, "TRIP_ID_IN_USE", f"trip_id {body['trip_id']!r} already exists.")
        return None

    def _check_profiles(self, body: dict) -> Response | None:
        """Paratransit and NEMT objects (SPEC.md §11): schema and cross-field rules, when present."""
        for profile in ("paratransit", "nemt"):
            if profile in body:
                if profile.upper() not in self.config.profiles:
                    return problem(400, "INVALID_REQUEST", f"This endpoint does not implement the {profile} profile.")
                errors = schema_errors(body, f"{profile}/trip-request") or profile_problems(body)
                if errors:
                    return problem(400, "INVALID_REQUEST", "; ".join(errors))
        return None

    def _create_trip(self, requester: str, body: dict) -> _Trip:
        now = _ts(self.clock())
        trip = _Trip(requester, {**copy.deepcopy(body), "status": "REQUESTED", "created_at": now, "updated_at": now})
        self._trips[(requester, body["trip_id"])] = trip
        self._emit(trip, "TRIP_STATUS_CHANGED", {"status": "REQUESTED"})
        return trip

    def _confirm(self, requester: str, trip_id: str) -> Response:
        trip = self._find(requester, trip_id)
        if not trip:
            return _not_found("trip", trip_id)
        if trip.body["status"] != "OFFERED":
            return _invalid_state(trip_id, trip.body["status"], "confirmed")
        self._mark_confirmed(trip)
        return 200, copy.deepcopy(trip.body)

    def _mark_confirmed(self, trip: _Trip) -> None:
        trip.body.pop("offer_expires_at", None)
        self._set_status(trip, "CONFIRMED")
        trip.schedule_at = self.clock() + timedelta(seconds=self.config.schedule_delay_s)

    def _modify(self, requester: str, trip_id: str, body) -> Response:
        if errors := schema_errors(body, "trip-modification"):
            return problem(400, "INVALID_REQUEST", "; ".join(errors))
        trip = self._find(requester, trip_id)
        if not trip:
            return _not_found("trip", trip_id)
        if trip.body["status"] in TERMINAL or trip.body["status"] == "IN_PROGRESS":
            return _invalid_state(trip_id, trip.body["status"], "modified")
        candidate = copy.deepcopy(trip.body)
        leg = candidate["legs"][0]
        for name in ("pickup_window", "dropoff_window"):
            if name in body:
                leg[name] = body[name]
        for name in ("party", "notes_to_driver"):
            if name in body:
                candidate[name] = body[name]
        if refusal := _window_problem(leg, self.clock()):
            return refusal
        if any(name in body for name in ("pickup_window", "dropoff_window")):
            if refusal := self._booking_refusal(candidate, tender=trip.interchange is not None):
                return refusal
        trip.body, trip.schedule_at = candidate, None
        for name in ("scheduled_pickup", "scheduled_dropoff", "vehicle_ref"):
            leg.pop(name, None)
        if self.config.decision == "async":
            self._set_status(trip, "REQUESTED")
            trip.decide_at = self.clock() + timedelta(seconds=self.config.decision_delay_s)
            return 202, copy.deepcopy(trip.body)
        self._offer(trip)
        return 200, copy.deepcopy(trip.body)

    def _cancel(self, requester: str, trip_id: str, body) -> Response:
        if errors := schema_errors(body, "cancel-request"):
            return problem(400, "INVALID_REQUEST", "; ".join(errors))
        trip = self._find(requester, trip_id)
        if not trip:
            return _not_found("trip", trip_id)
        if trip.body["status"] in TERMINAL:
            return _invalid_state(trip_id, trip.body["status"], "cancelled")
        self._cancel_trip(trip, body["reason"], "REQUESTER", body.get("detail"))
        return 200, copy.deepcopy(trip.body)

    def _cancel_trip(self, trip: _Trip, reason: str, by: str, detail: str | None = None) -> None:
        trip.decide_at = trip.schedule_at = None
        cancellation = {"reason": reason, "cancelled_by": by, "cancelled_at": _ts(self.clock())}
        if detail:
            cancellation["detail"] = detail
        trip.body["cancellation"] = cancellation
        self._set_status(trip, "CANCELLED")
        self._emit(trip, "TRIP_CANCELLED", {"cancellation": cancellation})

    def _subscribe(self, requester: str, body) -> Response:
        if errors := schema_errors(body, "subscription-request"):
            return problem(400, "INVALID_REQUEST", "; ".join(errors))
        sub = {
            "subscription_id": self.new_id("sub"),
            **body,
            "signing_secret": self.new_secret(),
            "created_at": _ts(self.clock()),
        }
        self._subscriptions[sub["subscription_id"]] = (requester, sub)
        return 201, copy.deepcopy(sub)

    # --- state machine helpers ----------------------------------------------------

    def _find(self, requester: str, trip_id: str) -> _Trip | None:
        trip = self._trips.get((requester, trip_id))
        if trip:
            self._advance(trip)
        return trip

    def _advance(self, trip: _Trip) -> None:
        now = self.clock()
        if trip.decide_at and now >= trip.decide_at and trip.body["status"] == "REQUESTED":
            trip.decide_at = None
            self._offer(trip)
        expires = trip.body.get("offer_expires_at")
        if trip.body["status"] == "OFFERED" and expires and now > datetime.fromisoformat(expires):
            self._set_status(trip, "EXPIRED")
        if trip.schedule_at and now >= trip.schedule_at and trip.body["status"] == "CONFIRMED":
            trip.schedule_at = None
            self._schedule(trip)

    def _offer(self, trip: _Trip) -> None:
        leg = trip.leg
        if "pickup_window" in leg:
            window = leg["pickup_window"]
            leg["scheduled_pickup"] = window.get("preferred", window["earliest"])
        else:
            window = leg["dropoff_window"]
            dropoff = datetime.fromisoformat(window.get("preferred", window["earliest"]))
            leg["scheduled_dropoff"] = _ts(dropoff)
            leg["scheduled_pickup"] = _ts(dropoff - timedelta(minutes=45))
        trip.body.setdefault("provider_trip_id", self.new_id(self.provider_id))
        if self.config.offer_ttl_s:
            trip.body["offer_expires_at"] = _ts(self.clock() + timedelta(seconds=self.config.offer_ttl_s))
        self._set_status(trip, "OFFERED")

    def _schedule(self, trip: _Trip) -> None:
        self._set_status(trip, "SCHEDULED")
        trip.leg["vehicle_ref"] = {"vehicle_id": f"{self.provider_id}-veh-1", "description": "Mock vehicle"}
        self._emit_scheduled(trip)

    def _emit_scheduled(self, trip: _Trip) -> None:
        leg = trip.leg
        payload = {k: leg[k] for k in ("scheduled_pickup", "scheduled_dropoff", "vehicle_ref") if k in leg}
        self._emit(trip, "TRIP_SCHEDULED", payload)

    def _set_status(self, trip: _Trip, status: str) -> None:
        previous = trip.body["status"]
        trip.body["status"] = status
        trip.body["updated_at"] = _ts(self.clock())
        self._emit(trip, "TRIP_STATUS_CHANGED", {"status": status, "previous_status": previous})

    def _refusal(self, trip_request: dict, tender: bool = False) -> Response | None:
        leg = trip_request["legs"][0]
        if refusal := _window_problem(leg, self.clock()):
            return refusal
        if refusal := self._booking_refusal(trip_request, tender):
            return refusal
        unsupported = sorted(set(leg.get("service_requirements", [])) & self.config.unsupported_requirements)
        if unsupported:
            return problem(422, "UNSUPPORTED_REQUIREMENT", f"Cannot provide: {', '.join(unsupported)}.")
        refused = sorted(
            {a.get("verification_method", "UNSTATED") for a in trip_request.get("rider_ref", {}).get("eligibility", [])}
            & self.config.refused_verification_methods
        )
        if refused:
            return problem(
                422, "ELIGIBILITY_NOT_ACCEPTED", f"Attestations verified by {', '.join(refused)} are not accepted."
            )
        return None

    def _booking_refusal(self, trip_request: dict, tender: bool = False) -> Response | None:
        """OUTSIDE_BOOKING_WINDOW (SPEC.md §7.1), with bookable_from when the request came too early."""
        window = booking_window_for(self.health()[1], trip_request, tender)
        outside = booking_window_problem(window, trip_time(trip_request), self.clock())
        if not outside:
            return None
        status, body = problem(422, "OUTSIDE_BOOKING_WINDOW", outside["detail"])
        if "bookable_from" in outside:
            body["bookable_from"] = outside["bookable_from"]
        return status, body

    # --- events -------------------------------------------------------------------

    def _emit(
        self,
        trip: _Trip | None,
        event_type: str,
        payload: dict,
        requester: str | None = None,
        trip_id: str | None = None,
    ) -> dict:
        now = self.clock()
        if self._last_event_at and now <= self._last_event_at:  # keep occurred_at strictly increasing
            now = self._last_event_at + timedelta(milliseconds=1)
        self._last_event_at = now
        event = {
            "event_id": self.new_id("evt"),
            "event_type": event_type,
            "occurred_at": now.isoformat(timespec="milliseconds"),
            "provider_id": self.provider_id,
            "schema_version": SPEC_VERSION,
            "payload": copy.deepcopy(payload),
        }
        if trip:
            event["trip_id"] = trip.body["trip_id"]
            event["leg_id"] = trip.leg["leg_id"]
        elif trip_id:
            event["trip_id"] = trip_id
        owner = requester or trip.requester
        self.events.append({"requester": owner, "event": event})
        self.sink(owner, event)
        return event
