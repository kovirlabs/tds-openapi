"""Paratransit and NEMT rules JSON Schema can't express (SPEC.md §11). Drafts, like the profiles."""

from datetime import date, datetime
from typing import Any

LIFT = {"WHEELCHAIR_LIFT", "RAMP"}
WHEELED = {"MANUAL_WHEELCHAIR", "POWER_WHEELCHAIR", "SCOOTER"}


def profile_problems(trip_request: dict[str, Any]) -> list[str]:
    """Messages for a schema-valid trip request whose `paratransit` / `nemt` object breaks a rule."""
    problems: list[str] = []
    leg = trip_request["legs"][0]
    requirements = set(leg.get("service_requirements", []))

    def devices_need_lift(devices: list[dict[str, Any]], where: str) -> None:
        wheeled = [d for d in devices if d["type"] in WHEELED and not d.get("rider_transfers_to_seat")]
        if wheeled and not requirements & LIFT:
            problems.append(f"{where}: a wheeled device the rider stays in needs WHEELCHAIR_LIFT or RAMP")

    if (pt := trip_request.get("paratransit")) is not None:
        programs = {e["program"] for e in trip_request["rider_ref"].get("eligibility", [])}
        if "ADA_PARATRANSIT" not in programs:
            problems.append("paratransit: rider_ref.eligibility must include program ADA_PARATRANSIT")
        devices_need_lift(pt.get("mobility_devices", []), "paratransit")
        if trip_request.get("party", {}).get("attendants", 0) and not pt.get("pca_authorized", False):
            problems.append("paratransit: party.attendants given but pca_authorized is false")

    if (nemt := trip_request.get("nemt")) is not None:
        level = nemt["level_of_service"]
        if level == "WHEELCHAIR" and not requirements & LIFT:
            problems.append("nemt: level_of_service WHEELCHAIR needs WHEELCHAIR_LIFT or RAMP")
        if level == "STRETCHER" and "STRETCHER" not in requirements:
            problems.append("nemt: level_of_service STRETCHER needs service requirement STRETCHER")
        if level != "STRETCHER" and "STRETCHER" in requirements:
            problems.append(f"nemt: service requirement STRETCHER but level_of_service {level}")
        devices_need_lift(nemt.get("mobility_devices", []), "nemt")
        auth = nemt["authorization"]
        window = leg.get("pickup_window") or leg["dropoff_window"]
        day = datetime.fromisoformat(window["earliest"]).date()
        if "valid_from" in auth and day < date.fromisoformat(auth["valid_from"]):
            problems.append(f"nemt: service date {day} is before authorization valid_from")
        if "valid_until" in auth and day > date.fromisoformat(auth["valid_until"]):
            problems.append(f"nemt: service date {day} is after authorization valid_until")
        funding = trip_request.get("fare_funding", {})
        if "authorization_ref" in funding and funding["authorization_ref"] != auth["authorization_ref"]:
            problems.append("nemt: fare_funding.authorization_ref differs from nemt.authorization.authorization_ref")
    return problems
