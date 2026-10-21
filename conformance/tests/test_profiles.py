"""Draft paratransit and NEMT profiles (SPEC.md §11): schemas, rules, privacy guards and the mock."""

import copy
from collections.abc import Iterator

import pytest
import yaml

from tds_conform.data import schema_errors
from tds_conform.examples import load_example, profiles_dir
from tds_conform.mock import Provider, ProviderConfig
from tds_conform.profiles import profile_problems

REQ = "throughride-demo"
EXAMPLES = {"paratransit": "paratransit-trip", "nemt": "nemt-trip"}


def request(profile: str) -> dict:
    return copy.deepcopy(load_example(EXAMPLES[profile], "01-request-trip.request.json"))


@pytest.mark.parametrize("profile", EXAMPLES)
def test_examples_are_valid(profile):
    body = request(profile)
    assert schema_errors(body, f"{profile}/trip-request") == []
    assert schema_errors(body, "core/trip-request") == []  # still a core TripRequest
    assert profile_problems(body) == []


@pytest.mark.parametrize("profile", EXAMPLES)
def test_mock_accepts_the_examples(profile, clock):
    body = request(profile)
    clock.now = clock.now.replace(year=2026, month=11, day=10)
    provider = Provider(ProviderConfig(provider_id=body["provider_id"]), clock=clock)
    status, trip = provider.request_trip(REQ, "k1", body)
    assert status in (201, 202), trip
    assert trip[profile] == body[profile]


def _set(path: str, value):
    def change(body: dict) -> None:
        *parents, last = path.split(".")
        node = body
        for key in parents:
            node = node[int(key)] if key.isdigit() else node[key]
        node[last] = value

    return change


@pytest.mark.parametrize(
    ("profile", "change", "problem"),
    [
        ("paratransit", _set("rider_ref.eligibility", []), "ADA_PARATRANSIT"),
        ("paratransit", _set("legs.0.service_requirements", ["DOOR_TO_DOOR"]), "WHEELCHAIR_LIFT or RAMP"),
        ("paratransit", _set("paratransit.pca_authorized", False), "pca_authorized"),
        ("nemt", _set("legs.0.service_requirements", []), "WHEELCHAIR needs"),
        ("nemt", _set("nemt.level_of_service", "STRETCHER"), "STRETCHER needs"),
        ("nemt", _set("legs.0.service_requirements", ["WHEELCHAIR_LIFT", "STRETCHER"]), "but level_of_service"),
        ("nemt", _set("nemt.authorization.valid_until", "2026-11-11"), "after authorization valid_until"),
        ("nemt", _set("nemt.authorization.valid_from", "2026-11-13"), "before authorization valid_from"),
        ("nemt", _set("fare_funding.authorization_ref", "PA-OTHER"), "differs"),
    ],
)
def test_rules(profile, change, problem, clock):
    body = request(profile)
    change(body)
    assert schema_errors(body, f"{profile}/trip-request") == []
    assert any(problem in p for p in profile_problems(body))
    clock.now = clock.now.replace(year=2026, month=11, day=10)
    status, error = Provider(ProviderConfig(provider_id=body["provider_id"]), clock=clock).request_trip(REQ, "k", body)
    assert status == 400 and problem in error["detail"]


@pytest.mark.parametrize(
    "change",
    [
        _set("nemt.diagnosis", "anything"),
        _set("nemt.authorization.member_id", "123"),
        _set("nemt.authorization.authorization_ref", "dialysis, Tue/Thu"),
        _set("nemt.level_of_service", "AMBULANCE"),
        lambda body: body["nemt"].pop("authorization"),
    ],
)
def test_nemt_object_is_closed_and_free_of_free_text(change):
    body = request("nemt")
    change(body)
    assert schema_errors(body, "nemt/trip-request")


def _string_nodes(node, path: str = "") -> Iterator[tuple[str, dict]]:
    if isinstance(node, dict):
        if node.get("type") == "string":
            yield path, node
        for key, value in node.items():
            if key != "examples":
                yield from _string_nodes(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _string_nodes(value, f"{path}/{i}")


NEMT_SCHEMAS = sorted((profiles_dir() / "nemt").glob("*.yaml"))


@pytest.mark.parametrize("path", NEMT_SCHEMAS, ids=lambda p: p.name)
def test_nemt_schemas_have_no_free_text(path):
    """Privacy by construction: every NEMT string is an enum, a date or a constrained reference."""
    schema = yaml.safe_load(path.read_text())
    loose = [where for where, node in _string_nodes(schema) if not {"enum", "const", "format", "pattern"} & set(node)]
    assert loose == []
    for where, node in _string_nodes(schema):
        if "pattern" in node:
            assert " " not in node["pattern"] and "\\s" not in node["pattern"], where


@pytest.mark.parametrize("path", NEMT_SCHEMAS + sorted((profiles_dir() / "paratransit").glob("*.yaml")), ids=str)
def test_no_health_vocabulary_in_property_names(path):
    banned = ("diagnos", "condition", "icd", "procedure", "medical", "health", "member_id", "medication")
    text = yaml.safe_load(path.read_text())

    def names(node) -> Iterator[str]:
        if isinstance(node, dict):
            for key, value in node.get("properties", {}).items():
                yield key
                yield from names(value)
            for key in ("items", "allOf"):
                if key in node:
                    yield from (
                        names(node[key])
                        if isinstance(node[key], dict)
                        else (n for sub in node[key] for n in names(sub))
                    )

    assert [n for n in names(text) if any(b in n for b in banned)] == []
