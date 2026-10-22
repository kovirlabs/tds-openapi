"""Eligibility attestation rules JSON Schema can't express (SPEC.md §6.5.1, §6.5.2)."""

import re
from datetime import date
from typing import Any

AGE_CODE = re.compile(r"^AGE_(?:[1-9][0-9]*_PLUS|UNDER_[1-9][0-9]*)$")
# RESIDENT_<ISO 3166-1 alpha-2 country>_<official area code>; in the US the area code is the
# 5-digit county FIPS code (state + county), e.g. RESIDENT_US_26161.
RESIDENT_CODE = re.compile(r"^RESIDENT_[A-Z]{2}_[A-Z0-9]+$")
RESIDENT_US_CODE = re.compile(r"^RESIDENT_US_[0-9]{5}$")
# Facts that are yes/no only: the code itself, never a qualifier (no condition, type or threshold).
BARE_FACTS = ("DISABILITY", "LOW_INCOME")


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


def _fact_code_problem(program: str) -> str | None:
    for fact in BARE_FACTS:
        if program.startswith(fact + "_"):
            return f"{fact} is a yes/no fact and takes no qualifier (never a condition, type or threshold)"
    if program.startswith("RESIDENT_"):
        if program.startswith("RESIDENT_US_"):
            if not RESIDENT_US_CODE.match(program):
                return "US residency SHOULD be RESIDENT_US_<5-digit county FIPS code>"
        elif not RESIDENT_CODE.match(program):
            return "residency SHOULD be RESIDENT_<ISO 3166-1 alpha-2 country>_<official area code>"
    return None


def fact_code_problems(attestations: list[dict[str, Any]], where: str = "rider_ref.eligibility") -> list[str]:
    """Messages for fact codes that don't follow the SHOULD conventions of SPEC.md §6.5.2.

    Covers AGE_<N>_PLUS / AGE_UNDER_<N>, bare DISABILITY and LOW_INCOME, and
    RESIDENT_<country>_<area> (RESIDENT_US_<county FIPS> in the US).
    """
    problems = age_code_problems(attestations, where)
    for i, attestation in enumerate(attestations):
        if message := _fact_code_problem(attestation["program"]):
            problems.append(f"{where}/{i}: program {attestation['program']!r}: {message}")
    return problems


def trip_eligibility_problems(trip_request: dict[str, Any]) -> list[str]:
    """The MUST rules for the attestations on a trip request's rider."""
    return eligibility_problems(trip_request.get("rider_ref", {}).get("eligibility", []))
