"""Eligibility attestations (SPEC.md §6.5.1): kit rules, the reference provider's policy, the kit scenario."""

import copy
from datetime import timedelta

import pytest

import tds_conform.scenarios  # noqa: F401  (registers the scenarios)
from tds_conform.data import example, schema_errors
from tds_conform.eligibility import age_code_problems, eligibility_problems, fact_code_problems
from tds_conform.mock.provider import Provider, ProviderConfig
from tds_conform.runner import run_scenario

REQ = "throughride-demo"
DIGITAL_ID = {
    "program": "AGE_60_PLUS",
    "attested_by": REQ,
    "verification_method": "DIGITAL_ID",
    "verified_at": "2026-10-15",
    "verifier_ref": "state-mdl-issuer",
}


def trip_with(*attestations: dict) -> dict:
    trip = example("single-trip", "01-request-trip.request.json")
    trip["rider_ref"]["eligibility"] = [copy.deepcopy(a) for a in attestations]
    return trip


def test_published_example_validates():
    trip = example("eligibility-attestation", "01-request-trip.request.json")
    assert schema_errors(trip, "trip-request") == []
    attestation = trip["rider_ref"]["eligibility"][0]
    assert (attestation["program"], attestation["verification_method"]) == ("AGE_60_PLUS", "DIGITAL_ID")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"verification_method": "BIOMETRIC"}, "is not one of"),
        ({"verified_at": "15/10/2026"}, "is not a 'date'"),
        ({"verifier_ref": "x" * 201}, "is too long"),
    ],
)
def test_schema_refuses_bad_verification_fields(change, message):
    errors = schema_errors(DIGITAL_ID | change, "eligibility")
    assert any(message in e for e in errors), errors


def test_verified_after_valid_until_is_a_problem():
    assert eligibility_problems([DIGITAL_ID | {"valid_until": "2027-10-15"}]) == []
    assert eligibility_problems([DIGITAL_ID | {"valid_until": "2026-10-14"}]) == [
        "rider_ref.eligibility/0: verified_at 2026-10-15 is after valid_until 2026-10-14"
    ]


@pytest.mark.parametrize(
    ("program", "ok"),
    [
        ("AGE_60_PLUS", True),
        ("AGE_65_PLUS", True),
        ("AGE_UNDER_18", True),
        ("SENIOR_5310", True),
        ("AGE_SENIOR", False),
        ("AGE_60", False),
        ("AGE_UNDER_018", False),
    ],
)
def test_age_code_convention(program, ok):
    assert (age_code_problems([{"program": program, "attested_by": REQ}]) == []) is ok


@pytest.mark.parametrize(
    ("program", "ok"),
    [
        ("DISABILITY", True),
        ("LOW_INCOME", True),
        ("RESIDENT_US_26161", True),
        ("RESIDENT_CA_3506", True),
        ("ADA_PARATRANSIT", True),
        ("MEDICAID", True),
        ("AGE_65_PLUS", True),
        ("DISABILITY_VISUAL", False),
        ("DISABILITY_YES", False),
        ("LOW_INCOME_200_FPL", False),
        ("RESIDENT_US_161", False),
        ("RESIDENT_US_MI_161", False),
        ("RESIDENT_US_WASHTENAW", False),
        ("RESIDENT_WASHTENAW", False),
        ("AGE_SENIOR", False),
    ],
)
def test_fact_code_conventions(program, ok):
    assert (fact_code_problems([{"program": program, "attested_by": REQ}]) == []) is ok


def test_fact_code_problem_says_what_never_why():
    [message] = fact_code_problems([{"program": "DISABILITY_COGNITIVE", "attested_by": REQ}])
    assert "never a condition" in message


def test_rider_category_example_attests_facts_only():
    trip = example("rider-category-facts", "01-request-trip.request.json")
    assert schema_errors(trip, "trip-request") == []
    attestations = trip["rider_ref"]["eligibility"]
    assert {a["program"] for a in attestations} == {"AGE_65_PLUS", "DISABILITY", "RESIDENT_US_26161"}
    assert fact_code_problems(attestations) == eligibility_problems(attestations) == []


def test_mock_keeps_fact_attestations_as_sent(clock):
    provider = Provider(ProviderConfig(), clock=clock)
    trip = example("rider-category-facts", "01-request-trip.request.json")
    trip["legs"][0]["pickup_window"] = {
        "earliest": (clock() + timedelta(days=2)).isoformat(),
        "latest": (clock() + timedelta(days=2, minutes=30)).isoformat(),
    }
    status, body = provider.request_trip(REQ, "k-facts", trip)
    assert status == 201, body
    assert body["rider_ref"]["eligibility"] == trip["rider_ref"]["eligibility"]


def test_mock_refuses_verified_after_valid_until(clock):
    provider = Provider(ProviderConfig(), clock=clock)
    status, body = provider.request_trip(REQ, "k1", trip_with(DIGITAL_ID | {"valid_until": "2026-10-01"}))
    assert (status, body["code"]) == (400, "INVALID_REQUEST")


def test_mock_keeps_attestations_as_sent(clock):
    provider = Provider(ProviderConfig(), clock=clock)
    trip = trip_with(DIGITAL_ID)
    status, body = provider.request_trip(REQ, "k1", trip)
    assert status == 201
    assert provider.get_trip(REQ, body["trip_id"])[1]["rider_ref"] == trip["rider_ref"]


@pytest.mark.parametrize(
    ("refused", "attestation"),
    [
        ({"DIGITAL_ID"}, DIGITAL_ID),
        ({"UNSTATED", "SELF_DECLARED"}, {"program": "AGE_60_PLUS", "attested_by": REQ}),
    ],
)
def test_mock_refuses_methods_it_does_not_accept(clock, refused, attestation):
    provider = Provider(ProviderConfig(refused_verification_methods=frozenset(refused)), clock=clock)
    status, body = provider.request_trip(REQ, "k1", trip_with(attestation))
    assert (status, body["code"]) == (422, "ELIGIBILITY_NOT_ACCEPTED")
    assert schema_errors(body, "error") == []
    assert provider.events[-1]["event"]["payload"]["status"] == "REJECTED"


def test_mock_rejects_tender_with_eligibility_reason(clock):
    clock.now = clock.now.replace(year=2026, month=11, day=10, hour=12)
    provider = Provider(
        ProviderConfig(provider_id="oakland-go", refused_verification_methods=frozenset({"DIGITAL_ID"})), clock=clock
    )
    body = example("two-provider-transfer", "07-tender-onward.request.json")
    body["trip"]["rider_ref"]["eligibility"] = [DIGITAL_ID]
    status, tender = provider.create_tender(REQ, "k1", body)
    assert (tender["status"], tender["reason"]) == ("REJECTED", "ELIGIBILITY_NOT_ACCEPTED")


def test_sim_policy_sets_refused_methods():
    provider = Provider(ProviderConfig())
    status, policy = provider.sim_policy({"refused_verification_methods": ["SELF_DECLARED"]})
    assert status == 200 and policy["refused_verification_methods"] == ["SELF_DECLARED"]


# --- the kit scenario -------------------------------------------------------------------


class DropsVerification(Provider):
    """Stores attestations without the fields it does not know."""

    def _create_trip(self, requester, body):
        body = copy.deepcopy(body)
        for a in body.get("rider_ref", {}).get("eligibility", []):
            for key in ("verification_method", "verified_at", "verifier_ref"):
                a.pop(key, None)
        return super()._create_trip(requester, body)


class RefusesUnknownFields(Provider):
    """Treats the new optional fields as an invalid request."""

    def _check_trip_request(self, requester, body):
        if any("verification_method" in a for a in body.get("rider_ref", {}).get("eligibility", [])):
            return 400, {"type": "about:blank", "title": "x", "status": 400, "code": "INVALID_REQUEST"}
        return super()._check_trip_request(requester, body)


class WrongRefusalCode(Provider):
    """Refuses the attestation with a capacity code, so the kit can't tell why."""

    def _check_trip_request(self, requester, body):
        if any("verification_method" in a for a in body.get("rider_ref", {}).get("eligibility", [])):
            return 422, {"type": "about:blank", "title": "x", "status": 422, "code": "NO_CAPACITY"}
        return super()._check_trip_request(requester, body)


def _run(serve, provider):
    settings, client = serve(provider)
    typical = provider.health()[1].get("typical_response_s", 0)
    return run_scenario("eligibility_attestation", settings, client, typical)


@pytest.mark.parametrize("mode", ["sync_provider", "async_provider"])
def test_reference_provider_passes(request, serve, mode):
    result = _run(serve, request.getfixturevalue(mode))
    failures = [(s.label, c.name, c.detail) for s in result.steps for c in s.checks if not c.passed]
    assert result.outcome == "PASS", (result.note, failures)


def test_refusing_with_eligibility_code_passes(serve):
    provider = Provider(ProviderConfig(refused_verification_methods=frozenset({"DIGITAL_ID"})))
    assert _run(serve, provider).outcome == "PASS"


def test_refusing_with_other_code_is_inconclusive(serve):
    assert _run(serve, WrongRefusalCode()).outcome == "INCONCLUSIVE"


@pytest.mark.parametrize(
    ("provider", "failing_check"),
    [
        (DropsVerification(), "rider_ref.eligibility kept as sent"),
        (RefusesUnknownFields(), "attestation fields accepted"),
    ],
)
def test_kit_catches_faults(serve, provider, failing_check):
    result = _run(serve, provider)
    assert result.outcome == "FAIL"
    failed = {c.name for s in result.steps for c in s.checks if not c.passed}
    assert failing_check in failed, failed
