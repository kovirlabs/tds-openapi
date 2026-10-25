"""Core profile scenarios (SPEC.md §7, §8.1, §13)."""

from datetime import UTC, date, datetime, timedelta

from tds_conform.booking import booking_window_for, booking_window_problem, health_booking_problems, opens_at, trip_time
from tds_conform.runner import Context, Inconclusive, scenario, shift_days, shift_windows

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


ELIGIBILITY_REFUSALS = ("ELIGIBILITY_NOT_ACCEPTED", "ELIGIBILITY_REQUIRED")


@scenario(
    "eligibility_attestation",
    "core",
    "Attestations with a verification method are kept as sent, or refused with an eligibility code",
)
def eligibility_attestation(ctx: Context) -> None:
    trip = ctx.new_trip()
    rider = trip.setdefault("rider_ref", {"rider_ref_id": "conform-rider"})
    attestation = {
        "program": "AGE_60_PLUS",
        "attested_by": ctx.settings.requester_id,
        "verification_method": "DIGITAL_ID",
        "verified_at": date.today().isoformat(),
        "verifier_ref": "conform-identity-platform",
    }
    rider["eligibility"] = [*rider.get("eligibility", []), attestation]
    step = ctx.call("request trip with a DIGITAL_ID attestation", "POST", "/trips", body=trip, expect=(201, 202, 422))
    if step.status == 422:
        code = _code(step.body)
        if code not in ELIGIBILITY_REFUSALS:
            raise Inconclusive(f"provider refused the test trip ({code}); supply --trip-template")
        step.check(True, "refusal over the attestation uses an eligibility refusal code")
        return
    step.require(step.status in (201, 202), "attestation fields accepted")
    body = step.body
    if isinstance(body, dict) and body.get("status") == "REQUESTED":
        body = ctx.wait_for_decision(trip["trip_id"]).body
    if body.get("status") == "REJECTED":
        raise Inconclusive("provider rejected the test trip asynchronously; supply --trip-template")
    step = ctx.call("read trip", "GET", TRIP, expect=(200,), trip_id=trip["trip_id"])
    got = (step.body.get("rider_ref") or {}).get("eligibility") if isinstance(step.body, dict) else None
    step.check_equal(got, rider["eligibility"], "rider_ref.eligibility kept as sent")
    ctx.call("clean up", "POST", TRIP + "/cancel", body={"reason": "OTHER"}, expect=(200,), trip_id=trip["trip_id"])


OUTSIDE = "OUTSIDE_BOOKING_WINDOW"


@scenario(
    "booking_window",
    "core",
    "Requests outside the advertised booking window are refused with OUTSIDE_BOOKING_WINDOW; inside ones are not",
)
def booking_window(ctx: Context) -> None:
    step = ctx.call("health", "GET", "/health", expect=(200,), auth=False)
    ctx.health = step.body if isinstance(step.body, dict) else {}
    problems = health_booking_problems(ctx.health)
    step.check(
        not problems,
        "booking windows are consistent (min_notice_s <= max_advance_s, IANA time_zone)",
        "; ".join(problems),
    )
    window = booking_window_for(ctx.health, ctx.new_trip())
    if not window:
        ctx.result.note = "no booking_window advertised; nothing to check"
        return

    inside = ctx.new_trip()
    if booking_window_problem(window, trip_time(inside), datetime.now(UTC)) is None:
        step = ctx.call("request a trip inside the window", "POST", "/trips", body=inside, expect=(201, 202, 422))
        step.check(
            _code(step.body) != OUTSIDE,
            "a trip inside the advertised window is not refused for timing",
            f"refused with {OUTSIDE}",
        )
        if step.status in (201, 202):
            ctx.call(
                "clean up", "POST", TRIP + "/cancel", body={"reason": "OTHER"}, expect=(200,), trip_id=inside["trip_id"]
            )

    if window.get("max_advance_s") is not None:
        early = ctx.new_trip()
        now = datetime.now(UTC)
        start = opens_at(window, trip_time(early))
        if start <= now + timedelta(hours=1):
            shift_days(early["legs"], (now + timedelta(days=1) - start).days + 1)
        step = ctx.call("request a trip beyond max_advance_s", "POST", "/trips", body=early, expect=(422,))
        step.check_equal(_code(step.body), OUTSIDE, "refused with OUTSIDE_BOOKING_WINDOW")
        bookable = step.body.get("bookable_from") if isinstance(step.body, dict) else None
        if step.check(bookable is not None, "bookable_from says when to retry", "missing bookable_from"):
            moment = datetime.fromisoformat(bookable)
            step.check(
                now < moment <= trip_time(early),
                "bookable_from is after now and not after the trip time",
                f"bookable_from {bookable}",
            )

    late = _too_late_trip(ctx, window)
    if late is None:
        if window.get("min_notice_s") or window.get("same_day") is False:
            ctx.result.note = "could not build a future trip with too little notice; notice not checked"
        return
    step = ctx.call("request a trip with too little notice", "POST", "/trips", body=late, expect=(422,))
    step.check_equal(_code(step.body), OUTSIDE, "refused with OUTSIDE_BOOKING_WINDOW")


def _too_late_trip(ctx: Context, window: dict) -> dict | None:
    """A trip still in the future that breaks min_notice_s or same_day; None if the window has neither."""
    if not window.get("min_notice_s") and window.get("same_day") is not False:
        return None
    trip, now = ctx.new_trip(), datetime.now(UTC)
    margin = now + timedelta(minutes=10)
    for _ in range(400):  # whole days first: keeps the template's time of day (service hours)
        if booking_window_problem(window, trip_time(trip), now) and trip_time(trip) > margin:
            return trip
        if trip_time(trip) - timedelta(days=1) <= margin:
            break
        shift_days(trip["legs"], -1)
    notice = window.get("min_notice_s") or 0
    target = now + timedelta(seconds=max(notice // 2, 1200))
    shift_windows(trip["legs"], target - trip_time(trip))
    return trip if booking_window_problem(window, trip_time(trip), now) else None


def _has_ce_headers(headers) -> bool:
    return any(k.lower().startswith("ce-") for k in headers)


def _code(body) -> str | None:
    return body.get("code") if isinstance(body, dict) else None


@scenario("webhook_delivery", "core", "Signed, schema-valid events are delivered for every change, including sync ones")
def webhook_delivery(ctx: Context) -> None:
    from tds_conform.cloudevents import cloudevents_problems
    from tds_conform.events import (
        BROKER_EVENTS,
        WEBHOOK_ID_HEADER,
        WEBHOOK_SIGNATURE_HEADER,
        event_errors,
        get_header,
        verify,
        verify_standard,
    )

    receiver = ctx.settings.receiver
    if receiver is None:
        raise Inconclusive("no webhook receiver; run with --listen (and --callback-url if the provider is remote)")
    types = ["TRIP_STATUS_CHANGED", "TRIP_SCHEDULED", "TRIP_CANCELLED"]
    subscription = {"callback_url": receiver.url, "event_types": types, "cloudevents": True}
    step = ctx.call("subscribe", "POST", "/subscriptions", body=subscription, expect=(201,))
    step.require(step.status == 201 and "signing_secret" in step.body, "subscription returns a signing_secret")
    secret = step.body["signing_secret"]
    wants_cloudevents = step.body.get("cloudevents") is True

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
    bad_std = []
    for d in deliveries:
        if get_header(d.headers, WEBHOOK_SIGNATURE_HEADER) is None:
            continue
        if problem := verify_standard(secret, d.headers, d.body):
            bad_std.append(f"{d.event.get('event_id')}: {problem}")
        if get_header(d.headers, WEBHOOK_ID_HEADER) != d.event.get("event_id"):
            bad_std.append(f"{d.event.get('event_id')}: webhook-id is not the event_id")
    step.check(
        not bad_std,
        "Standard Webhooks headers, when sent, verify (webhook-signature v1, webhook-id = event_id)",
        "; ".join(bad_std[:3]),
    )
    every = [d for d in receiver.deliveries if isinstance(d.event, dict)]
    broker_only = sorted({d.event["event_type"] for d in every if d.event.get("event_type") in BROKER_EVENTS})
    step.check(
        not broker_only,
        "provider sends no broker events (EXCEPTION_RAISED, EXCEPTION_RESOLVED)",
        f"got {broker_only}",
    )
    if wants_cloudevents:
        missing = [d.event.get("event_id") for d in deliveries if not _has_ce_headers(d.headers)]
        step.check(
            not missing,
            "subscription says cloudevents: true, so every delivery carries ce-* headers",
            f"none on {missing[:3]}",
        )
    bad_ce = [f"{d.event.get('event_id')}: {p}" for d in deliveries for p in cloudevents_problems(d.headers, d.event)]
    step.check(not bad_ce, "ce-* headers, when sent, match the envelope (SPEC.md §9.1)", "; ".join(bad_ce[:3]))
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


SECRETS = "/subscriptions/{subscription_id}/secrets"
CLOCK_SKEW_S = 120


def _trip_deliveries(ctx: Context, receiver, trip_id: str, tag: str) -> list:
    """Book and cancel a test trip; return its deliveries to the callback URL carrying `tag`."""
    trip = ctx.new_trip(trip_id=trip_id)
    _request_and_decide(ctx, trip)
    ctx.call("cancel", "POST", TRIP + "/cancel", body={"reason": "RIDER_REQUEST"}, expect=(200,), trip_id=trip_id)

    def ours() -> list:
        return [
            d
            for d in receiver.deliveries
            if tag in d.path and isinstance(d.event, dict) and d.event.get("trip_id") == trip_id
        ]

    arrived = receiver.wait_for(
        lambda _: any(d.event.get("event_type") == "TRIP_CANCELLED" for d in ours()), ctx.max_wait_s
    )
    ctx.result.steps[-1].check(arrived, f"TRIP_CANCELLED arrives within {ctx.max_wait_s:.0f}s", "no TRIP_CANCELLED")
    return ours()


@scenario(
    "secret_rotation",
    "core",
    "Subscription secrets rotate over the API with an overlap and retire early (when /health declares it)",
)
def secret_rotation(ctx: Context) -> None:
    import uuid

    from tds_conform.events import WEBHOOK_SIGNATURE_HEADER, get_header, standard_key, verify, verify_standard

    if not ctx.health:
        step = ctx.call("health", "GET", "/health", expect=(200,), auth=False)
        ctx.health = step.body if isinstance(step.body, dict) else {}
    if ctx.health.get("secret_rotation") is not True:
        ctx.result.note = "secret_rotation not declared on /health; nothing to check"
        return
    receiver = ctx.settings.receiver
    # A tag in the callback URL tells this subscription's deliveries from other scenarios'.
    tag = f"rotation-{uuid.uuid4().hex[:12]}"
    callback = f"{receiver.url}?subscription={tag}" if receiver else "http://127.0.0.1:9/tds-conform-unused"
    sub = {"callback_url": callback, "event_types": ["TRIP_STATUS_CHANGED", "TRIP_CANCELLED"]}
    step = ctx.call("subscribe", "POST", "/subscriptions", body=sub, expect=(201,))
    step.require(step.status == 201 and "signing_secret" in step.body, "subscription returns a signing_secret")
    sub_id, old = step.body["subscription_id"], step.body["signing_secret"]
    old_id = step.body.get("signing_secret_id")
    step.check(old_id is not None, "subscription returns signing_secret_id", "missing signing_secret_id")

    overlap_s, key = 3600, f"conform-{uuid.uuid4()}"
    rotate = {"overlap_s": overlap_s}
    started = datetime.now(UTC)
    step = ctx.call("rotate the secret", "POST", SECRETS, body=rotate, key=key, expect=(201,), subscription_id=sub_id)
    step.require(step.status == 201 and isinstance(step.body, dict), "rotation succeeds")
    secrets = step.body.get("secrets") or [{}]
    first, rest = secrets[0], secrets[1:]
    step.require("signing_secret" in first, "the first entry is the new secret, with its value")
    new, new_id = first["signing_secret"], first.get("secret_id")
    step.check(new != old, "the new secret differs from the old one", "same value returned")
    step.check(
        "expires_at" not in first, "the newest secret has no expires_at", f"expires_at {first.get('expires_at')}"
    )
    step.check(
        all("signing_secret" not in s for s in rest), "only the new secret carries its value", "older value returned"
    )
    step.check_equal(step.body.get("subscription_id"), sub_id, "answer names the subscription")
    older = next((s for s in rest if s.get("secret_id") == old_id), rest[0] if rest else None)
    if step.check(older is not None, "the old secret is still listed during the overlap", "old secret missing"):
        old_id = older.get("secret_id", old_id)
        expires = older.get("expires_at")
        latest = started + timedelta(seconds=overlap_s + CLOCK_SKEW_S)
        step.check(
            expires is not None
            and started - timedelta(seconds=CLOCK_SKEW_S) < datetime.fromisoformat(expires) <= latest,
            f"the old secret expires after now and within overlap_s ({overlap_s} s)",
            f"expires_at {expires}",
        )

    step = ctx.call("replay the rotation", "POST", SECRETS, body=rotate, key=key, expect=(201,), subscription_id=sub_id)
    replayed = (step.body.get("secrets") or [{}])[0] if isinstance(step.body, dict) else {}
    step.check_equal(
        replayed.get("signing_secret"), new, "a replay with the same Idempotency-Key returns the same secret"
    )

    step = ctx.call("list secrets", "GET", SECRETS, expect=(200,), subscription_id=sub_id)
    listed = step.body.get("secrets", []) if isinstance(step.body, dict) else []
    step.check(all("signing_secret" not in s for s in listed), "a listing carries no secret values", "value listed")
    step.check_equal([s.get("secret_id") for s in listed[:1]], [new_id], "the listing starts with the newest secret")
    step.check(any(s.get("secret_id") == old_id for s in listed), "the old secret is listed", f"got {listed}")

    ctx.call(
        "rotate an unknown subscription",
        "POST",
        SECRETS,
        body={},
        expect=(404,),
        subscription_id=f"conform-missing-{uuid.uuid4().hex[:8]}",
    )

    both_whsec = bool(standard_key(old) and standard_key(new))
    if receiver:
        deliveries = _trip_deliveries(ctx, receiver, f"conform-{uuid.uuid4().hex[:12]}", tag)
        step = ctx.result.steps[-1]
        bad = [p for d in deliveries if (p := verify(new, d.headers, d.body))]
        step.check(not bad, "during the overlap, X-TDS-Signature uses the new secret", "; ".join(bad[:3]))
        standard = [d for d in deliveries if get_header(d.headers, WEBHOOK_SIGNATURE_HEADER) is not None]
        if both_whsec:
            missing = [
                d.event.get("event_id")
                for d in standard
                if verify_standard(new, d.headers, d.body) or verify_standard(old, d.headers, d.body)
            ]
            step.check(
                not missing,
                "during the overlap, webhook-signature has a v1 entry for the new and the old secret",
                f"not on {missing[:3]}",
            )

    step = ctx.call(
        "retire the newest secret",
        "DELETE",
        SECRETS + "/{secret_id}",
        expect=(409,),
        subscription_id=sub_id,
        secret_id=new_id or "",
    )
    step.check_equal(_code(step.body), "INVALID_STATE", "refused with INVALID_STATE")
    if old_id:
        step = ctx.call(
            "retire the old secret early",
            "DELETE",
            SECRETS + "/{secret_id}",
            expect=(200,),
            subscription_id=sub_id,
            secret_id=old_id,
        )
        expires = step.body.get("expires_at") if isinstance(step.body, dict) else None
        step.check(
            expires is not None
            and datetime.fromisoformat(expires) <= datetime.now(UTC) + timedelta(seconds=CLOCK_SKEW_S),
            "the retired secret's expires_at is now or earlier",
            f"expires_at {expires}",
        )
        step.check(
            isinstance(step.body, dict) and "signing_secret" not in step.body, "retiring returns no secret value"
        )
        step = ctx.call("list secrets after retiring", "GET", SECRETS, expect=(200,), subscription_id=sub_id)
        listed = step.body.get("secrets", []) if isinstance(step.body, dict) else []
        step.check(
            [s.get("secret_id") for s in listed] == [new_id],
            "only the new secret is left",
            f"got {[s.get('secret_id') for s in listed]}",
        )
    ctx.call(
        "retire an unknown secret",
        "DELETE",
        SECRETS + "/{secret_id}",
        expect=(404,),
        subscription_id=sub_id,
        secret_id=f"conform-missing-{uuid.uuid4().hex[:8]}",
    )

    if receiver and old_id:
        deliveries = _trip_deliveries(ctx, receiver, f"conform-{uuid.uuid4().hex[:12]}", tag)
        step = ctx.result.steps[-1]
        bad = [p for d in deliveries if (p := verify(new, d.headers, d.body))]
        step.check(not bad, "after retiring, deliveries are signed with the new secret", "; ".join(bad[:3]))
        if both_whsec:
            stale = [
                d.event.get("event_id")
                for d in deliveries
                if get_header(d.headers, WEBHOOK_SIGNATURE_HEADER) is not None
                and verify_standard(old, d.headers, d.body) is None
            ]
            step.check(not stale, "after retiring, no signature is made with the old secret", f"on {stale[:3]}")


SUBSCRIPTION = "/subscriptions/{subscription_id}"


@scenario(
    "subscription_management",
    "core",
    "Subscriptions are read, changed and ended over the API, keeping their secret (when /health declares it)",
)
def subscription_management(ctx: Context) -> None:
    import uuid

    from tds_conform.events import verify

    if not ctx.health:
        step = ctx.call("health", "GET", "/health", expect=(200,), auth=False)
        ctx.health = step.body if isinstance(step.body, dict) else {}
    if ctx.health.get("subscription_management") is not True:
        ctx.result.note = "subscription_management not declared on /health; nothing to check"
        return
    receiver = ctx.settings.receiver
    tag = f"manage-{uuid.uuid4().hex[:12]}"
    callback = f"{receiver.url}?subscription={tag}" if receiver else "http://127.0.0.1:9/tds-conform-unused"
    step = ctx.call(
        "subscribe",
        "POST",
        "/subscriptions",
        body={"callback_url": callback, "event_types": ["TRIP_STATUS_CHANGED"]},
        expect=(201,),
    )
    step.require(step.status == 201 and "signing_secret" in step.body, "subscription returns a signing_secret")
    created = step.body
    sub_id, secret = created["subscription_id"], created["signing_secret"]

    def no_secret(step, what: str) -> None:
        step.check(
            isinstance(step.body, dict) and "signing_secret" not in step.body,
            f"{what} carries no signing_secret",
            "signing_secret returned",
        )

    step = ctx.call("read the subscription", "GET", SUBSCRIPTION, expect=(200,), subscription_id=sub_id)
    no_secret(step, "reading")
    if isinstance(step.body, dict):
        for name in ("subscription_id", "callback_url", "event_types"):
            step.check_equal(step.body.get(name), created.get(name), f"{name} as created")

    step = ctx.call("list subscriptions", "GET", "/subscriptions", expect=(200,))
    listed = step.body.get("subscriptions", []) if isinstance(step.body, dict) else []
    step.check(any(s.get("subscription_id") == sub_id for s in listed), "the listing has the subscription", "missing")
    step.check(all("signing_secret" not in s for s in listed), "a listing carries no signing_secret", "secret listed")

    change, key = {"event_types": ["TRIP_CANCELLED"]}, f"conform-{uuid.uuid4()}"
    step = ctx.call(
        "change the event types", "PATCH", SUBSCRIPTION, body=change, key=key, expect=(200,), subscription_id=sub_id
    )
    step.require(step.status == 200 and isinstance(step.body, dict), "the change is applied")
    changed = step.body
    no_secret(step, "the change's answer")
    step.check_equal(changed.get("subscription_id"), sub_id, "subscription_id unchanged")
    step.check_equal(changed.get("event_types"), ["TRIP_CANCELLED"], "event_types replaced as a whole")
    step.check_equal(changed.get("callback_url"), created.get("callback_url"), "callback_url left as it was")
    step.check("updated_at" in changed, "the answer carries updated_at", "no updated_at")
    if created.get("signing_secret_id"):
        step.check_equal(changed.get("signing_secret_id"), created["signing_secret_id"], "signing_secret_id unchanged")
    step = ctx.call(
        "replay the change", "PATCH", SUBSCRIPTION, body=change, key=key, expect=(200,), subscription_id=sub_id
    )
    step.check_equal(step.body, changed, "a replay with the same Idempotency-Key returns the same answer")
    step = ctx.call(
        "reuse the key with another change",
        "PATCH",
        SUBSCRIPTION,
        body={"cloudevents": False},
        key=key,
        expect=(409,),
        subscription_id=sub_id,
    )
    step.check_equal(_code(step.body), "IDEMPOTENCY_KEY_REUSED", "refused with IDEMPOTENCY_KEY_REUSED")
    step = ctx.call("change nothing", "PATCH", SUBSCRIPTION, body={}, expect=(400,), subscription_id=sub_id)
    step.check_equal(_code(step.body), "INVALID_REQUEST", "an empty change is INVALID_REQUEST")
    step = ctx.call("read after the change", "GET", SUBSCRIPTION, expect=(200,), subscription_id=sub_id)
    step.check_equal(
        step.body.get("event_types") if isinstance(step.body, dict) else None, ["TRIP_CANCELLED"], "new event_types"
    )

    if receiver:
        deliveries = _trip_deliveries(ctx, receiver, f"conform-{uuid.uuid4().hex[:12]}", tag)
        step = ctx.result.steps[-1]
        dropped = [d.event.get("event_type") for d in deliveries if d.event.get("event_type") != "TRIP_CANCELLED"]
        step.check(not dropped, "after the change, only the new event types are delivered", f"got {dropped[:3]}")
        bad = [p for d in deliveries if (p := verify(secret, d.headers, d.body))]
        step.check(not bad, "after the change, deliveries verify with the secret from subscribing", "; ".join(bad[:3]))

    key = f"conform-{uuid.uuid4()}"
    step = ctx.call("end the subscription", "DELETE", SUBSCRIPTION, key=key, expect=(200,), subscription_id=sub_id)
    no_secret(step, "ending")
    ended = step.body
    step = ctx.call("replay the end", "DELETE", SUBSCRIPTION, key=key, expect=(200,), subscription_id=sub_id)
    step.check_equal(step.body, ended, "a replay with the same Idempotency-Key returns the same answer")
    for label, method, body in [
        ("read the ended subscription", "GET", None),
        ("change the ended subscription", "PATCH", change),
        ("end it again", "DELETE", None),
    ]:
        step = ctx.call(label, method, SUBSCRIPTION, body=body, expect=(404,), subscription_id=sub_id)
        step.check_equal(_code(step.body), "NOT_FOUND", "code is NOT_FOUND")
    step = ctx.call("list after ending", "GET", "/subscriptions", expect=(200,))
    listed = step.body.get("subscriptions", []) if isinstance(step.body, dict) else []
    step.check(all(s.get("subscription_id") != sub_id for s in listed), "the ended one is not listed", "still listed")
    ctx.call(
        "read an unknown subscription",
        "GET",
        SUBSCRIPTION,
        expect=(404,),
        subscription_id=f"conform-missing-{uuid.uuid4().hex[:8]}",
    )

    if receiver:
        # A second subscription shows when the next trip's events have gone out.
        witness = f"manage-witness-{uuid.uuid4().hex[:12]}"
        body = {"callback_url": f"{receiver.url}?subscription={witness}", "event_types": ["TRIP_CANCELLED"]}
        step = ctx.call("subscribe a witness", "POST", "/subscriptions", body=body, expect=(201,))
        witness_id = step.body.get("subscription_id") if isinstance(step.body, dict) else None
        trip_id = f"conform-{uuid.uuid4().hex[:12]}"
        _trip_deliveries(ctx, receiver, trip_id, witness)
        late = [
            d.event.get("event_type")
            for d in receiver.deliveries
            if tag in d.path and isinstance(d.event, dict) and d.event.get("trip_id") == trip_id
        ]
        ctx.result.steps[-1].check(not late, "nothing is delivered to an ended subscription", f"got {late[:3]}")
        if witness_id:
            ctx.call("end the witness", "DELETE", SUBSCRIPTION, expect=(200,), subscription_id=witness_id)
