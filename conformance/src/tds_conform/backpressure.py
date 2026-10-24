"""Back-pressure hints (SPEC.md §7.2): `Retry-After` and `retry_after_s` on 429 and 503.

A provider that is rate limiting a client (429, code `RATE_LIMITED`) or cannot take requests
for now (503, code `TEMPORARILY_UNAVAILABLE`) SHOULD say how long to wait, both as the HTTP
`Retry-After` header (delta-seconds or an HTTP-date, RFC 9110 §10.2.3) and as `retry_after_s`
in the Problem Details body. The client waits at least that long, then repeats the same request
with the same `Idempotency-Key`. The same header on a 429/503 answer to a webhook delivery asks
the sender to wait before its next attempt.

Requesters use `retry_after_s(headers, body)`; checks use `backpressure_problems`.
"""

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

from tds_conform.events import get_header

__all__ = [
    "BACKPRESSURE_CODES",
    "backpressure_problems",
    "parse_retry_after",
    "retry_after_header",
    "retry_after_s",
]

# The status codes that carry back-pressure, and the error code each SHOULD use.
BACKPRESSURE_CODES = {429: "RATE_LIMITED", 503: "TEMPORARILY_UNAVAILABLE"}


def parse_retry_after(value: str | None, now: datetime | None = None) -> int | None:
    """Seconds to wait from a `Retry-After` value: delta-seconds or an HTTP-date.

    An HTTP-date in the past gives 0. Anything unparseable gives None (RFC 9110: ignore it).
    """
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if moment.tzinfo is None:  # "-0000" dates parse naive; HTTP-dates are always GMT
        moment = moment.replace(tzinfo=UTC)
    return max(0, math.ceil((moment - (now or datetime.now(UTC))).total_seconds()))


def _body_hint(body) -> int | None:
    value = body.get("retry_after_s") if isinstance(body, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def retry_after_s(headers: Mapping[str, str] | None, body=None, now: datetime | None = None) -> int | None:
    """The wait a response asks for, in seconds, or None when it gives no hint.

    Reads the `Retry-After` header and the body's `retry_after_s`; when both are present the
    larger wins (each is a minimum). Use it on any response; only 429 and 503 SHOULD carry one.
    """
    hints = [
        h for h in (parse_retry_after(get_header(headers or {}, "Retry-After"), now), _body_hint(body)) if h is not None
    ]
    return max(hints) if hints else None


def retry_after_header(seconds: int) -> dict[str, str]:
    """The header a provider sends for a wait of `seconds` (delta-seconds form)."""
    return {"Retry-After": str(max(0, int(seconds)))}


def backpressure_problems(
    status: int, headers: Mapping[str, str] | None, body, now: datetime | None = None
) -> list[str]:
    """SHOULD-level problems with a 429 or 503 response's back-pressure hint (empty otherwise)."""
    if status not in BACKPRESSURE_CODES:
        return []
    problems = []
    raw = get_header(headers or {}, "Retry-After")
    header = parse_retry_after(raw, now)
    field = _body_hint(body)
    if raw is None:
        problems.append(f"{status} without a Retry-After header")
    elif header is None:
        problems.append(f"Retry-After {raw!r} is neither delta-seconds nor an HTTP-date")
    if field is None:
        problems.append(f"{status} without retry_after_s in the Problem Details")
    if header is not None and field is not None and abs(header - field) > 1:
        problems.append(f"Retry-After ({header} s) and retry_after_s ({field} s) disagree")
    code = body.get("code") if isinstance(body, dict) else None
    if code != BACKPRESSURE_CODES[status]:
        problems.append(f"{status} with code {code!r}, expected {BACKPRESSURE_CODES[status]}")
    return problems
