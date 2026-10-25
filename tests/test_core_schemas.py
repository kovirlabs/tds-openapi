"""Behavioural rules of the core schemas (SPEC.md §6) and the repo validator's own checks."""

import copy
import json
from pathlib import Path

import pytest

import validate_examples as ve

SCHEMAS = ve.load_schemas()
REGISTRY = ve.build_registry(SCHEMAS)
CORE = ve.ID_BASE + "core/"


def errors(schema_name: str, instance) -> list[str]:
    schema = REGISTRY.contents(CORE + schema_name)
    return [e.message for e in ve.validator_for(schema, REGISTRY).iter_errors(instance)]


def trip_example() -> dict:
    return copy.deepcopy(REGISTRY.contents(CORE + "trip.yaml")["examples"][0])


def test_repo_validates_cleanly():
    assert ve.main() == 0


def test_core_trip_has_exactly_one_leg():
    trip = trip_example()
    trip["legs"].append(copy.deepcopy(trip["legs"][0]))
    assert errors("trip.yaml", trip)
    trip["legs"] = []
    assert errors("trip.yaml", trip)


def test_leg_needs_a_pickup_or_dropoff_window():
    leg = trip_example()["legs"][0]
    del leg["pickup_window"]
    assert errors("leg.yaml", leg)
    leg["dropoff_window"] = {"earliest": "2026-11-03T10:00:00-05:00", "latest": "2026-11-03T10:30:00-05:00"}
    assert not errors("leg.yaml", leg)


def test_times_require_an_offset():
    window = {"earliest": "2026-11-03T09:00:00", "latest": "2026-11-03T09:30:00-05:00"}
    assert errors("time-window.yaml", window)


def test_rider_contact_requires_consent():
    rider = {"rider_ref_id": "r-1", "contact": {"phone": "+17345550123"}}
    assert errors("rider.yaml", rider)
    rider["contact_consent"] = False
    assert errors("rider.yaml", rider)
    rider["contact_consent"] = True
    assert not errors("rider.yaml", rider)


def test_unknown_service_requirement_rejected():
    leg = trip_example()["legs"][0]
    leg["service_requirements"] = ["TELEPORTER"]
    assert errors("leg.yaml", leg)


@pytest.mark.parametrize(
    ("schema", "problem"),
    [
        ({"title": "T", "description": "d", "examples": []}, "missing top-level `$id`"),
        ({"properties": {"tripId": {"description": "d"}}}, "property `tripId` is not snake_case"),
        ({"properties": {"trip_id": {"type": "string"}}}, "property `trip_id` has no description"),
        ({"enum": ["requested"]}, "enum value 'requested' is not UPPER_SNAKE_CASE"),
    ],
)
def test_convention_checks(schema, problem):
    assert problem in ve.check_conventions(ve.PROFILES / "core" / "x.yaml", schema)


def test_crosswalk_flags_unmapped_core_field(tmp_path: Path):
    path = ve.PROFILES / "core" / "widget.yaml"
    schemas = {path: {"properties": {"mapped": {}, "ext": {"x-tds-extension": True}, "orphan": {}}}}
    crosswalk = tmp_path / "cw.md"
    crosswalk.write_text("| `widget.mapped` | TODO(crosswalk) | | TODO | |\n")
    assert ve.check_crosswalk(schemas, crosswalk) == [
        "`widget.orphan` is not in the crosswalk and not marked x-tds-extension"
    ]


def test_example_not_in_manifest_is_reported(tmp_path: Path):
    scenario = tmp_path / "examples" / "s"
    scenario.mkdir(parents=True)
    (scenario / "stray.json").write_text("{}")
    results = ve.check_examples(REGISTRY, tmp_path / "examples")
    assert any("not listed" in e for e in results[scenario / "stray.json"])


@pytest.mark.parametrize(
    ("change", "reqs", "problem"),
    [
        ({}, ["HAND_TO_HAND"], None),
        ({"supervision_required": False}, ["HAND_TO_HAND"], "supervision_required"),
        ({"supervision_required": False, "protection": "BEST_EFFORT"}, ["HAND_TO_HAND"], None),
        ({"supervision_required": False}, ["WHEELCHAIR_LIFT"], None),
        ({"auto_hold_s": 1000, "max_hold_s": 900}, [], "auto_hold_s"),
    ],
)
def test_interchange_rules_on_tender_examples(change, reqs, problem):
    tender = copy.deepcopy(REGISTRY.contents(ve.ID_BASE + "transfer/tender-request.yaml")["examples"][0])
    tender["interchange"] |= {"supervision_required": True} | change
    tender["trip"]["legs"][0]["service_requirements"] = reqs
    found = ve.interchange_problems(tender["interchange"], tender["trip"]["legs"])
    assert any(problem in e for e in found) if problem else found == []


def test_cancel_reason_is_a_code_not_free_text():
    assert errors("cancel-request.yaml", {"reason": "RIDER_REQUEST"}) == []
    assert errors("cancel-request.yaml", {"reason": "rider changed plans"})


def test_health_must_list_core():
    health = {"status": "OK", "spec_versions": ["0.2.0"], "profiles": ["TRANSFER"], "operated_via": "DIRECT"}
    assert errors("health.yaml", health)


def test_subscription_callback_must_be_https():
    sub = {"callback_url": "http://broker.example.org/events", "event_types": ["TRIP_STATUS_CHANGED"]}
    assert errors("subscription-request.yaml", sub)


def test_trip_modification_cannot_be_empty():
    assert errors("trip-modification.yaml", {})


WIRED = Path("/x/examples/s/a.json")


@pytest.mark.parametrize(
    ("entry", "wiring", "problem"),
    [
        ({"operation": "GET /nope", "status": 200}, {}, "spec has no GET /nope 200"),
        (
            {"operation": "GET /t", "status": 200},
            {("GET /t", "200"): {"schema": CORE + "trip.yaml", "examples": set()}},
            "not referenced as an example of GET /t 200",
        ),
        (
            {"operation": "GET /t", "status": 200},
            {("GET /t", "200"): {"schema": CORE + "error.yaml", "examples": {WIRED}}},
            f"GET /t 200 declares {CORE}error.yaml, manifest says {CORE}trip.yaml",
        ),
    ],
)
def test_wiring_checks(entry, wiring, problem):
    manifests = {WIRED.parent: {WIRED.name: {"schema": CORE + "trip.yaml"} | entry}}
    assert problem in ve.check_wiring(manifests, wiring)[WIRED]


def test_spec_example_without_manifest_entry_is_reported():
    wiring = {("GET /t", "200"): {"schema": None, "examples": {WIRED}}}
    assert ve.check_wiring({}, wiring) == {WIRED: ["used by GET /t 200 but no manifest entry says so"]}


def test_bundle_finalizer_strips_ids_and_schema_examples():
    import finalize_bundle

    spec = {"components": {"schemas": {"trip": {"$id": "x", "$schema": "y", "examples": [{}], "type": "object"}}}}
    assert finalize_bundle.finalize(spec)["components"]["schemas"]["trip"] == {"type": "object"}


def test_kit_response_schemas_match_spec():
    from tds_conform.runner import RESPONSE_SCHEMAS

    declared = {
        (op, int(slot)): schema.split("/schemas/")[1].removesuffix(".yaml")
        for (op, slot), wired in ve.spec_wiring().items()
        if slot != "request" and (schema := wired["schema"]) and not slot.startswith(("4", "5"))
    }
    assert RESPONSE_SCHEMAS == declared


def test_kit_mailbox_response_schemas_match_spec():
    from tds_conform.runner import MAILBOX_RESPONSE_SCHEMAS

    declared = {
        (op, int(slot)): schema.split("/schemas/")[1].removesuffix(".yaml")
        for (op, slot), wired in ve.spec_wiring(ve.MAILBOX).items()
        if slot != "request" and (schema := wired["schema"]) and not slot.startswith(("4", "5"))
    }
    assert MAILBOX_RESPONSE_SCHEMAS == declared


def test_mailbox_listing_must_match_the_files_it_names(tmp_path: Path):
    import shutil

    scenario = tmp_path / "relay-mailbox"
    shutil.copytree(ve.EXAMPLES / "relay-mailbox", scenario)
    listing = json.loads((scenario / "01-list.response.json").read_text())
    assert ve.mailbox_listing_problems(scenario, listing) == []
    listing["files"][0] |= {"size": 1, "sha256": "0" * 64}
    problems = ve.mailbox_listing_problems(scenario, listing)
    assert any("size 1" in p for p in problems) and any("sha256" in p for p in problems)


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("tds_answers_20261104T204000Z.csv", "answers"),
        ("tds_answers_20261104T204000Z_2.csv", "answers"),
        ("tds_statuses_20261104T204000Z_1a2b3c4d.csv", "statuses"),
        ("tds_requests_20261104T204000Z_conform-ab12.csv", "requests"),
        ("tds_answers_batch42.csv", None),
        ("tds_answers_20261104T204000Z_.csv", None),
        ("tds_answers_20261104T204000Z_a.b.csv", None),
    ],
)
def test_csv_file_names_take_an_optional_tag(name, kind):
    from tds_conform.csvfile import kind_from_name

    assert kind_from_name(name) == kind


def test_event_coverage_reports_missing_states():
    manifests = ve.load_manifests()
    events = [
        e
        for e in ve.collect_events(manifests)
        if not (e["event_type"] == "TRIP_STATUS_CHANGED" and e["payload"]["status"] == "NO_SHOW")
        and e["event_type"] != "EXCEPTION_RESOLVED"
    ]
    problems = ve.check_event_coverage(manifests, REGISTRY, events=events)
    assert any("TRIP_STATUS_CHANGED" in p and "NO_SHOW" in p for p in problems)
    assert any(p.startswith("EXCEPTION_RESOLVED: no example event") for p in problems)
    assert ve.check_event_coverage(manifests, REGISTRY) == []


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"verified_at": "2027-07-01"}, "verified_at 2027-07-01 is after valid_until 2027-06-30"),
        ({"program": "AGE_SENIOR"}, "does not follow AGE_<N>_PLUS or AGE_UNDER_<N>"),
        ({"program": "DISABILITY_VISUAL"}, "takes no qualifier"),
        ({"program": "RESIDENT_US_MI_161"}, "RESIDENT_US_<5-digit county FIPS code>"),
    ],
)
def test_eligibility_rules_on_trip_examples(change, problem):
    trip = trip_example()
    trip["rider_ref"]["eligibility"] = [{"program": "AGE_60_PLUS", "attested_by": "b", "valid_until": "2027-06-30"}]
    assert ve.RULES[CORE + "trip.yaml"](trip) == []
    trip["rider_ref"]["eligibility"][0] |= change
    assert not errors("trip.yaml", trip)
    assert any(problem in p for p in ve.RULES[CORE + "trip.yaml"](trip))


DELIVERY = ve.EXAMPLES / "webhook-delivery"


def _tampered_delivery(tmp_path: Path, change) -> list[str]:
    import json
    import shutil

    scenario = tmp_path / "webhook-delivery"
    shutil.copytree(DELIVERY, scenario)
    path = scenario / "02-trip-status-changed.delivery.json"
    delivery = json.loads(path.read_text())
    change(delivery)
    path.write_text(json.dumps(delivery))
    entry = ve.load_manifests(tmp_path)[scenario][path.name]
    return ve.check_delivery(path, entry, REGISTRY, ve.event_wiring())


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda d: d.update(body=d["body"].replace("IN_PROGRESS", "COMPLETED")), "signature does not match"),
        (lambda d: d["headers"].update({"ce-type": "org.tds-openapi.milestone"}), "ce-type"),
        (lambda d: d["headers"].pop("ce-source"), "ce-source"),
        (lambda d: [d["headers"].pop(k) for k in list(d["headers"]) if k.startswith("ce-")], "cloudevents: true"),
        (lambda d: d["headers"].update({"ce-specversion": "0.3"}), "1.0"),
        (lambda d: d["headers"].update({"webhook-id": "evt-9999"}), "webhook-signature entry matches"),
        (lambda d: d["headers"].pop("webhook-signature"), "webhook-id/webhook-timestamp sent without"),
        (lambda d: d["headers"].update({"webhook-signature": "v1:abc"}), "does not match"),
    ],
)
def test_webhook_delivery_example_checks(tmp_path: Path, change, problem):
    problems = _tampered_delivery(tmp_path, change)
    assert any(problem in p for p in problems), problems


def test_webhook_delivery_example_is_clean(tmp_path: Path):
    assert _tampered_delivery(tmp_path, lambda d: None) == []


def test_backpressure_check_flags_undocumented_429_and_bad_examples(tmp_path: Path):
    spec = tmp_path / "openapi.yaml"
    spec.write_text(
        "openapi: 3.1.0\npaths:\n  /x:\n    get:\n      responses:\n        '429':\n          description: no header\n"
    )
    scenario = tmp_path / "s"
    scenario.mkdir()
    (scenario / "busy.json").write_text('{"code": "RATE_LIMITED", "status": 503}')
    manifests = {scenario: {"busy.json": {"operation": "GET /x", "status": 503}}}
    problems = ve.check_backpressure(manifests, spec)
    assert "GET /x 429: no Retry-After header" in problems
    assert "GET /x: no 503 response" in problems
    assert any(p.endswith("code is not TEMPORARILY_UNAVAILABLE") for p in problems)
    assert any(p.endswith("no retry_after_s") for p in problems)


def test_error_retry_after_s_is_a_non_negative_integer():
    error = {"title": "Busy", "status": 503, "code": "TEMPORARILY_UNAVAILABLE"}
    assert not errors("error.yaml", error | {"retry_after_s": 30})
    assert errors("error.yaml", error | {"retry_after_s": -1})
    assert errors("error.yaml", error | {"retry_after_s": "30"})
