"""TDS CSV profile (SPEC.md §9.2): reading, writing, conversion and checks."""

import json

import pytest

from tds_conform.cli import conform_main
from tds_conform.csvfile import (
    columns,
    exchange_problems,
    file_name,
    file_problems,
    idempotency_key,
    json_to_row,
    kind_from_name,
    read,
    row_at,
    row_key,
    row_to_json,
    write,
)
from tds_conform.data import data_root, schema_errors
from tds_conform.examples import load_example

BATCH = data_root() / "examples" / "csv-batch"
REQUESTS = "tds_requests_20261104T150000Z.csv"
ANSWERS = "tds_answers_20261104T204000Z.csv"
STATUSES = "tds_statuses_20261105T150000Z.csv"
HEADER = "requester_id,provider_id,trip_id,leg_id,action,rider_ref_id,pickup_lat,pickup_lon,dropoff_lat,dropoff_lon,"


def _bytes(name: str) -> bytes:
    return (BATCH / name).read_bytes()


def _request_csv(*rows: str, header: str = HEADER + "pickup_earliest,pickup_latest") -> bytes:
    return ("\r\n".join([header, *rows]) + "\r\n").encode()


GOOD_ROW = (
    "req,prov,tr-1,leg-1,REQUEST,r-1,42.28,-83.74,42.25,-83.69,2026-11-03T09:00:00-05:00,2026-11-03T09:30:00-05:00"
)


def test_file_names():
    assert kind_from_name("tds_requests_20261104T150000Z.csv") == "requests"
    assert kind_from_name("out/tds_statuses_20261104T150000Z_2.csv") == "statuses"
    assert kind_from_name("trips.csv") is None
    assert kind_from_name("tds_requests_2026-11-04.csv") is None
    assert file_name("answers", "20261104T204000Z", 3) == "tds_answers_20261104T204000Z_3.csv"


def test_examples_conform():
    for path in sorted(BATCH.glob("*.csv")):
        assert file_problems(path.read_bytes(), filename=path.name) == [], path.name
    assert exchange_problems({p.name: p.read_bytes() for p in BATCH.glob("*.csv")}) == []


def test_bom_crlf_lf_and_column_order_are_tolerated():
    base = _request_csv(GOOD_ROW)
    assert file_problems(b"\xef\xbb\xbf" + base, "requests") == []
    assert file_problems(base.replace(b"\r\n", b"\n"), "requests") == []
    header, row = HEADER + "pickup_earliest,pickup_latest", GOOD_ROW
    swapped_header = ",".join(reversed(header.split(",")))
    swapped_row = ",".join(reversed(row.split(",")))
    assert file_problems(_request_csv(swapped_row, header=swapped_header), "requests") == []
    extra = _request_csv(GOOD_ROW + ",whatever", header=header + ",vendor_extra_column")
    assert file_problems(extra, "requests") == []


@pytest.mark.parametrize(
    ("data", "problem"),
    [
        ("café".encode("latin-1"), "not UTF-8"),
        (b"", "empty file"),
        (_request_csv(GOOD_ROW, header=HEADER.replace("trip_id,", "") + "pickup_earliest,pickup_latest"), "missing"),
        (_request_csv(GOOD_ROW.replace("42.28", "42,28")), "cells, header has"),
        (_request_csv(GOOD_ROW.replace("42.28", '"42,28"')), "is not a number"),
        (_request_csv(GOOD_ROW.replace("REQUEST", "BOOK")), "BOOK"),
        (_request_csv(GOOD_ROW.replace("2026-11-03T09:00:00-05:00", "11/03/2026 9:00")), "date-time"),
        (
            _request_csv(GOOD_ROW.replace(",2026-11-03T09:00:00-05:00,2026-11-03T09:30:00-05:00", ",,")),
            "any of the given schemas",
        ),
        (_request_csv(GOOD_ROW, GOOD_ROW.replace(",r-1,", ",r-2,")), "same key as line 2 with different content"),
    ],
)
def test_file_problems(data, problem):
    problems = file_problems(data, "requests")
    assert any(problem in p for p in problems), problems


def test_identical_resent_rows_are_fine():
    assert file_problems(_request_csv(GOOD_ROW, GOOD_ROW), "requests") == []


def test_name_and_kind_must_agree():
    problems = file_problems(_request_csv(GOOD_ROW), "answers", filename="tds_requests_20261104T150000Z.csv")
    assert any("file name says requests" in p for p in problems)
    assert any("not tds_" in p for p in file_problems(_request_csv(GOOD_ROW), filename="trips.csv"))


def test_mapped_json_is_checked_too():
    # The row schema allows a phone with consent; the rider schema then needs consent true.
    header = HEADER + "pickup_earliest,pickup_latest,rider_phone,rider_contact_consent"
    data = _request_csv(GOOD_ROW + ",+15555550100,false", header=header)
    assert any("rider_contact_consent" in p or "true" in p for p in file_problems(data, "requests"))
    # A window that ends before it starts is a kit rule, not a schema rule: not flagged here.


def test_request_row_converts_to_trip_request_and_back():
    row = row_at(_bytes(REQUESTS), "requests", 2)
    call = row_to_json("requests", row)
    assert call["operation"] == "POST /trips"
    assert call["body"] == load_example("csv-batch", "equivalent-request.json")
    assert schema_errors(call["body"], "trip-request") == []
    back = json_to_row("requests", call["body"], action="REQUEST", revision=1)
    assert back == row


def test_modify_cancel_and_confirm_rows():
    modify, cancel = (row_at(_bytes("tds_requests_20261104T215000Z.csv"), "requests", n) for n in (2, 3))
    m = row_to_json("requests", modify)
    assert m["operation"] == "PATCH /trips/{trip_id}" and m["trip_id"] == "tr-20261105-0001"
    assert m["body"] == {
        "pickup_window": {"earliest": "2026-11-05T08:15:00-05:00", "latest": "2026-11-05T08:45:00-05:00"}
    }
    assert schema_errors(m["body"], "trip-modification") == []
    c = row_to_json("requests", cancel)
    assert c == {
        "operation": "POST /trips/{trip_id}/cancel",
        "trip_id": "tr-20261105-0002",
        "body": {"reason": "RIDER_REQUEST"},
    }
    confirm = {k: v for k, v in cancel.items() if k != "cancel_reason"} | {"action": "CONFIRM"}
    assert row_to_json("requests", confirm)["body"] is None
    keys = {"requester_id": "throughride-demo", "provider_id": "county-dial-a-ride", "leg_id": "leg-1"}
    back = json_to_row("requests", c["body"], action="CANCEL", trip_id=c["trip_id"], revision=1, **keys)
    assert back == cancel


def test_answer_rows_convert_to_trip_or_problem():
    answers = _bytes(ANSWERS)
    accepted = row_at(answers, "answers", 2)
    request = row_at(_bytes(REQUESTS), "requests", 2)
    trip = row_to_json("answers", accepted, request=request)["trip"]
    assert trip == load_example("csv-batch", "equivalent-trip.json")
    assert schema_errors(trip, "trip") == []
    assert trip["status"] == "CONFIRMED" and trip["provider_trip_id"] == "CDR-31877"
    refusal = row_to_json("answers", row_at(answers, "answers", 4))["problem"]
    assert refusal == load_example("csv-batch", "equivalent-refusal.json")
    assert refusal["status"] == 422 and refusal["code"] == "OUTSIDE_SERVICE_AREA"
    assert row_key("answers", accepted) == row_key("requests", request)


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"result": "REJECTED", "status": "REJECTED"}, "reason"),
        ({"provider_trip_id": None}, "provider_trip_id"),
        ({"status": "CANCELLED"}, "status"),
    ],
)
def test_answer_row_rules(change, problem):
    row = row_at(_bytes(ANSWERS), "answers", 2) | change
    data = write([{k: v for k, v in row.items() if v is not None}], "answers")
    assert any(problem in p for p in file_problems(data, "answers")), file_problems(data, "answers")


def test_status_rows_are_events():
    rows, problems = read(_bytes(STATUSES), "statuses")
    assert problems == []
    events = [row_to_json("statuses", r) for r in rows]
    types = {e["event_type"] for e in events}
    assert types == {"TRIP_STATUS_CHANGED", "TRIP_SCHEDULED", "TRIP_CANCELLED", "MILESTONE"}
    scheduled = next(e for e in events if e["event_type"] == "TRIP_SCHEDULED" and "vehicle_ref" in e["payload"])
    assert scheduled["payload"]["vehicle_ref"]["vehicle_id"] == "V-12"
    assert "requester_id" not in scheduled and "provider_trip_id" not in scheduled
    first = {k: v for k, v in rows[0].items() if k != "_line"}
    back = json_to_row("statuses", events[0])
    assert back == {k: v for k, v in first.items() if k not in ("requester_id", "provider_trip_id")}


def test_status_file_checks_milestone_order_and_default_version():
    rows, _ = read(_bytes(STATUSES), "statuses")
    rows = [{k: v for k, v in r.items() if k != "_line"} for r in rows]
    skipped = [r for r in rows if r.get("milestone") != "RIDER_ONBOARD"]
    problems = file_problems(write(skipped, "statuses"), "statuses")
    assert any("cannot follow ARRIVED_PICKUP" in p for p in problems), problems
    unversioned = [{k: v for k, v in r.items() if k != "schema_version"} for r in rows]
    assert file_problems(write(unversioned, "statuses"), "statuses") == []
    assert row_to_json("statuses", unversioned[0], schema_version="0.6.0")["schema_version"] == "0.6.0"


def test_answers_must_answer_a_request():
    stray = row_at(_bytes(ANSWERS), "answers", 2) | {"trip_id": "tr-unknown"}
    files = {REQUESTS: _bytes(REQUESTS), ANSWERS: write([stray], "answers")}
    assert any("answers no request row" in p for p in exchange_problems(files))


def test_write_round_trip_and_format():
    rows, _ = read(_bytes(REQUESTS), "requests")
    data = write(rows, "requests")
    assert data == _bytes(REQUESTS)
    assert not data.startswith(b"\xef\xbb\xbf") and b"\r\n" in data
    assert columns("requests")["service_requirements"] == "array"
    assert b"WHEELCHAIR_LIFT|DOOR_TO_DOOR" in data


def test_idempotency_key_for_api_bridges():
    row = row_at(_bytes(REQUESTS), "requests", 2)
    assert idempotency_key(row) == "tr-20261105-0001/leg-1/REQUEST/1"
    assert idempotency_key(row | {"trip_id": "a/b"}) == "a%2Fb/leg-1/REQUEST/1"


def test_cli_check_csv(tmp_path, capsys):
    paths = []
    for name in (REQUESTS, ANSWERS, STATUSES):
        (tmp_path / name).write_bytes(_bytes(name))
        paths.append(str(tmp_path / name))
    assert conform_main(["check-csv", *paths]) == 0
    bad = tmp_path / "export.csv"
    bad.write_bytes(_request_csv(GOOD_ROW.replace("REQUEST", "BOOK")))
    assert conform_main(["check-csv", str(bad), "--kind", "requests"]) == 1
    assert "BOOK" in capsys.readouterr().out


def test_examples_api_loads_csv_as_text():
    text = load_example("csv-batch", REQUESTS)
    assert text.startswith("requester_id,") and json.dumps(text)
