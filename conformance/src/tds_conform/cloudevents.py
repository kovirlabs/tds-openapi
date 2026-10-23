"""CloudEvents 1.0 binary-mode rendering of the webhook envelope (SPEC.md §9.1).

The body of a delivery stays the TDS event, byte for byte. A sender that renders CloudEvents
adds `ce-*` HTTP headers derived from the envelope, so CloudEvents tooling can route and
dedupe without parsing the body:

    ce-specversion  1.0
    ce-id           event_id
    ce-source       urn:tds:provider:<provider_id, percent-encoded>
    ce-type         org.tds-openapi.<event_type in lower case>
    ce-time         occurred_at
    ce-subject      trips/<trip_id>[/legs/<leg_id>]        (absent without trip_id)
    ce-dataschema   https://tds-openapi.org/schemas/<schema_version>/<profile>/<schema>.yaml
    ce-providerid   provider_id                             (extension)
    ce-schemaversion schema_version                         (extension)

Content-Type stays `application/json` (binary mode carries `datacontenttype` there, never
as a `ce-` header). Header values are percent-encoded as the CloudEvents HTTP binding
requires (space, `"`, `%` and anything outside printable ASCII).
"""

from __future__ import annotations

from collections.abc import Mapping
from urllib.parse import quote, unquote

from tds_conform.data import ID_BASE

SPECVERSION = "1.0"
SOURCE_PREFIX = "urn:tds:provider:"
TYPE_PREFIX = "org.tds-openapi."
HEADER_PREFIX = "ce-"
# Attributes this binding renders, in header order. `subject` only when the event has a trip_id.
ATTRIBUTES = (
    "specversion",
    "id",
    "source",
    "type",
    "time",
    "subject",
    "dataschema",
    "providerid",
    "schemaversion",
)

__all__ = [
    "ATTRIBUTES",
    "SOURCE_PREFIX",
    "SPECVERSION",
    "TYPE_PREFIX",
    "cloudevents_headers",
    "cloudevents_problems",
    "dedupe_key",
    "event_type_from_ce",
    "header_value",
    "parse_header_value",
    "render",
]


def _event_schema(event_type: str) -> str:
    from tds_conform.events import EVENT_SCHEMAS

    return EVENT_SCHEMAS.get(event_type, "core/event")


def render(event: Mapping) -> dict[str, str]:
    """CloudEvents attributes (unencoded) for a TDS event envelope."""
    attrs = {
        "specversion": SPECVERSION,
        "id": event["event_id"],
        "source": SOURCE_PREFIX + quote(event["provider_id"], safe=""),
        "type": TYPE_PREFIX + event["event_type"].lower(),
        "time": event["occurred_at"],
    }
    if event.get("trip_id"):
        subject = "trips/" + quote(event["trip_id"], safe="")
        if event.get("leg_id"):
            subject += "/legs/" + quote(event["leg_id"], safe="")
        attrs["subject"] = subject
    attrs["dataschema"] = f"{ID_BASE}{event['schema_version']}/{_event_schema(event['event_type'])}.yaml"
    attrs["providerid"] = event["provider_id"]
    attrs["schemaversion"] = event["schema_version"]
    return attrs


def header_value(value: str) -> str:
    """Percent-encode a header value as the CloudEvents HTTP binding requires."""
    out = []
    for ch in value:
        if ch in ' "%' or not ("!" <= ch <= "~"):
            out.append(quote(ch, safe=""))
        else:
            out.append(ch)
    return "".join(out)


def parse_header_value(value: str) -> str:
    return unquote(value)


def cloudevents_headers(event: Mapping) -> dict[str, str]:
    """The `ce-*` headers for one delivery of `event` (binary content mode)."""
    return {HEADER_PREFIX + name: header_value(value) for name, value in render(event).items()}


def event_type_from_ce(ce_type: str) -> str | None:
    """`org.tds-openapi.milestone` → `MILESTONE`; None if the type is not this binding's."""
    if not ce_type.startswith(TYPE_PREFIX):
        return None
    return ce_type[len(TYPE_PREFIX) :].upper()


def dedupe_key(event: Mapping) -> tuple[str, str]:
    """Receivers dedupe on (provider_id, event_id): CloudEvents (source, id)."""
    return (event.get("provider_id"), event.get("event_id"))


def cloudevents_problems(headers: Mapping[str, str], event: Mapping) -> list[str]:
    """What is wrong with the `ce-*` headers of a delivery of `event`; [] when none are sent.

    Headers are matched case-insensitively. When any `ce-` header is present, every
    attribute this binding renders must be present and equal its derived value.
    """
    lower = {k.lower(): v for k, v in headers.items()}
    sent = {k[len(HEADER_PREFIX) :]: v for k, v in lower.items() if k.startswith(HEADER_PREFIX)}
    if not sent:
        return []
    problems = []
    if "datacontenttype" in sent:
        problems.append("binary mode carries datacontenttype in Content-Type, not ce-datacontenttype")
    content_type = lower.get("content-type", "")
    if not content_type.startswith("application/json"):
        problems.append(f"Content-Type {content_type!r} is not application/json")
    try:
        expected = render(event)
    except (KeyError, AttributeError, TypeError):
        return [*problems, "body is not a TDS event envelope"]
    for name in ATTRIBUTES:
        if name not in expected:
            if name in sent:
                problems.append(f"ce-{name} sent for an event without trip_id")
            continue
        if name not in sent:
            problems.append(f"missing ce-{name}")
        elif parse_header_value(sent[name]) != expected[name]:
            problems.append(f"ce-{name} is {parse_header_value(sent[name])!r}, expected {expected[name]!r}")
    return problems
