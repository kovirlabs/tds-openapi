"""Settlement split (SPEC.md §10.8): schema, cross-field rules and the examples' fit with the scenarios."""

import copy

import pytest

from tds_conform.data import schema_errors
from tds_conform.examples import load_example, load_manifest
from tds_conform.settlement import settlement_problems

SCENARIOS = list(load_manifest("settlement-split")["files"])


def split() -> dict:
    return copy.deepcopy(load_example("settlement-split", "missed-connection-reprotect.json"))


@pytest.mark.parametrize("name", SCENARIOS)
def test_examples_are_valid_and_add_up(name):
    body = load_example("settlement-split", name)
    assert schema_errors(body, "transfer/settlement-split") == []
    assert settlement_problems(body) == []


@pytest.mark.parametrize("name", SCENARIOS)
def test_example_lines_match_the_generated_journey(name):
    """Every line names a trip of that scenario and carries its final status."""
    scenario = name.removesuffix(".json")
    final = {}
    for event in load_example(scenario, "events.json"):
        if event["event_type"] == "TRIP_STATUS_CHANGED":
            final[(event["provider_id"], event["trip_id"])] = event["payload"]["status"]
    body = load_example("settlement-split", name)
    assert {(line["leg"]["provider_id"], line["leg"]["trip_id"]) for line in body["legs"]} == set(final)
    for line in body["legs"]:
        assert line["trip_status"] == final[(line["leg"]["provider_id"], line["leg"]["trip_id"])]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (lambda s: s["legs"][0]["cost"].update(currency="CAD"), "cost in CAD"),
        (lambda s: s["total"].update(amount="44.49"), "not the sum"),
        (lambda s: s["legs"].append(copy.deepcopy(s["legs"][0])), "listed twice"),
        (lambda s: s["legs"][0].update(trip_status="IN_PROGRESS"), "not terminal"),
        (lambda s: s["legs"][0].update(cancellation_reason="OTHER"), "cancellation_reason on a COMPLETED"),
    ],
)
def test_cross_field_rules(change, problem):
    body = split()
    change(body)
    assert any(problem in p for p in settlement_problems(body))


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s["legs"][0].pop("funding_source"),
        lambda s: s["legs"][0].update(funding_source="INSURANCE"),
        lambda s: s["legs"][0].update(cost={"amount": "-1.00", "currency": "USD"}),
        lambda s: s.update(legs=[]),
    ],
)
def test_schema_refuses(change):
    body = split()
    change(body)
    assert schema_errors(body, "transfer/settlement-split")
