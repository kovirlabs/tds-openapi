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
import math
import secrets
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from tds_conform import SPEC_VERSION
from tds_conform.booking import booking_window_for, booking_window_problem, trip_time
from tds_conform.data import schema_errors
from tds_conform.eligibility import trip_eligibility_problems
from tds_conform.events import new_standard_secret
from tds_conform.mock.common import (
    TERMINAL,
    Response,
    _invalid_state,
    _not_found,
    _ts,
    _window_problem,
    backpressure,
    problem,
)
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
    # Subscription secret rotation (SPEC.md §12.1), declared on /health as secret_rotation.
    secret_rotation: bool = True
    default_overlap_s: int = 86400
    max_overlap_s: int = 7 * 86400
    max_active_secrets: int = 5
    # Back-pressure (SPEC.md §7.2): {"requests": N, "per_s": S} lets each requester make N
    # authenticated calls per S seconds (sliding window); more get 429 RATE_LIMITED with
    # Retry-After. None: no limit. 503s are switched on with /_sim/<id>/unavailable.
    rate_limit: dict | None = None


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
class _Secret:
    secret_id: str
    value: str
    created_at: datetime
    expires_at: datetime | None = None

    def active(self, now: datetime) -> bool:
        return self.expires_at is None or now < self.expires_at

    def public(self) -> dict:
        body = {"secret_id": self.secret_id, "created_at": _ts(self.created_at)}
        if self.expires_at is not None:
            body["expires_at"] = _ts(self.expires_at)
        return body


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
    new_secret: Callable[[], str] = new_standard_secret
    events: list[dict] = field(default_factory=list)
    _trips: dict[tuple[str, str], _Trip] = field(default_factory=dict)
    _idempotency: dict[tuple[str, str], _Stored] = field(default_factory=dict)
    _subscriptions: dict[str, tuple[str, dict]] = field(default_factory=dict)
    _secrets: dict[str, list[_Secret]] = field(default_factory=dict)  # by subscription_id, newest first
    _tenders: dict[tuple[str, str], dict] = field(default_factory=dict)
    _holds: dict[tuple[str, str], dict] = field(default_factory=dict)
    _lock: threading.RLock = field(default_factory=threading.RLock)
    _last_event_at: datetime | None = None
    _calls: dict[str, deque] = field(default_factory=dict)  # recent call times per requester (rate_limit)
    _unavailable_until: datetime | None = None

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
        if self.config.secret_rotation:
            body["secret_rotation"] = True
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

    def rotate_secret(self, requester: str, key: str | None, subscription_id: str, body) -> Response:
        return self._idempotent(
            requester,
            key,
            f"POST /subscriptions/{subscription_id}/secrets",
            body,
            lambda: self._rotate_secret(requester, subscription_id, body),
        )

    def list_secrets(self, requester: str, subscription_id: str) -> Response:
        with self._lock:
            if refusal := self._check_secret_api(requester, subscription_id):
                return refusal
            return 200, self._secret_list(subscription_id)

    def retire_secret(self, requester: str, key: str | None, subscription_id: str, secret_id: str) -> Response:
        return self._idempotent(
            requester,
            key,
            f"DELETE /subscriptions/{subscription_id}/secrets/{secret_id}",
            None,
            lambda: self._retire_secret(requester, subscription_id, secret_id),
        )

    def signing_secrets(self, subscription: dict) -> list[str]:
        """Secrets to sign a delivery with now, newest first (SPEC.md §12, §12.1)."""
        with self._lock:
            now = self.clock()
            older = self._secrets.get(subscription["subscription_id"], [])[1:]
            return [subscription["signing_secret"], *(s.value for s in older if s.active(now))]

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

    # --- back-pressure (SPEC.md §7.2) ------------------------------------------------

    def admit(self, requester: str | None) -> Response | None:
        """Whether to take this call: None, or the 503/429 to answer instead (nothing is processed,
        so a retry with the same Idempotency-Key is a first attempt). `requester` is None for
        unauthenticated calls (/health), which are never rate limited."""
        with self._lock:
            now = self.clock()
            if self._unavailable_until is not None and now < self._unavailable_until:
                wait = max(1, math.ceil((self._unavailable_until - now).total_seconds()))
                return backpressure(
                    503, "TEMPORARILY_UNAVAILABLE", "The provider cannot take requests for now. Retry later.", wait
                )
            limit = self.config.rate_limit
            if requester is None or not limit:
                return None
            window = timedelta(seconds=limit["per_s"])
            calls = self._calls.setdefault(requester, deque())
            while calls and calls[0] <= now - window:
                calls.popleft()
            if len(calls) >= limit["requests"]:
                wait = max(1, math.ceil((calls[0] + window - now).total_seconds()))
                detail = f"More than {limit['requests']} requests in {limit['per_s']} s from this client."
                return backpressure(429, "RATE_LIMITED", detail, wait)
            calls.append(now)
            return None

    def sim_unavailable(self, body) -> Response:
        """Answer every call with 503 TEMPORARILY_UNAVAILABLE for `for_s` seconds ({"for_s": 0} ends it)."""
        for_s = (body or {}).get("for_s") if isinstance(body, dict) else None
        if not isinstance(for_s, int) or isinstance(for_s, bool) or for_s < 0:
            return problem(400, "INVALID_REQUEST", 'Send {"for_s": <seconds, 0 or more>}.')
        with self._lock:
            self._unavailable_until = self.clock() + timedelta(seconds=for_s) if for_s else None
            until = self._unavailable_until
            return 200, {"unavailable_until": _ts(until) if until else None}

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
        # **body echoes `cloudevents`: the reference provider supports the CloudEvents headers.
        now = self.clock()
        sub = {
            "subscription_id": self.new_id("sub"),
            **body,
            "signing_secret": self.new_secret(),
            "created_at": _ts(now),
        }
        # Secret ids are scoped to the subscription (sec-1, sec-2, ...), so they use no global ids.
        secret = _Secret("sec-1", sub["signing_secret"], now)
        if self.config.secret_rotation:
            sub["signing_secret_id"] = secret.secret_id
        self._subscriptions[sub["subscription_id"]] = (requester, sub)
        self._secrets[sub["subscription_id"]] = [secret]
        return 201, copy.deepcopy(sub)

    # --- subscription secrets (SPEC.md §12.1) ---------------------------------------

    def _check_secret_api(self, requester: str, subscription_id: str) -> Response | None:
        if not self.config.secret_rotation:
            return problem(404, "NOT_FOUND", "This endpoint does not rotate secrets over the API (secret_rotation).")
        owned = self._subscriptions.get(subscription_id)
        if not owned or owned[0] != requester:
            return _not_found("subscription", subscription_id)
        return None

    def _secret_list(self, subscription_id: str) -> dict:
        now = self.clock()
        active = [s.public() for s in self._secrets[subscription_id] if s.active(now)]
        return {"subscription_id": subscription_id, "secrets": active}

    def _rotate_secret(self, requester: str, subscription_id: str, body) -> Response:
        body = {} if body is None else body
        if errors := schema_errors(body, "secret-rotation-request"):
            return problem(400, "INVALID_REQUEST", "; ".join(errors))
        if refusal := self._check_secret_api(requester, subscription_id):
            return refusal
        now, secrets_ = self.clock(), self._secrets[subscription_id]
        if sum(s.active(now) for s in secrets_) >= self.config.max_active_secrets:
            return problem(
                409,
                "INVALID_STATE",
                f"Subscription {subscription_id!r} already has {self.config.max_active_secrets} active secrets; "
                "retire one first.",
            )
        overlap = min(body.get("overlap_s", self.config.default_overlap_s), self.config.max_overlap_s)
        until = now + timedelta(seconds=overlap)
        for old in secrets_:
            if old.active(now) and (old.expires_at is None or until < old.expires_at):
                old.expires_at = until
        new = _Secret(f"sec-{len(secrets_) + 1}", self.new_secret(), now)
        secrets_.insert(0, new)
        self._subscriptions[subscription_id][1]["signing_secret"] = new.value
        answer = self._secret_list(subscription_id)
        answer["secrets"][0]["signing_secret"] = new.value
        return 201, answer

    def _retire_secret(self, requester: str, subscription_id: str, secret_id: str) -> Response:
        if refusal := self._check_secret_api(requester, subscription_id):
            return refusal
        secrets_ = self._secrets[subscription_id]
        secret = next((s for s in secrets_ if s.secret_id == secret_id), None)
        if secret is None:
            return _not_found("secret", secret_id)
        if secret is secrets_[0]:
            return problem(
                409, "INVALID_STATE", f"{secret_id!r} is the newest secret of {subscription_id!r}; rotate first."
            )
        now = self.clock()
        if secret.active(now):
            secret.expires_at = now
        return 200, secret.public()

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
