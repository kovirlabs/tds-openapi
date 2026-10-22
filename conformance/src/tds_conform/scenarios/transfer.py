"""Transfer profile scenarios that any provider can be tested on through the API (SPEC.md §10, §13).

milestone_sequence, custody_required and eta_heartbeat_staleness need real trips to run;
they are checked offline on captured events with `tds-conform check-events`.
"""

from datetime import UTC, datetime, timedelta

from tds_conform.runner import Context, Inconclusive, scenario

GRACE_S = 30  # allowance for clock skew around respond_by


def _deadline(ctx: Context) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=max(120, 2 * ctx.typical_response_s))


def _tender_body(ctx: Context, trip: dict, max_hold_s: int = 600, auto_hold_s: int = 120) -> dict:
    """Tender `trip` as the onward leg of an interchange whose feeder is a fictional other provider."""
    leg = trip["legs"][0]
    return {
        "tender_id": f"tn-{trip['trip_id']}",
        "trip": trip,
        "interchange": {
            "interchange_id": f"ix-{trip['trip_id']}",
            "from": {"provider_id": "conform-feeder", "trip_id": f"{trip['trip_id']}-feeder", "leg_id": "leg-1"},
            "to": {"provider_id": trip["provider_id"], "trip_id": trip["trip_id"], "leg_id": leg["leg_id"]},
            "transfer_location": leg["pickup"],
            "min_connection_s": 300,
            "max_hold_s": max_hold_s,
            "auto_hold_s": auto_hold_s,
            "protection": "PROTECTED",
        },
        "respond_by": _deadline(ctx).isoformat(timespec="seconds"),
    }


def _tender(ctx: Context, body: dict):
    """POST the tender and wait for an answer; returns the final tender body."""
    step = ctx.call("tender a leg", "POST", "/tenders", body=body, expect=(201, 202))
    step.require(isinstance(step.body, dict) and step.status in (201, 202), "tender accepted for processing")
    allowed = {"ACCEPTED", "REJECTED"} if step.status == 201 else {"OFFERED"}
    step.check(
        step.body.get("status") in allowed,
        f"status is {' or '.join(sorted(allowed))}",
        f"got {step.body.get('status')!r} with HTTP {step.status}",
    )
    tender = step.body
    if tender.get("status") == "OFFERED":
        respond_by = datetime.fromisoformat(body["respond_by"])
        wait = (respond_by - datetime.now(UTC)).total_seconds() + GRACE_S
        step = ctx.poll("poll tender", "/tenders/{tender_id}", "OFFERED", deadline_s=wait, tender_id=body["tender_id"])
        step.check(
            step.body.get("status") != "OFFERED",
            "answered or EXPIRED by respond_by",
            f"still OFFERED {GRACE_S}s after respond_by",
        )
        tender = step.body
    return tender


@scenario("tender_deadline", "transfer", "Tenders are answered or expire by respond_by; acceptance creates the trip")
def tender_deadline(ctx: Context) -> None:
    trip = ctx.new_trip(tender=True)
    body = _tender_body(ctx, trip)
    tender = _tender(ctx, body)
    status = tender.get("status")
    if status == "EXPIRED":
        step = ctx.call("no trip after expiry", "GET", "/trips/{trip_id}", expect=(404,), trip_id=trip["trip_id"])
        return
    if status == "REJECTED":
        step = ctx.call("no trip after rejection", "GET", "/trips/{trip_id}", expect=(404,), trip_id=trip["trip_id"])
        step.check("reason" in tender, "rejected tender carries a reason")
        raise Inconclusive(f"provider rejected the test tender ({tender.get('reason')}); supply --trip-template")

    terms = tender.get("accepted_terms") or {}
    step = ctx.result.steps[-1]
    step.check("accepted_terms" in tender, "accepted tender carries accepted_terms")
    requested = body["interchange"]
    step.check(
        terms.get("max_hold_s", 0) <= requested["max_hold_s"],
        "accepted max_hold_s ≤ requested",
        f"{terms.get('max_hold_s')} > {requested['max_hold_s']}",
    )
    step.check(
        terms.get("auto_hold_s", 0) <= min(requested["auto_hold_s"], terms.get("max_hold_s", 0)),
        "accepted auto_hold_s ≤ requested and ≤ accepted max_hold_s",
        str(terms),
    )

    step = ctx.call(
        "accepted tender created the trip", "GET", "/trips/{trip_id}", expect=(200,), trip_id=trip["trip_id"]
    )
    step.check(
        step.body.get("status") in ("CONFIRMED", "SCHEDULED"),
        "trip is CONFIRMED or SCHEDULED",
        f"got {step.body.get('status')!r}",
    )

    step = ctx.call("same tender_id, new key", "POST", "/tenders", body=body, expect=(409,))
    step.check_equal(step.body.get("code") if isinstance(step.body, dict) else None, "TRIP_ID_IN_USE", "code")

    ctx.call(
        "clean up", "POST", "/trips/{trip_id}/cancel", body={"reason": "OTHER"}, expect=(200,), trip_id=trip["trip_id"]
    )


@scenario("hold_request", "transfer", "Hold requests on an onward leg are answered or expire; grants move the pickup")
def hold_request(ctx: Context) -> None:
    trip = ctx.new_trip(tender=True)
    body = _tender_body(ctx, trip, max_hold_s=900, auto_hold_s=120)
    tender = _tender(ctx, body)
    if tender.get("status") != "ACCEPTED":
        raise Inconclusive(f"test tender was {tender.get('status')}; a hold needs an accepted onward leg")
    trip_id, ix_id = trip["trip_id"], body["interchange"]["interchange_id"]
    step = ctx.call("read onward trip", "GET", "/trips/{trip_id}", expect=(200,), trip_id=trip_id)
    pickup = step.body.get("legs", [{}])[0].get("scheduled_pickup")
    step.require(pickup is not None, "onward trip has a scheduled_pickup")

    hold_body = {
        "hold_id": f"hd-{trip_id}",
        "interchange_id": ix_id,
        "action": "HOLD",
        "requested_until": (datetime.fromisoformat(pickup) + timedelta(minutes=5)).isoformat(timespec="seconds"),
        "respond_by": _deadline(ctx).isoformat(timespec="seconds"),
    }
    step = ctx.call(
        "request a 5-minute hold", "POST", "/trips/{trip_id}/hold", body=hold_body, expect=(201, 202), trip_id=trip_id
    )
    step.require(isinstance(step.body, dict) and step.status in (201, 202), "hold accepted for processing")
    allowed = {"GRANTED", "PARTIAL", "DENIED"} if step.status == 201 else {"PENDING"}
    step.check(
        step.body.get("status") in allowed,
        f"status is {' or '.join(sorted(allowed))}",
        f"got {step.body.get('status')!r} with HTTP {step.status}",
    )
    hold = step.body
    if hold.get("status") == "PENDING":
        wait = (datetime.fromisoformat(hold_body["respond_by"]) - datetime.now(UTC)).total_seconds() + GRACE_S
        step = ctx.poll(
            "poll hold",
            "/trips/{trip_id}/holds/{hold_id}",
            "PENDING",
            deadline_s=wait,
            trip_id=trip_id,
            hold_id=hold_body["hold_id"],
        )
        step.check(
            step.body.get("status") != "PENDING",
            "answered or EXPIRED by respond_by",
            f"still PENDING {GRACE_S}s after respond_by",
        )
        hold = step.body

    if hold.get("status") in ("GRANTED", "PARTIAL"):
        step.check("scheduled_pickup" in hold, "granted hold carries scheduled_pickup")
        after = ctx.call("pickup moved", "GET", "/trips/{trip_id}", expect=(200,), trip_id=trip_id)
        after.check_equal(
            after.body.get("legs", [{}])[0].get("scheduled_pickup"),
            hold.get("scheduled_pickup"),
            "trip scheduled_pickup matches the hold",
        )
    elif hold.get("status") == "DENIED":
        step.check("reason" in hold, "denied hold carries a reason")

    wrong = hold_body | {"hold_id": f"hd-{trip_id}-wrong", "interchange_id": f"{ix_id}-unknown"}
    step = ctx.call(
        "hold on the wrong interchange", "POST", "/trips/{trip_id}/hold", body=wrong, expect=(409,), trip_id=trip_id
    )
    step.check_equal(step.body.get("code") if isinstance(step.body, dict) else None, "INVALID_STATE", "code")

    ctx.call("clean up", "POST", "/trips/{trip_id}/cancel", body={"reason": "OTHER"}, expect=(200,), trip_id=trip_id)
