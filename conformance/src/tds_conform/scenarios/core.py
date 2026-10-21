"""Core profile scenarios (SPEC.md §7, §8.1, §13)."""

from tds_conform.runner import Context, Inconclusive, scenario

TRIP = "/trips/{trip_id}"


def _request_and_decide(ctx: Context, trip: dict, label: str = "request trip"):
    """POST a trip and, if the provider decides asynchronously, wait for the decision. Returns the trip body."""
    step = ctx.call(label, "POST", "/trips", body=trip, expect=(201, 202, 422))
    if step.status == 422:
        raise Inconclusive(f"provider refused the test trip ({step.body.get('code')}); supply --trip-template")
    step.require(isinstance(step.body, dict), "response is a trip")
    allowed = {"OFFERED", "CONFIRMED"} if step.status == 201 else {"REQUESTED"}
    step.check(
        step.body.get("status") in allowed,
        f"status is {' or '.join(sorted(allowed))}",
        f"got {step.body.get('status')!r} with HTTP {step.status}",
    )
    for name in ("trip_id", "requester_id", "provider_id"):
        step.check_equal(step.body.get(name), trip[name], f"{name} echoed")
    sent, got = trip["legs"][0]["pickup"], (step.body.get("legs") or [{}])[0].get("pickup", {})
    step.check_equal((got.get("lat"), got.get("lon")), (sent["lat"], sent["lon"]), "pickup location preserved")
    body = step.body
    if body.get("status") == "REQUESTED":
        body = ctx.wait_for_decision(trip["trip_id"]).body
    if body.get("status") == "REJECTED":
        raise Inconclusive("provider rejected the test trip asynchronously; supply --trip-template")
    return body


@scenario("book_confirm_cancel", "core", "Request, confirm, read and cancel a trip; terminal states are enforced")
def book_confirm_cancel(ctx: Context) -> None:
    ctx.call("health", "GET", "/health", expect=(200,), auth=False)
    trip = ctx.new_trip()
    trip_id = trip["trip_id"]
    body = _request_and_decide(ctx, trip)

    if body.get("status") == "OFFERED":
        step = ctx.call("confirm offer", "POST", TRIP + "/confirm", expect=(200,), trip_id=trip_id)
        step.require(step.status == 200, "offer can be confirmed")
        step.check_equal(step.body.get("status"), "CONFIRMED", "status is CONFIRMED")

    step = ctx.call("read confirmed trip", "GET", TRIP, expect=(200,), trip_id=trip_id)
    step.check(
        step.body.get("status") in ("CONFIRMED", "SCHEDULED"),
        "status is CONFIRMED or SCHEDULED",
        f"got {step.body.get('status')!r}",
    )

    step = ctx.call("read without credentials", "GET", TRIP, expect=(401,), auth=False, trip_id=trip_id)
    step.check_equal(step.body.get("code") if isinstance(step.body, dict) else None, "UNAUTHORIZED", "code")

    step = ctx.call(
        "cancel", "POST", TRIP + "/cancel", body={"reason": "RIDER_REQUEST"}, expect=(200,), trip_id=trip_id
    )
    step.require(step.status == 200, "trip can be cancelled")
    step.check_equal(step.body.get("status"), "CANCELLED", "status is CANCELLED")
    cancellation = step.body.get("cancellation") or {}
    step.check_equal(cancellation.get("reason"), "RIDER_REQUEST", "cancellation.reason recorded")
    step.check_equal(cancellation.get("cancelled_by"), "REQUESTER", "cancellation.cancelled_by is REQUESTER")

    step = ctx.call("read cancelled trip", "GET", TRIP, expect=(200,), trip_id=trip_id)
    step.check_equal(step.body.get("status"), "CANCELLED", "status stays CANCELLED")

    for label, route, body in [
        ("cancel again", TRIP + "/cancel", {"reason": "RIDER_REQUEST"}),
        ("confirm after cancel", TRIP + "/confirm", None),
    ]:
        step = ctx.call(label, "POST", route, body=body, expect=(409,), trip_id=trip_id)
        step.check_equal(_code(step.body), "INVALID_STATE", "code is INVALID_STATE")

    step = ctx.call("read unknown trip", "GET", TRIP, expect=(404,), trip_id=f"{trip_id}-missing")
    step.check_equal(_code(step.body), "NOT_FOUND", "code is NOT_FOUND")


@scenario("idempotency", "core", "Idempotency-Key replays, conflicts and duplicate trip ids")
def idempotency(ctx: Context) -> None:
    trip = ctx.new_trip()
    key = f"conform-idem-{trip['trip_id']}"
    first = ctx.call("request trip", "POST", "/trips", body=trip, key=key, expect=(201, 202))
    first.require(first.status in (201, 202), "trip accepted")

    replay = ctx.call("replay same key and body", "POST", "/trips", body=trip, key=key, expect=(first.status,))
    replay.check_equal(replay.body, first.body, "replay returns the original response body")

    changed = trip | {"notes_to_driver": "Different body, same key."}
    step = ctx.call("same key, different body", "POST", "/trips", body=changed, key=key, expect=(409,))
    step.check_equal(_code(step.body), "IDEMPOTENCY_KEY_REUSED", "code is IDEMPOTENCY_KEY_REUSED")

    step = ctx.call("new key, same trip_id", "POST", "/trips", body=trip, expect=(409,))
    step.check_equal(_code(step.body), "TRIP_ID_IN_USE", "code is TRIP_ID_IN_USE")

    step = ctx.call("no Idempotency-Key", "POST", "/trips", body=ctx.new_trip(), key=None, expect=(400,))
    step.check_equal(_code(step.body), "INVALID_REQUEST", "code is INVALID_REQUEST")

    cancel_key = f"{key}-cancel"
    body = {"reason": "DUPLICATE"}
    first = ctx.call(
        "cancel", "POST", TRIP + "/cancel", body=body, key=cancel_key, expect=(200,), trip_id=trip["trip_id"]
    )
    again = ctx.call(
        "retry cancel with same key",
        "POST",
        TRIP + "/cancel",
        body=body,
        key=cancel_key,
        expect=(200,),
        trip_id=trip["trip_id"],
    )
    again.check_equal(again.body, first.body, "retried cancel replays the original 200, not a 409")


def _code(body) -> str | None:
    return body.get("code") if isinstance(body, dict) else None


@scenario("webhook_delivery", "core", "Signed, schema-valid events are delivered for every change, including sync ones")
def webhook_delivery(ctx: Context) -> None:
    from tds_conform.events import event_errors, verify

    receiver = ctx.settings.receiver
    if receiver is None:
        raise Inconclusive("no webhook receiver; run with --listen (and --callback-url if the provider is remote)")
    types = ["TRIP_STATUS_CHANGED", "TRIP_SCHEDULED", "TRIP_CANCELLED"]
    step = ctx.call(
        "subscribe", "POST", "/subscriptions", body={"callback_url": receiver.url, "event_types": types}, expect=(201,)
    )
    step.require(step.status == 201 and "signing_secret" in step.body, "subscription returns a signing_secret")
    secret = step.body["signing_secret"]

    trip = ctx.new_trip()
    body = _request_and_decide(ctx, trip)
    trip_id = trip["trip_id"]
    if body.get("status") == "OFFERED":
        ctx.call("confirm", "POST", TRIP + "/confirm", expect=(200,), trip_id=trip_id)
    ctx.call("cancel", "POST", TRIP + "/cancel", body={"reason": "RIDER_REQUEST"}, expect=(200,), trip_id=trip_id)

    def cancelled(events):
        ours = [e for e in events if isinstance(e, dict) and e.get("trip_id") == trip_id]
        return any(e.get("event_type") == "TRIP_CANCELLED" for e in ours) and any(
            e.get("event_type") == "TRIP_STATUS_CHANGED" and e["payload"].get("status") == "CANCELLED" for e in ours
        )

    arrived = receiver.wait_for(cancelled, ctx.max_wait_s)
    deliveries = [d for d in receiver.deliveries if isinstance(d.event, dict) and d.event.get("trip_id") == trip_id]
    step = ctx.result.steps[-1]
    step.check(
        arrived,
        f"TRIP_STATUS_CHANGED → CANCELLED and TRIP_CANCELLED arrive within {ctx.max_wait_s:.0f}s",
        f"got {[d.event.get('event_type') for d in deliveries]}",
    )
    bad_sig = [problem for d in deliveries if (problem := verify(secret, d.headers, d.body))]
    step.check(not bad_sig, "every delivery is signed (X-TDS-Signature, X-TDS-Timestamp)", "; ".join(bad_sig[:3]))
    bad_schema = [f"{d.event.get('event_type')}: {e}" for d in deliveries for e in event_errors(d.event)]
    step.check(not bad_schema, "every event matches its schema", "; ".join(bad_schema[:3]))
    by_id = {}
    for d in deliveries:
        by_id.setdefault(d.event["event_id"], set()).add(d.body)
    step.check(all(len(bodies) == 1 for bodies in by_id.values()), "redeliveries repeat the same event_id and body")
    statuses = {
        d.event["payload"].get("status") for d in deliveries if d.event.get("event_type") == "TRIP_STATUS_CHANGED"
    }
    expected = {"CANCELLED"} | ({"OFFERED", "CONFIRMED"} if body.get("status") == "OFFERED" else set())
    step.check(
        expected <= statuses,
        "an event for every status change, including ones returned synchronously",
        f"missing {sorted(expected - statuses)}",
    )
