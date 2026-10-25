"""TDS CSV profile: batch files for providers without an API (SPEC.md §9.2).

Three file types, each a CSV with one header row:

    tds_requests_<YYYYMMDDTHHMMSSZ>[_<tag>].csv requester → provider   profiles/csv/request-row.yaml
    tds_answers_<YYYYMMDDTHHMMSSZ>[_<tag>].csv  provider → requester   profiles/csv/answer-row.yaml
    tds_statuses_<YYYYMMDDTHHMMSSZ>[_<tag>].csv provider → requester   profiles/csv/status-row.yaml

Format: RFC 4180 (comma, double-quote quoting, CRLF; readers accept LF), UTF-8 (readers
strip a byte-order mark; writers don't write one). Columns are matched by header name, in
any order; unknown columns are ignored. An empty cell is an absent value. Numbers use `.`
as the decimal point, booleans are `true`/`false`, lists are `|`-separated, times are
RFC 3339 with offset, coordinates WGS84 `lat`/`lon`.

Each row schema's `x-maps-to` gives a column's JSON Pointer in the equivalent API object
(per action or result, for request and answer rows), which `row_to_json` and `json_to_row`
use to convert either way.

    from tds_conform.csvfile import read, file_problems, row_to_json

    problems = file_problems(data, filename="tds_requests_20261102T220000Z.csv")
    rows, problems = read(data, "requests")
    call = row_to_json("requests", rows[0])   # {"operation": "POST /trips", "trip_id": ..., "body": {...}}
"""

from __future__ import annotations

import csv
import io
import math
import re
from collections.abc import Iterable, Mapping
from functools import cache
from typing import Any
from urllib.parse import quote

from jsonschema import Draft202012Validator

from tds_conform import SPEC_VERSION
from tds_conform.data import ID_BASE, registry, schema_errors

__all__ = [
    "FILE_KINDS",
    "FILE_NAME",
    "LIST_SEPARATOR",
    "CsvError",
    "columns",
    "exchange_problems",
    "file_name",
    "file_problems",
    "idempotency_key",
    "json_to_row",
    "kind_from_name",
    "read",
    "row_at",
    "row_key",
    "row_to_json",
    "write",
]

FILE_KINDS = {"requests": "csv/request-row", "answers": "csv/answer-row", "statuses": "csv/status-row"}
# The optional tag tells apart files written in the same second: 1-40 letters, digits or `-`
# (a sequence number or a random suffix).
FILE_NAME = re.compile(r"^tds_(requests|answers|statuses)_([0-9]{8}T[0-9]{6}Z)(_[A-Za-z0-9-]{1,40})?\.csv$")
LIST_SEPARATOR = "|"
BOM = "﻿"
# Operation each request action stands for.
OPERATIONS = {
    "REQUEST": "POST /trips",
    "CONFIRM": "POST /trips/{trip_id}/confirm",
    "MODIFY": "PATCH /trips/{trip_id}",
    "CANCEL": "POST /trips/{trip_id}/cancel",
}
REQUEST_SCHEMAS = {"REQUEST": "trip-request", "MODIFY": "trip-modification", "CANCEL": "cancel-request"}
# HTTP status of the Problem Details a rejected row stands for.
PROBLEM_STATUS = {"INVALID_STATE": 409, "NOT_FOUND": 404, "INVALID_REQUEST": 400}
_INT = re.compile(r"^[+-]?[0-9]+$")
_NUM = re.compile(r"^[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?$")


class CsvError(ValueError):
    """A file that cannot be read as a TDS CSV file at all."""


def kind_from_name(name: str) -> str | None:
    """`tds_answers_20261102T233000Z.csv` → `answers`; None if the name does not follow §9.2."""
    match = FILE_NAME.match(name.rsplit("/", 1)[-1])
    return match.group(1) if match else None


def file_name(kind: str, when: str, sequence: int | str | None = None) -> str:
    """`file_name("requests", "20261102T220000Z")` → `tds_requests_20261102T220000Z.csv`; `sequence`
    is the optional tag (a number, or 1-40 letters, digits or `-`)."""
    if kind not in FILE_KINDS:
        raise ValueError(f"unknown file kind {kind!r}")
    return f"tds_{kind}_{when}" + (f"_{sequence}" if sequence is not None else "") + ".csv"


# --- schema-driven column information ------------------------------------------------


def _schema_id(kind: str) -> str:
    return f"{ID_BASE}{FILE_KINDS[kind]}.yaml"


@cache
def _schema(kind: str) -> dict:
    return registry().contents(_schema_id(kind))


def _resolved_type(sub: Mapping, base: str) -> str:
    if "type" in sub:
        return sub["type"]
    if "$ref" in sub:
        target = registry().resolver(base_uri=base).lookup(sub["$ref"]).contents
        return _resolved_type(target, target.get("$id", base))
    for option in sub.get("anyOf", []):
        return _resolved_type(option, base)
    return "string"


@cache
def columns(kind: str) -> dict[str, str]:
    """Column name → JSON type ("string", "number", "integer", "boolean", "array"), in schema order."""
    schema = _schema(kind)
    return {name: _resolved_type(sub, _schema_id(kind)) for name, sub in schema["properties"].items()}


def _mappings(kind: str) -> dict[str, Any]:
    return {name: sub.get("x-maps-to") for name, sub in _schema(kind)["properties"].items()}


def _discriminator(kind: str) -> str | None:
    return _schema(kind).get("x-csv-discriminator")


# --- cells -------------------------------------------------------------------------


def _parse_cell(text: str, type_: str):
    if type_ == "integer":
        if not _INT.match(text):
            raise ValueError(f"{text!r} is not an integer")
        return int(text)
    if type_ == "number":
        if not _NUM.match(text):
            raise ValueError(f"{text!r} is not a number (use . as the decimal point)")
        value = float(text)
        if not math.isfinite(value):
            raise ValueError(f"{text!r} is not a finite number")
        return int(value) if _INT.match(text) else value
    if type_ == "boolean":
        if text.lower() not in ("true", "false"):
            raise ValueError(f"{text!r} is not true or false")
        return text.lower() == "true"
    if type_ == "array":
        return [part.strip() for part in text.split(LIST_SEPARATOR) if part.strip()]
    return text


def _format_cell(value, type_: str) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return LIST_SEPARATOR.join(str(v) for v in value)
    if isinstance(value, float):
        return repr(value)
    return str(value)


# --- reading and writing -------------------------------------------------------------


def _decode(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CsvError(f"not UTF-8 (byte {exc.start}); TDS CSV files are UTF-8") from exc
    return text[1:] if text.startswith(BOM) else text


def read(data: bytes, kind: str) -> tuple[list[dict], list[str]]:
    """Typed rows of a file and the problems found reading it (not yet schema problems).

    Each row is a dict of the columns that had a value, plus `_line` (its line number).
    Raises CsvError when the file cannot be decoded or has no header.
    """
    types = columns(kind)
    reader = csv.reader(io.StringIO(_decode(data), newline=""))
    try:
        header = next(reader)
    except StopIteration:
        raise CsvError("empty file: a header row is required") from None
    header = [h.strip() for h in header]
    problems = []
    if len(set(header)) != len(header):
        dupes = sorted({h for h in header if header.count(h) > 1})
        problems.append(f"line 1: duplicate columns {dupes}")
    if "" in header:
        problems.append("line 1: a column has no name")
    missing = [c for c in _schema(kind).get("required", []) if c not in header]
    if missing:
        problems.append(f"line 1: missing required columns {missing}")
    rows = []
    for cells in reader:
        line = reader.line_num
        if not any(c.strip() for c in cells):
            continue
        if len(cells) != len(header):
            problems.append(f"line {line}: {len(cells)} cells, header has {len(header)}")
            continue
        row: dict[str, Any] = {"_line": line}
        for name, cell in zip(header, cells, strict=True):
            if name not in types or cell == "":
                continue
            try:
                row[name] = _parse_cell(cell, types[name])
            except ValueError as exc:
                problems.append(f"line {line}: {name}: {exc}")
        rows.append(row)
    return rows, problems


def write(rows: Iterable[Mapping], kind: str) -> bytes:
    """A file of `rows` (UTF-8 without BOM, CRLF): required columns, then any column used, in schema order."""
    rows = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
    types = columns(kind)
    used = set(_schema(kind).get("required", [])) | {k for r in rows for k, v in r.items() if v is not None}
    header = [c for c in types if c in used]
    out = io.StringIO(newline="")
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(header)
    for r in rows:
        writer.writerow([_format_cell(r.get(c), types[c]) for c in header])
    return out.getvalue().encode("utf-8")


# --- keys and conversion --------------------------------------------------------------


def row_key(kind: str, row: Mapping) -> tuple:
    """Idempotency key of a row. Requests and answers: (requester, provider, trip, leg, action,
    revision); an answer has its request's key. Statuses: (provider_id, event_id)."""
    if kind == "statuses":
        return (row.get("provider_id"), row.get("event_id"))
    return tuple(row.get(c) for c in ("requester_id", "provider_id", "trip_id", "leg_id", "action")) + (
        row.get("revision") or 1,
    )


def idempotency_key(row: Mapping) -> str:
    """The request row's key as an `Idempotency-Key` value, for a bridge that replays rows as API calls."""
    _, _, trip, leg, action, revision = row_key("requests", row)
    return "/".join(quote(str(p), safe="") for p in (trip, leg, action, revision))


def _set(target: dict, pointer: str, value) -> None:
    parts = pointer.strip("/").split("/")
    node: Any = target
    for part, nxt in zip(parts, parts[1:] + [None], strict=True):
        key: Any = int(part) if isinstance(node, list) else part
        if nxt is None:
            if isinstance(node, list):
                while len(node) <= key:
                    node.append({})
            node[key] = value
            return
        if isinstance(node, list):
            while len(node) <= key:
                node.append({})
            node = node[key]
        else:
            node = node.setdefault(key, [] if nxt.isdigit() else {})


def _get(source, pointer: str):
    node = source
    for part in pointer.strip("/").split("/"):
        if isinstance(node, list):
            if not part.isdigit() or int(part) >= len(node):
                return None
            node = node[int(part)]
        elif isinstance(node, Mapping):
            if part not in node:
                return None
            node = node[part]
        else:
            return None
    return node


def _pointer(mapping, selector: str | None) -> str | None:
    if isinstance(mapping, str):
        return mapping
    if isinstance(mapping, Mapping):
        return mapping.get(selector)
    return None


def _apply(kind: str, row: Mapping, selector: str | None, target: dict) -> dict:
    for name, mapping in _mappings(kind).items():
        pointer = _pointer(mapping, selector)
        if pointer and name in row and row[name] is not None:
            _set(target, pointer, row[name])
    return target


def row_to_json(kind: str, row: Mapping, *, schema_version: str | None = None, request: Mapping | None = None):
    """The API object a row stands for.

    - requests: `{"operation", "trip_id", "body"}`; `body` is None for CONFIRM.
    - answers: `{"trip": ...}` for ACCEPTED (a full Trip when the answered request row is
      given, else the answered fields only) or `{"problem": ...}` for REJECTED.
    - statuses: the event envelope; `schema_version` fills an empty schema_version cell
      (default: this binding's version).
    """
    if kind == "requests":
        action = row["action"]
        body = _apply(kind, row, action, {}) if action != "CONFIRM" else None
        return {"operation": OPERATIONS[action], "trip_id": row["trip_id"], "body": body}
    if kind == "answers":
        if row["result"] == "REJECTED":
            code = row.get("reason")
            problem = {
                "type": "about:blank",
                "title": "Refused" if code not in PROBLEM_STATUS else code.replace("_", " ").capitalize(),
                "status": PROBLEM_STATUS.get(code, 422),
            }
            return {"problem": _apply(kind, row, "REJECTED", problem)}
        trip: dict = {}
        if request is not None:
            trip = row_to_json("requests", request)["body"] or {}
            trip["created_at"] = row["answered_at"]
        return {"trip": _apply(kind, row, "ACCEPTED", trip)}
    if kind == "statuses":
        event = _apply(kind, row, None, {"payload": {}})
        event.setdefault("schema_version", schema_version or SPEC_VERSION)
        return event
    raise ValueError(f"unknown file kind {kind!r}")


def json_to_row(kind: str, obj: Mapping, **columns_given) -> dict:
    """A row from an API object (the inverse of `row_to_json`).

    Give the discriminator column (`action` for requests, `result` and `action` for answers)
    and any key column the object does not carry (e.g. `trip_id` for MODIFY, `leg_id`).
    """
    selector = columns_given.get(_discriminator(kind)) if _discriminator(kind) else None
    row: dict[str, Any] = {}
    for name, mapping in _mappings(kind).items():
        pointer = _pointer(mapping, selector)
        if pointer is not None and (value := _get(obj, pointer)) is not None:
            row[name] = value
    row.update(columns_given)
    return {name: row[name] for name in columns(kind) if name in row}


# --- checking -------------------------------------------------------------------------


def _row_errors(kind: str, row: Mapping) -> list[str]:
    reg = registry()
    validator = Draft202012Validator(
        reg.contents(_schema_id(kind)), registry=reg, format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    clean = {k: v for k, v in row.items() if not k.startswith("_")}
    return [f"{'/'.join(map(str, e.absolute_path)) or '(row)'}: {e.message}" for e in validator.iter_errors(clean)]


def _mapped_errors(kind: str, row: Mapping, schema_version: str | None) -> list[str]:
    if kind == "requests":
        schema = REQUEST_SCHEMAS.get(row["action"])
        body = row_to_json(kind, row)["body"]
        return [f"as {schema}: {e}" for e in schema_errors(body, schema)] if schema else []
    if kind == "answers":
        converted = row_to_json(kind, row)
        if "problem" in converted:
            return [f"as error: {e}" for e in schema_errors(converted["problem"], "error")]
        return []
    from tds_conform.events import event_errors

    return [f"as event: {e}" for e in event_errors(row_to_json(kind, row, schema_version=schema_version))]


def file_problems(
    data: bytes, kind: str | None = None, *, filename: str | None = None, schema_version: str | None = None
) -> list[str]:
    """Everything wrong with one TDS CSV file; [] when it conforms to SPEC.md §9.2.

    Checks the name (when given), encoding, header, each cell's type, each row against its row
    schema and, converted, against the API schema it stands for; that a key is not reused for
    different content; and, for statuses, the milestone order (SPEC.md §10.3).
    """
    problems = []
    if filename is not None:
        named = kind_from_name(filename)
        if named is None:
            problems.append(f"file name {filename!r} is not tds_<requests|answers|statuses>_<YYYYMMDDTHHMMSSZ>.csv")
        elif kind is not None and kind != named:
            problems.append(f"file name says {named}, checked as {kind}")
        kind = kind or named
    if kind not in FILE_KINDS:
        return [*problems, "unknown file kind; name the file per SPEC.md §9.2 or pass the kind"]
    try:
        rows, read_problems = read(data, kind)
    except CsvError as exc:
        return [*problems, str(exc)]
    problems += read_problems
    seen: dict[tuple, tuple[int, dict]] = {}
    for row in rows:
        line = row["_line"]
        errors = _row_errors(kind, row)
        if not errors:
            errors = _mapped_errors(kind, row, schema_version)
        problems += [f"line {line}: {e}" for e in errors]
        key = row_key(kind, row)
        clean = {k: v for k, v in row.items() if k != "_line"}
        if key in seen and seen[key][1] != clean:
            problems.append(f"line {line}: same key as line {seen[key][0]} with different content")
        seen.setdefault(key, (line, clean))
    if kind == "statuses" and not problems:
        from tds_conform.events import dedupe, milestone_problems

        events = dedupe([row_to_json(kind, r, schema_version=schema_version) for r in rows])
        problems += milestone_problems(events)
    return problems


def row_at(data: bytes, kind: str, line: int) -> dict:
    """The typed row on `line` (1-based, counting the header) of a file."""
    rows, _ = read(data, kind)
    for row in rows:
        if row["_line"] == line:
            return {k: v for k, v in row.items() if k != "_line"}
    raise KeyError(f"no row on line {line}")


def exchange_problems(files: Mapping[str, bytes]) -> list[str]:
    """Problems across the files of one exchange (name → bytes, names per SPEC.md §9.2).

    Every answer row must answer a request row of one of the requests files, and must come
    from the provider and for the requester that row names.
    """
    requests = set()
    for name, data in files.items():
        if kind_from_name(name) == "requests":
            rows, _ = read(data, "requests")
            requests |= {row_key("requests", r) for r in rows}
    problems = []
    for name, data in sorted(files.items()):
        if kind_from_name(name) == "answers":
            rows, _ = read(data, "answers")
            problems += [
                f"{name} line {r['_line']}: answers no request row {row_key('answers', r)}"
                for r in rows
                if row_key("answers", r) not in requests
            ]
    return problems
