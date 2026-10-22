"""Booking windows (SPEC.md §7.1): when a provider accepts a request for a trip.

Usable by requesters (plan when to submit each leg) and providers (refuse requests
outside the window). All functions are pure; pass `now` explicitly.
"""

from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "booking_window_for",
    "booking_window_problem",
    "closes_at",
    "health_booking_problems",
    "opens_at",
    "trip_time",
]


def booking_window_for(health: dict[str, Any], trip_request: dict[str, Any], tender: bool = False) -> dict | None:
    """The booking window that applies to a request, from a provider's /health body (None: no limit stated)."""
    by_profile = health.get("booking_window_by_profile") or {}
    for profile in ("paratransit", "nemt"):
        if profile in trip_request and profile in by_profile:
            return by_profile[profile]
    if tender and "transfer" in by_profile:
        return by_profile["transfer"]
    return health.get("booking_window")


def trip_time(trip_request: dict[str, Any]) -> datetime:
    """`earliest` of the first leg's pickup_window, or of its dropoff_window when it has none."""
    leg = trip_request["legs"][0]
    window = leg.get("pickup_window") or leg["dropoff_window"]
    return datetime.fromisoformat(window["earliest"])


def opens_at(window: dict[str, Any], at: datetime) -> datetime | None:
    """First moment a request for a trip at `at` is inside the window (None: no max_advance_s)."""
    if window.get("max_advance_s") is None:
        return None
    return at - timedelta(seconds=window["max_advance_s"])


def closes_at(window: dict[str, Any], at: datetime) -> datetime | None:
    """Last moment (inclusive) a request for a trip at `at` is inside the window (None: no limit)."""
    limits = []
    if window.get("min_notice_s") is not None:
        limits.append(at - timedelta(seconds=window["min_notice_s"]))
    if window.get("same_day") is False:
        limits.append(_day_start(window, at) - timedelta(seconds=1))
    return min(limits) if limits else None


def booking_window_problem(window: dict[str, Any] | None, at: datetime, now: datetime) -> dict | None:
    """None when a request at `now` for a trip at `at` is inside `window`.

    Otherwise a dict for the refusal: {"detail": str} plus "bookable_from" (RFC 3339) when
    the request came too early.
    """
    if not window:
        return None
    start = opens_at(window, at)
    if start is not None and now < start:
        return {
            "detail": f"Trips are booked at most {_human(window['max_advance_s'])} ahead.",
            "bookable_from": _local(window, start).isoformat(timespec="seconds"),
        }
    if window.get("min_notice_s") is not None and now > at - timedelta(seconds=window["min_notice_s"]):
        return {"detail": f"Trips need at least {_human(window['min_notice_s'])} of notice."}
    if window.get("same_day") is False and now >= _day_start(window, at):
        return {"detail": "Trips must be booked by the end of the previous service day."}
    return None


def health_booking_problems(health: dict[str, Any]) -> list[str]:
    """Rules JSON Schema can't express: min_notice_s <= max_advance_s, and time_zone is a known IANA zone."""
    windows = [("booking_window", health.get("booking_window"))]
    windows += [
        (f"booking_window_by_profile.{name}", w) for name, w in (health.get("booking_window_by_profile") or {}).items()
    ]
    problems = []
    for where, window in windows:
        if not window:
            continue
        notice, advance = window.get("min_notice_s"), window.get("max_advance_s")
        if notice is not None and advance is not None and notice > advance:
            problems.append(f"{where}: min_notice_s {notice} is more than max_advance_s {advance}")
        if zone := window.get("time_zone"):
            try:
                ZoneInfo(zone)
            except (ZoneInfoNotFoundError, ValueError):
                problems.append(f"{where}: time_zone {zone!r} is not an IANA time zone")
    return problems


def _day_start(window: dict[str, Any], at: datetime) -> datetime:
    """Midnight starting the trip's service day, in the window's time zone (or the trip's offset)."""
    local = _local(window, at)
    return datetime.combine(local.date(), time(0), tzinfo=local.tzinfo)


def _local(window: dict[str, Any], moment: datetime) -> datetime:
    zone = window.get("time_zone")
    return moment.astimezone(ZoneInfo(zone)) if zone else moment


def _human(seconds: int) -> str:
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size and seconds % size == 0:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return f"{seconds} seconds"
