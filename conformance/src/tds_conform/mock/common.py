"""Helpers shared by the core provider and the transfer mixin."""

from datetime import datetime

TERMINAL = {"COMPLETED", "FAILED", "REJECTED", "EXPIRED", "CANCELLED", "NO_SHOW"}

Response = tuple[int, dict]


def problem(status: int, code: str, detail: str) -> Response:
    titles = {400: "Invalid request", 401: "Unauthorized", 404: "Not found", 409: "Conflict", 422: "Refused"}
    title = titles.get(status, "Error")
    return status, {"type": "about:blank", "title": title, "status": status, "detail": detail, "code": code}


def _not_found(kind: str, ident: str) -> Response:
    return problem(404, "NOT_FOUND", f"No {kind} {ident!r} for this requester.")


def _invalid_state(trip_id: str, status: str, verb: str) -> Response:
    return problem(409, "INVALID_STATE", f"Trip {trip_id!r} is {status} and cannot be {verb}.")


def _window_problem(leg: dict, now: datetime) -> Response | None:
    """SPEC.md §6.4 ordering (earliest <= preferred <= latest), which JSON Schema cannot express."""
    for name in ("pickup_window", "dropoff_window"):
        if window := leg.get(name):
            earliest, latest = datetime.fromisoformat(window["earliest"]), datetime.fromisoformat(window["latest"])
            preferred = datetime.fromisoformat(window.get("preferred", window["earliest"]))
            if not earliest <= preferred <= latest:
                return problem(422, "INVALID_WINDOW", f"{name}: need earliest <= preferred <= latest.")
            if latest < now:
                return problem(422, "INVALID_WINDOW", f"{name} is in the past.")
    return None


def _ts(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")
