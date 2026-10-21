"""Settlement split rules JSON Schema can't express (SPEC.md §10.8)."""

from decimal import Decimal
from typing import Any

TERMINAL = {"COMPLETED", "FAILED", "REJECTED", "EXPIRED", "CANCELLED", "NO_SHOW"}


def settlement_problems(split: dict[str, Any]) -> list[str]:
    """Messages for a schema-valid settlement split that breaks the cross-field rules; empty when fine.

    Checks: one currency throughout, total = sum of leg costs, each leg listed once, every
    leg in a terminal status, cancellation_reason only on CANCELLED legs.
    """
    problems = []
    currency = split["total"]["currency"]
    seen = set()
    total = Decimal(0)
    for i, line in enumerate(split["legs"]):
        leg = line["leg"]
        key = (leg["provider_id"], leg["trip_id"], leg["leg_id"])
        if key in seen:
            problems.append(f"legs[{i}]: {'/'.join(key)} listed twice")
        seen.add(key)
        if line["cost"]["currency"] != currency:
            problems.append(f"legs[{i}]: cost in {line['cost']['currency']}, total in {currency}")
        total += Decimal(line["cost"]["amount"])
        if line["trip_status"] not in TERMINAL:
            problems.append(f"legs[{i}]: trip_status {line['trip_status']} is not terminal")
        if "cancellation_reason" in line and line["trip_status"] != "CANCELLED":
            problems.append(f"legs[{i}]: cancellation_reason on a {line['trip_status']} leg")
    if total != Decimal(split["total"]["amount"]):
        problems.append(f"total {split['total']['amount']} is not the sum of leg costs ({total})")
    return problems
