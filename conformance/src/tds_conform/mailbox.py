"""Relay mailbox for CSV profile files (SPEC.md §9.4): the rules shared by `tds-mock` and the kit.

A requester serves the mailbox; a provider's agent, which can only call out, polls it:

    GET  /outbox               request files not yet acknowledged (csv/mailbox-listing)
    GET  /outbox/{file_name}   one file's bytes, with X-Relay-SHA256
    POST /ack/{file_name}      the agent has the file (csv/mailbox-ack); idempotent
    PUT  /inbox/{file_name}    an answers or statuses file, write-once by name (csv/mailbox-receipt)

`Authorization: Bearer <token>`; the token names one provider's mailbox.
"""

import csv
import hashlib
import io
import re
from collections.abc import Iterable, Mapping

from tds_conform.csvfile import FILE_NAME

__all__ = [
    "AGENT_HEADER",
    "INBOX_KINDS",
    "MIN_MAX_FILE_BYTES",
    "SAFE_NAME",
    "SHA256_HEADER",
    "inbox_name_problem",
    "is_outbox_name",
    "sha256_hex",
    "upload_report",
]

SHA256_HEADER = "X-Relay-SHA256"
AGENT_HEADER = "X-Relay-Agent"
# Characters a mailbox file name may use at all (SPEC.md §9.4): nothing that could leave the
# mailbox's folder or need quoting.
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
INBOX_KINDS = ("answers", "statuses")
# The least max_file_bytes a mailbox may declare.
MIN_MAX_FILE_BYTES = 1_000_000


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_outbox_name(name: str) -> bool:
    """A requests file name per SPEC.md §9.2."""
    match = FILE_NAME.match(name)
    return bool(match and match.group(1) == "requests")


def inbox_name_problem(name: str) -> str | None:
    """Why `name` cannot be uploaded (SPEC.md §9.4), or None when it is an answers or statuses
    file name per SPEC.md §9.2."""
    if not SAFE_NAME.match(name) or name.startswith("."):
        return "use only letters, digits, '_', '-' and '.'"
    match = FILE_NAME.match(name)
    if match is None:
        return "not a file name per SPEC.md §9.2 (tds_<answers|statuses>_<YYYYMMDDTHHMMSSZ>[_<tag>].csv)"
    if match.group(1) not in INBOX_KINDS:
        return f"a {match.group(1)} file goes from requester to provider; upload answers or statuses files"
    return None


# --- upload reports (SPEC.md §9.4) -------------------------------------------------------

ROW_OUTCOMES = ("APPLIED", "DUPLICATE", "REJECTED")


def upload_report(
    data: bytes,
    kind: str,
    *,
    provider_id: str,
    requester_id: str | None = None,
    requests: Iterable[Mapping] | None = None,
    applied: dict | None = None,
) -> dict:
    """What a requester following SPEC.md §9.4 reports for one uploaded answers or statuses file:
    `status` (PROCESSED or REFUSED with `refusal`), the counts and the rows not applied.

    `requests` are the request rows this requester sent the provider (typed, as `csvfile.read`
    gives them); None skips the UNKNOWN_REQUEST / UNKNOWN_TRIP checks. `applied` maps each key
    applied so far to its row and is updated with the rows this file applies, so files are
    reported in the order they are processed. One code per rejected row, the first that applies
    in `csv/row-problem` order.
    """
    from tds_conform.csvfile import CsvError, _decode, _schema, columns, row_key

    applied = {} if applied is None else applied
    try:
        text = _decode(data)
    except CsvError:
        return {"status": "REFUSED", "refusal": "INVALID_ENCODING"}
    reader = csv.reader(io.StringIO(text, newline=""))
    header = [h.strip() for h in next(reader, [])]
    required = _schema(kind).get("required", [])
    if not header or "" in header or len(set(header)) != len(header) or set(required) - set(header):
        return {"status": "REFUSED", "refusal": "INVALID_HEADER"}
    types = columns(kind)
    known_requests = None if requests is None else {row_key("requests", r) for r in requests}
    known_legs = None if requests is None else {(r.get("trip_id"), r.get("leg_id")) for r in requests}
    validator = _row_validator(kind)
    rows, counts, number = [], dict.fromkeys(ROW_OUTCOMES, 0), 1

    def reject(code: str, column: str | None) -> None:
        counts["REJECTED"] += 1
        rows.append({"row": number, "outcome": "REJECTED", "code": code} | ({"column": column} if column else {}))

    for cells in reader:
        if not any(c.strip() for c in cells):
            continue
        number += 1
        found = _row_problem(kind, header, cells, types, validator, provider_id, requester_id)
        if isinstance(found, tuple):
            reject(*found)
            continue
        row, key = found, row_key(kind, found)
        if kind == "answers" and known_requests is not None and key not in known_requests:
            reject("UNKNOWN_REQUEST", "trip_id")
        elif kind == "statuses" and known_legs is not None and (row["trip_id"], row["leg_id"]) not in known_legs:
            reject("UNKNOWN_TRIP", "trip_id")
        elif key in applied and applied[key] != row:
            reject("KEY_REUSED", None)
        elif key in applied:
            counts["DUPLICATE"] += 1
            rows.append({"row": number, "outcome": "DUPLICATE"})
        else:
            applied[key] = row
            counts["APPLIED"] += 1
    return {
        "status": "PROCESSED",
        "row_count": number - 1,
        "applied_count": counts["APPLIED"],
        "duplicate_count": counts["DUPLICATE"],
        "rejected_count": counts["REJECTED"],
        "rows": rows,
    }


def _row_validator(kind: str):
    from jsonschema import Draft202012Validator

    from tds_conform.csvfile import _schema_id
    from tds_conform.data import registry

    reg = registry()
    return Draft202012Validator(
        reg.contents(_schema_id(kind)), registry=reg, format_checker=Draft202012Validator.FORMAT_CHECKER
    )


def _row_problem(kind, header, cells, types, validator, provider_id, requester_id) -> dict | tuple[str, str | None]:
    """The typed row, or (code, column) for the first problem found in its cells."""
    from tds_conform.csvfile import _mapped_errors, _parse_cell

    if len(cells) != len(header):
        return ("INVALID_VALUE", None)
    row = {}
    for name, cell in zip(header, cells, strict=True):
        if name not in types or cell == "":
            continue
        try:
            row[name] = _parse_cell(cell, types[name])
        except ValueError:
            return ("INVALID_VALUE", name)
    errors = list(validator.iter_errors(row))
    # The schema's own required columns first, then those a condition requires (e.g. an ACCEPTED
    # answer's provider_trip_id), each in header order.
    always = [c for c in validator.schema.get("required", []) if c not in row]
    conditional = sorted(
        {c for e in errors if e.validator == "required" for c in e.validator_value if c not in row},
        key=lambda c: header.index(c) if c in header else len(header),
    )
    if always or conditional:
        return ("MISSING_VALUE", (always or conditional)[0])
    if errors:
        first = min(errors, key=lambda e: header.index(e.absolute_path[0]) if e.absolute_path else len(header))
        return ("INVALID_VALUE", str(first.absolute_path[0]) if first.absolute_path else None)
    if row.get("provider_id") != provider_id:
        return ("WRONG_PROVIDER", "provider_id")
    if kind == "answers" and requester_id is not None and row.get("requester_id") != requester_id:
        return ("WRONG_REQUESTER", "requester_id")
    if _mapped_errors(kind, row, None):
        return ("INVALID_VALUE", None)
    return row
