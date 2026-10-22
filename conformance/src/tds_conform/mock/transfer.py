"""Transfer profile behaviour for the reference provider (SPEC.md §10).

Mixed into Provider: tenders (§10.2), hold requests (§10.5), milestones (§10.3), ETA
bands and heartbeats (§10.4) and custody handoffs (§10.7). Milestones, delays, no-shows
and failures don't happen on their own in a mock; the `sim_*` methods (exposed under
/_sim) let a demo or test drive a leg through them.
"""

import copy
from datetime import datetime, timedelta

from tds_conform.data import schema_errors
from tds_conform.mock.common import TERMINAL, Response, _not_found, _ts, problem

NEXT_MILESTONE = {
    None: "EN_ROUTE_TO_PICKUP",
    "EN_ROUTE_TO_PICKUP": "ARRIVED_PICKUP",
    "ARRIVED_PICKUP": "RIDER_ONBOARD",
    "RIDER_ONBOARD": "EN_ROUTE_TO_DROPOFF",
    "EN_ROUTE_TO_DROPOFF": "ARRIVED_DROPOFF",
    "ARRIVED_DROPOFF": "LEG_COMPLETED",  # RIDER_HANDED_OFF first when supervised
    "RIDER_HANDED_OFF": "LEG_COMPLETED",
}
BEFORE_PICKUP = {None, "EN_ROUTE_TO_PICKUP", "ARRIVED_PICKUP"}
POLICY_FIELDS = {
    "decision": str,
    "decision_delay_s": float,
    "schedule_delay_s": float,
    "tender_decision": str,
    "reject_reason": str,
    "hold_decision": str,
    "offer_ttl_s": int,
    "unsupported_requirements": frozenset,
    "refused_verification_methods": frozenset,
    "booking_window": dict,
    "booking_window_by_profile": dict,
    "max_hold_s": int,
    "max_auto_hold_s": int,
    "heartbeat_s": int,
}


class TransferMixin:
    # --- operations -------------------------------------------------------------

    def create_tender(self, requester: str, key: str | None, body) -> Response:
        return self._idempotent(requester, key, "POST /tenders", body, lambda: self._create_tender(requester, body))

    def get_tender(self, requester: str, tender_id: str) -> Response:
        with self._lock:
            self._tick_transfer()
            state = self._tenders.get((requester, tender_id))
            return (200, copy.deepcopy(state["tender"])) if state else _not_found("tender", tender_id)

    def request_hold(self, requester: str, key: str | None, trip_id: str, body) -> Response:
        return self._idempotent(
            requester, key, f"POST /trips/{trip_id}/hold", body, lambda: self._request_hold(requester, trip_id, body)
        )

    def get_hold(self, requester: str, trip_id: str, hold_id: str) -> Response:
        with self._lock:
            self._tick_transfer()
            state = self._holds.get((requester, hold_id))
            if not state or state["hold"]["trip_id"] != trip_id:
                return _not_found("hold", hold_id)
            return 200, copy.deepcopy(state["hold"])

    # --- tenders ------------------------------------------------------------------

    def _create_tender(self, requester: str, body) -> Response:
        if errors := schema_errors(body, "transfer/tender-request"):
            return problem(400, "INVALID_REQUEST", "; ".join(errors))
        trip_req, interchange = body["trip"], body["interchange"]
        if refusal := self._check_trip_request(requester, trip_req):
            return refusal
        if (requester, body["tender_id"]) in self._tenders:
            return problem(409, "TRIP_ID_IN_USE", f"tender_id {body['tender_id']!r} already exists.")
        role = _role(interchange, trip_req)
        if role is None:
            return problem(400, "INVALID_REQUEST", "The trip is neither the from nor the to leg of the interchange.")
        if interchange.get("auto_hold_s", 0) > interchange.get("max_hold_s", 0):
            return problem(400, "INVALID_REQUEST", "auto_hold_s must not exceed max_hold_s.")
        if (
            "HAND_TO_HAND" in trip_req["legs"][0].get("service_requirements", [])
            and interchange["protection"] == "PROTECTED"
            and not interchange.get("supervision_required", False)
        ):
            return problem(
                400, "INVALID_REQUEST", "HAND_TO_HAND on a PROTECTED interchange needs supervision_required: true."
            )

        tender = {
            "tender_id": body["tender_id"],
            "trip_id": trip_req["trip_id"],
            "interchange_id": interchange["interchange_id"],
            "respond_by": body["respond_by"],
            "status": "OFFERED",
            "created_at": _ts(self.clock()),
        }
        state = {"tender": tender, "request": copy.deepcopy(body), "requester": requester, "role": role}
        state["decide_at"] = None
        self._tenders[(requester, body["tender_id"])] = state
        if self.config.tender_decision == "IGNORE":
            return 202, copy.deepcopy(tender)
        if self.config.decision == "async":
            state["decide_at"] = self.clock() + timedelta(seconds=self.config.decision_delay_s)
            return 202, copy.deepcopy(tender)
        self._decide_tender(state)
        return 201, copy.deepcopy(tender)

    def _decide_tender(self, state: dict) -> None:
        tender, request, requester = state["tender"], state["request"], state["requester"]
        trip_req, interchange = request["trip"], request["interchange"]
        refusal = self._refusal(trip_req, tender=True)
        if self.config.tender_decision == "REJECT" or refusal:
            tender["status"] = "REJECTED"
            tender["reason"] = refusal[1]["code"] if refusal else self.config.reject_reason
        else:
            max_hold = min(interchange.get("max_hold_s", 0), self.config.max_hold_s)
            terms = {
                "auto_hold_s": min(interchange.get("auto_hold_s", 0), self.config.max_auto_hold_s, max_hold),
                "max_hold_s": max_hold,
            }
            trip = self._create_trip(requester, trip_req)
            trip.interchange, trip.role, trip.accepted_terms = copy.deepcopy(interchange), state["role"], terms
            self._offer(trip)
            self._mark_confirmed(trip)
            tender.update(status="ACCEPTED", accepted_terms=terms, provider_trip_id=trip.body["provider_trip_id"])
        tender["responded_at"] = _ts(self.clock())
        self._emit(None, "TENDER_RESPONSE", {"tender": tender}, requester=requester, trip_id=tender["trip_id"])

    # --- holds --------------------------------------------------------------------

    def _request_hold(self, requester: str, trip_id: str, body) -> Response:
        if errors := schema_errors(body, "transfer/hold-request"):
            return problem(400, "INVALID_REQUEST", "; ".join(errors))
        trip = self._find(requester, trip_id)
        if not trip:
            return _not_found("trip", trip_id)
        if (requester, body["hold_id"]) in self._holds:
            return problem(409, "TRIP_ID_IN_USE", f"hold_id {body['hold_id']!r} already exists.")
        ix = trip.interchange or {}
        if trip.role != "to" or ix.get("interchange_id") != body["interchange_id"]:
            return problem(409, "INVALID_STATE", "This trip is not the outbound leg of that interchange.")
        if trip.body["status"] not in ("CONFIRMED", "SCHEDULED", "IN_PROGRESS") or trip.milestone not in BEFORE_PICKUP:
            return problem(409, "INVALID_STATE", "The outbound leg has already picked up or ended.")

        hold = {
            "hold_id": body["hold_id"],
            "trip_id": trip_id,
            "interchange_id": body["interchange_id"],
            "action": body["action"],
            "requested_until": body["requested_until"],
            "respond_by": body["respond_by"],
            "status": "PENDING",
            "created_at": _ts(self.clock()),
        }
        state = {"hold": hold, "trip": trip, "requester": requester, "decide_at": None}
        self._holds[(requester, body["hold_id"])] = state
        if self.config.hold_decision == "IGNORE":
            return 202, copy.deepcopy(hold)
        if self.config.decision == "async":
            state["decide_at"] = self.clock() + timedelta(seconds=self.config.decision_delay_s)
            return 202, copy.deepcopy(hold)
        self._decide_hold(state)
        return 201, copy.deepcopy(hold)

    def _decide_hold(self, state: dict) -> None:
        hold, trip = state["hold"], state["trip"]
        base = datetime.fromisoformat(trip.leg["scheduled_pickup"])
        wanted = datetime.fromisoformat(hold["requested_until"])
        need = (wanted - base).total_seconds()
        cap = (trip.accepted_terms or {}).get("max_hold_s", 0)
        policy = self.config.hold_decision
        if policy == "GRANT" or (policy == "AUTO" and need <= cap):
            status, new, reason = "GRANTED", max(wanted, base), None
        elif policy == "PARTIAL":  # a counter-offer: halfway
            status, new, reason = "PARTIAL", base + timedelta(seconds=max(0, need) / 2), "DOWNSTREAM_COMMITMENT"
        elif policy == "AUTO" and cap > 0:
            status, new, reason = "PARTIAL", base + timedelta(seconds=cap), "EXCEEDS_MAX_HOLD"
        else:
            status, new = "DENIED", None
            reason = "DOWNSTREAM_COMMITMENT" if policy == "DENY" else "EXCEEDS_MAX_HOLD"
        hold["status"] = status
        if reason:
            hold["reason"] = reason
        if new is not None:
            hold["scheduled_pickup"] = _ts(new)
            if hold["action"] == "HOLD":
                hold["held_until"] = _ts(new)
            trip.leg["scheduled_pickup"] = _ts(new)
            trip.body["updated_at"] = _ts(self.clock())
            self._emit_scheduled(trip)
        hold["responded_at"] = _ts(self.clock())
        self._emit(trip, "HOLD_RESPONSE", {"hold": hold})

    # --- timers ---------------------------------------------------------------------

    def _tick_transfer(self) -> None:
        now = self.clock()
        for state in self._tenders.values():
            tender = state["tender"]
            if tender["status"] != "OFFERED":
                continue
            if state["decide_at"] and now >= state["decide_at"]:
                state["decide_at"] = None
                self._decide_tender(state)
            elif now > datetime.fromisoformat(tender["respond_by"]):
                tender.update(status="EXPIRED", responded_at=_ts(now))
                self._emit(
                    None, "TENDER_RESPONSE", {"tender": tender}, requester=state["requester"], trip_id=tender["trip_id"]
                )
        for state in self._holds.values():
            hold = state["hold"]
            if hold["status"] != "PENDING":
                continue
            if state["decide_at"] and now >= state["decide_at"]:
                state["decide_at"] = None
                self._decide_hold(state)
            elif now > datetime.fromisoformat(hold["respond_by"]):
                hold.update(status="EXPIRED", responded_at=_ts(now))
                self._emit(state["trip"], "HOLD_RESPONSE", {"hold": hold})
        for trip in self._trips.values():
            heartbeat = self._heartbeat_s(trip)
            if heartbeat and (trip.last_eta_at is None or (now - trip.last_eta_at).total_seconds() >= heartbeat):
                self._emit_eta(trip)

    def _heartbeat_s(self, trip) -> int | None:
        """Seconds between ETA updates owed for this trip, or None if no heartbeat is owed."""
        ix = trip.interchange
        if not ix or trip.role != "from" or ix.get("protection") != "PROTECTED":
            return None
        if trip.body["status"] != "IN_PROGRESS" or trip.milestone in ("ARRIVED_DROPOFF", "RIDER_HANDED_OFF"):
            return None
        return self.config.heartbeat_s or ix.get("heartbeat_s", 60)

    # --- simulation controls (/_sim) -------------------------------------------------

    def sim_trip(self, trip_id: str):
        return next((t for t in self._trips.values() if t.body["trip_id"] == trip_id), None)

    def sim_advance(self, trip_id: str, body: dict | None) -> Response:
        """Move a leg to its next milestone, or through to `until` (e.g. {"until": "ARRIVED_DROPOFF"})."""
        body = body or {}
        with self._lock:
            trip = self.sim_trip(trip_id)
            if not trip:
                return _not_found("trip", trip_id)
            if trip.body["status"] == "CONFIRMED":
                trip.schedule_at = None
                self._schedule(trip)
            if trip.body["status"] not in ("SCHEDULED", "IN_PROGRESS"):
                return problem(409, "INVALID_STATE", f"Trip is {trip.body['status']}.")
            start = len(self.events)
            while True:
                milestone = self._next_milestone(trip)
                self._milestone(trip, milestone, body)
                if milestone == "LEG_COMPLETED" or body.get("until") in (None, milestone):
                    break
            return 200, {"trip": copy.deepcopy(trip.body), "emitted": [e["event"] for e in self.events[start:]]}

    def sim_delay(self, trip_id: str, body: dict | None) -> Response:
        """Make a leg late: {"seconds": 600, "cause": "TRAFFIC"} shifts its ETA and emits ETA_UPDATE."""
        body = body or {}
        with self._lock:
            trip = self.sim_trip(trip_id)
            if not trip:
                return _not_found("trip", trip_id)
            trip.delay_s += int(body.get("seconds", 300))
            trip.delay_cause = body.get("cause", "UNKNOWN")
            return 200, {"trip": copy.deepcopy(trip.body), "emitted": [self._emit_eta(trip)]}

    def sim_end(self, trip_id: str, outcome: str, body: dict | None) -> Response:
        """End a leg as RIDER_NO_SHOW (after ARRIVED_PICKUP) or LEG_FAILED ({"cause": ...})."""
        with self._lock:
            trip = self.sim_trip(trip_id)
            if not trip:
                return _not_found("trip", trip_id)
            if trip.body["status"] in TERMINAL:
                return problem(409, "INVALID_STATE", f"Trip is {trip.body['status']}.")
            if outcome == "no-show" and trip.milestone != "ARRIVED_PICKUP":
                return problem(409, "INVALID_STATE", "A no-show needs ARRIVED_PICKUP first.")
            start = len(self.events)
            if outcome == "no-show":
                self._emit(trip, "MILESTONE", {"milestone": "RIDER_NO_SHOW", "location": trip.leg["pickup"]})
                trip.milestone = "RIDER_NO_SHOW"
                self._set_status(trip, "NO_SHOW")
            else:
                cause = (body or {}).get("cause", "UNKNOWN")
                self._emit(trip, "MILESTONE", {"milestone": "LEG_FAILED", "delay_cause": cause})
                trip.milestone = "LEG_FAILED"
                self._set_status(trip, "FAILED")
            return 200, {"trip": copy.deepcopy(trip.body), "emitted": [e["event"] for e in self.events[start:]]}

    def sim_cancel(self, trip_id: str, body: dict | None) -> Response:
        """The provider cancels a trip it can no longer serve ({"reason": "PROVIDER_UNABLE"})."""
        with self._lock:
            trip = self.sim_trip(trip_id)
            if not trip:
                return _not_found("trip", trip_id)
            if trip.body["status"] in TERMINAL or trip.body["status"] == "IN_PROGRESS":
                return problem(409, "INVALID_STATE", f"Trip is {trip.body['status']}.")
            start = len(self.events)
            self._cancel_trip(trip, (body or {}).get("reason", "PROVIDER_UNABLE"), "PROVIDER")
            return 200, {"trip": copy.deepcopy(trip.body), "emitted": [e["event"] for e in self.events[start:]]}

    def sim_policy(self, body: dict | None) -> Response:
        """Change how this provider answers, e.g. {"tender_decision": "IGNORE", "hold_decision": "PARTIAL"}."""
        with self._lock:
            for key, value in (body or {}).items():
                if key not in POLICY_FIELDS:
                    return problem(400, "INVALID_REQUEST", f"Unknown policy {key!r}; one of {sorted(POLICY_FIELDS)}.")
                setattr(self.config, key, None if value is None else POLICY_FIELDS[key](value))
            values = {k: getattr(self.config, k) for k in POLICY_FIELDS}
            return 200, {k: sorted(v) if isinstance(v, frozenset) else v for k, v in values.items()}

    def sim_events(self, trip_id: str | None = None) -> Response:
        with self._lock:
            events = [e["event"] for e in self.events if trip_id in (None, e["event"].get("trip_id"))]
            return 200, {"events": copy.deepcopy(events)}

    # --- milestones, ETA, custody ------------------------------------------------------

    def _supervised(self, trip) -> bool:
        if "HAND_TO_HAND" in trip.leg.get("service_requirements", []):
            return True
        return bool(trip.interchange and trip.interchange.get("supervision_required"))

    def _next_milestone(self, trip) -> str:
        if trip.milestone == "ARRIVED_DROPOFF" and self._supervised(trip):
            return "RIDER_HANDED_OFF"
        return NEXT_MILESTONE[trip.milestone]

    def _milestone(self, trip, milestone: str, options: dict) -> None:
        leg = trip.leg
        if milestone == "EN_ROUTE_TO_PICKUP":
            self._set_status(trip, "IN_PROGRESS")
        if milestone == "RIDER_ONBOARD" and self._supervised(trip):
            self._custody(trip, at="pickup", options=options)
        payload = {"milestone": milestone}
        if milestone in ("ARRIVED_PICKUP", "RIDER_ONBOARD"):
            payload["location"] = leg["pickup"]
        elif milestone in ("ARRIVED_DROPOFF", "RIDER_HANDED_OFF"):
            payload["location"] = leg["dropoff"]
        self._emit(trip, "MILESTONE", payload)
        trip.milestone = milestone
        if milestone == "RIDER_HANDED_OFF":
            self._custody(trip, at="dropoff", options=options)
        if milestone in ("EN_ROUTE_TO_PICKUP", "EN_ROUTE_TO_DROPOFF"):
            self._emit_eta(trip)
        if milestone == "LEG_COMPLETED":
            self._set_status(trip, "COMPLETED")

    def _custody(self, trip, at: str, options: dict) -> None:
        me = {"role": "DRIVER", "provider_id": self.provider_id}
        ix = trip.interchange or {}
        if at == "pickup":
            if trip.role == "to":
                giver = {"role": "DRIVER", "provider_id": ix["from"]["provider_id"]}
            else:
                giver = {"role": options.get("handoff_from", "CAREGIVER")}
            giver_taker, location = (giver, me), trip.leg["pickup"]
        else:
            if trip.role == "from":
                taker = {"role": "DRIVER", "provider_id": ix["to"]["provider_id"]}
            else:
                taker = {"role": options.get("handoff_to", "FACILITY_STAFF")}
            giver_taker, location = (me, taker), trip.leg["dropoff"]
        driver_to_driver = all(p["role"] == "DRIVER" for p in giver_taker)
        handoff = {
            "handoff_from": giver_taker[0],
            "handoff_to": giver_taker[1],
            "timestamp": _ts(self.clock()),
            "location": location,
            "method": options.get("method", "CODE_EXCHANGE" if driver_to_driver else "DRIVER_CONFIRM"),
            "caregiver_notified": True,
        }
        self._emit(trip, "CUSTODY_HANDOFF", {"handoff": handoff})

    def _emit_eta(self, trip) -> dict:
        leg, now = trip.leg, self.clock()
        target = "PICKUP" if trip.milestone in BEFORE_PICKUP else "DROPOFF"
        pickup = datetime.fromisoformat(leg["scheduled_pickup"])
        base = (
            pickup
            if target == "PICKUP"
            else datetime.fromisoformat(leg.get("scheduled_dropoff") or _ts(pickup + timedelta(minutes=40)))
        )
        likely = max(base + timedelta(seconds=trip.delay_s), now + timedelta(minutes=1))  # never in the past
        eta = {
            "target": target,
            "earliest": _ts(max(now, likely - timedelta(minutes=3))),
            "likely": _ts(likely),
            "latest": _ts(likely + timedelta(seconds=300 + trip.delay_s // 2)),
            "confidence": 0.9 if trip.delay_s == 0 else 0.7,
            "computed_at": _ts(now),
            "source": "VEHICLE_POSITION" if trip.body["status"] == "IN_PROGRESS" else "SCHEDULE",
        }
        if trip.delay_s:
            eta["delay_cause"] = trip.delay_cause or "UNKNOWN"
        trip.last_eta_at = now
        return self._emit(trip, "ETA_UPDATE", {"eta": eta})


def _role(interchange: dict, trip_req: dict) -> str | None:
    leg_id = trip_req["legs"][0]["leg_id"]
    for role in ("from", "to"):
        ref = interchange[role]
        if (ref["provider_id"], ref["trip_id"], ref["leg_id"]) == (
            trip_req["provider_id"],
            trip_req["trip_id"],
            leg_id,
        ):
            return role
    return None
