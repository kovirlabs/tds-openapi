"""Eligibility attestation rules JSON Schema can't express (SPEC.md §6.5.1)."""

import re
from datetime import date
from typing import Any

AGE_CODE = re.compile(r"^AGE_(?:[1-9][0-9]*_PLUS|UNDER_[1-9][0-9]*)$")


def eligibility_problems(attestations: list[dict[str, Any]], where: str = "rider_ref.eligibility") -> list[str]:
    """Messages for schema-valid attestations that break a MUST: `verified_at` after `valid_until`."""
    problems = []
    for i, attestation in enumerate(attestations):
        verified, until = attestation.get("verified_at"), attestation.get("valid_until")
        if verified and until and date.fromisoformat(verified) > date.fromisoformat(until):
            problems.append(f"{where}/{i}: verified_at {verified} is after valid_until {until}")
    return problems


def age_code_problems(attestations: list[dict[str, Any]], where: str = "rider_ref.eligibility") -> list[str]:
    """Messages for AGE_ codes that don't follow the SHOULD convention AGE_<N>_PLUS / AGE_UNDER_<N>."""
    return [
        f"{where}/{i}: program {a['program']!r} does not follow AGE_<N>_PLUS or AGE_UNDER_<N>"
        for i, a in enumerate(attestations)
        if a["program"].startswith("AGE_") and not AGE_CODE.match(a["program"])
    ]


def trip_eligibility_problems(trip_request: dict[str, Any]) -> list[str]:
    """The MUST rules for the attestations on a trip request's rider."""
    return eligibility_problems(trip_request.get("rider_ref", {}).get("eligibility", []))
