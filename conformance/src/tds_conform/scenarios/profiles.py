"""Paratransit and NEMT profile scenarios (SPEC.md §11, §13).

Each profile has a booking scenario (the provider declares the profile, accepts a valid
request and keeps the profile object exactly as sent) and a rules scenario (it refuses
requests that break the profile's rules with 400 INVALID_REQUEST). The test trips come from
the published examples (`paratransit-trip`, `nemt-trip`), moved to future dates; a provider
that refuses them for capacity or service area makes the booking scenario INCONCLUSIVE.
"""

import copy
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from tds_conform.data import example
from tds_conform.runner import Context, _shift_to_future, scenario
from tds_conform.scenarios.core import TRIP, _code, _request_and_decide

EXAMPLES = {"paratransit": "paratransit-trip", "nemt": "nemt-trip"}


def profile_trip(ctx: Context, profile: str) -> dict:
    """The profile's published example trip, with a fresh id, this run's parties and future dates."""
    trip = copy.deepcopy(example(EXAMPLES[profile], "01-request-trip.request.json"))
    trip["trip_id"] = f"conform-{profile[:2]}-{uuid.uuid4().hex[:10]}"
    trip["requester_id"] = ctx.settings.requester_id
    trip["provider_id"] = ctx.settings.provider_id
    _shift_to_future(trip["legs"])
    leg = trip["legs"][0]
    day = datetime.fromisoformat((leg.get("pickup_window") or leg["dropoff_window"])["earliest"]).date()
    for attestation in trip["rider_ref"].get("eligibility", []):
        attestation["valid_until"] = (day + timedelta(days=365)).isoformat()
    if profile == "nemt":
        auth = trip["nemt"]["authorization"]
        auth["valid_from"], auth["valid_until"] = (
            (day - timedelta(days=1)).isoformat(),
            (day + timedelta(days=30)).isoformat(),
        )
    return trip


def _declares(ctx: Context, profile: str) -> None:
    step = ctx.call("health", "GET", "/health", expect=(200,), auth=False)
    declared = step.body.get("profiles", []) if isinstance(step.body, dict) else []
    step.require(profile.upper() in declared, f"/health lists {profile.upper()}", f"profiles: {declared}")


def _booking(ctx: Context, profile: str) -> None:
    _declares(ctx, profile)
    trip = profile_trip(ctx, profile)
    body = _request_and_decide(ctx, trip)
    step = ctx.result.steps[-1]
    step.check_equal(body.get(profile), trip[profile], f"decision keeps the {profile} object as sent")
    step = ctx.call("read trip", "GET", TRIP, expect=(200,), trip_id=trip["trip_id"])
    step.check_equal(step.body.get(profile), trip[profile], f"stored trip keeps the {profile} object as sent")
    sent = trip["legs"][0].get("service_requirements")
    got = (step.body.get("legs") or [{}])[0].get("service_requirements")
    step.check_equal(sorted(got or []), sorted(sent or []), "service_requirements preserved")
    ctx.call("clean up", "POST", TRIP + "/cancel", body={"reason": "OTHER"}, expect=(200,), trip_id=trip["trip_id"])


def _rules(ctx: Context, profile: str, cases: list[tuple[str, Callable[[dict], None]]]) -> None:
    _declares(ctx, profile)
    for label, change in cases:
        trip = profile_trip(ctx, profile)
        change(trip)
        step = ctx.call(f"refuse: {label}", "POST", "/trips", body=trip, expect=(400,))
        step.check_equal(_code(step.body), "INVALID_REQUEST", "code is INVALID_REQUEST")
        if step.status in (200, 201, 202):  # accepted by mistake: don't leave it booked
            ctx.call(
                "clean up", "POST", TRIP + "/cancel", body={"reason": "OTHER"}, expect=(200,), trip_id=trip["trip_id"]
            )


def _set(path: str, value) -> Callable[[dict], None]:
    def change(trip: dict) -> None:
        *parents, last = path.split(".")
        node = trip
        for key in parents:
            node = node[int(key)] if key.isdigit() else node[key]
        node[last] = value

    return change


@scenario(
    "paratransit_booking", "paratransit", "A paratransit trip is accepted and its paratransit object kept as sent"
)
def paratransit_booking(ctx: Context) -> None:
    _booking(ctx, "paratransit")


@scenario("paratransit_rules", "paratransit", "Paratransit requests that break the profile rules are refused")
def paratransit_rules(ctx: Context) -> None:
    _rules(
        ctx,
        "paratransit",
        [
            ("no ADA_PARATRANSIT attestation", _set("rider_ref.eligibility", [])),
            ("wheelchair without lift or ramp", _set("legs.0.service_requirements", ["DOOR_TO_DOOR"])),
            ("attendant without pca_authorized", _set("paratransit.pca_authorized", False)),
            ("unknown eligibility_category", _set("paratransit.eligibility_category", "PERMANENT")),
        ],
    )


@scenario("nemt_booking", "nemt", "A NEMT trip is accepted and its nemt object kept exactly as sent (nothing added)")
def nemt_booking(ctx: Context) -> None:
    _booking(ctx, "nemt")


@scenario("nemt_rules", "nemt", "NEMT requests that break the rules or carry extra or free-text data are refused")
def nemt_rules(ctx: Context) -> None:
    _rules(
        ctx,
        "nemt",
        [
            ("WHEELCHAIR level without lift or ramp", _set("legs.0.service_requirements", [])),
            ("STRETCHER level without STRETCHER requirement", _set("nemt.level_of_service", "STRETCHER")),
            ("service date outside the authorization", _set("nemt.authorization.valid_until", "2000-01-01")),
            ("extra field in the nemt object", _set("nemt.trip_purpose", "CHECKUP")),
            ("free text in authorization_ref", _set("nemt.authorization.authorization_ref", "see notes, Tue/Thu")),
            ("authorization_ref differs from fare_funding", _set("fare_funding.authorization_ref", "PA-OTHER")),
        ],
    )
