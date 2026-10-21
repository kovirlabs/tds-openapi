"""Paratransit and NEMT scenarios against the reference provider, and faulty providers each must catch."""

import pytest

from tds_conform.cli import conform_main
from tds_conform.mock.provider import Provider, ProviderConfig
from tds_conform.runner import run_scenario

SCENARIOS = ["paratransit_booking", "paratransit_rules", "nemt_booking", "nemt_rules"]


def failures(result):
    return [(s.label, c.name, c.detail) for s in result.steps for c in s.checks if not c.passed]


@pytest.mark.parametrize("mode", ["sync_provider", "async_provider"])
@pytest.mark.parametrize("scenario_id", SCENARIOS)
def test_reference_provider_passes(request, serve, mode, scenario_id):
    provider = request.getfixturevalue(mode)
    settings, client = serve(provider)
    typical = provider.health()[1].get("typical_response_s", 0)
    result = run_scenario(scenario_id, settings, client, typical)
    assert result.outcome == "PASS", (result.note, failures(result))


class DropsParatransit(Provider):
    """Accepts paratransit trips but forgets the paratransit object."""

    def _create_trip(self, requester, body):
        trip = super()._create_trip(requester, body)
        trip.body.pop("paratransit", None)
        return trip


class AddsToNemt(Provider):
    """Stores something extra in the nemt object (data the requester never sent)."""

    def _create_trip(self, requester, body):
        trip = super()._create_trip(requester, body)
        if "nemt" in trip.body:
            trip.body["nemt"]["visit_type"] = "FOLLOW_UP"
        return trip


class NoProfileRules(Provider):
    """Takes any paratransit or NEMT object without checking it."""

    def _check_profiles(self, body):
        return None


@pytest.mark.parametrize(
    ("provider", "scenario_id", "failing_check"),
    [
        (DropsParatransit(), "paratransit_booking", "stored trip keeps the paratransit object as sent"),
        (NoProfileRules(), "paratransit_rules", "status is 400"),
        (AddsToNemt(), "nemt_booking", "stored trip keeps the nemt object as sent"),
        (NoProfileRules(), "nemt_rules", "status is 400"),
        (Provider(ProviderConfig(profiles=("CORE",))), "paratransit_booking", "/health lists PARATRANSIT"),
        (Provider(ProviderConfig(profiles=("CORE", "PARATRANSIT"))), "nemt_rules", "/health lists NEMT"),
    ],
)
def test_kit_catches_faults(serve, provider, scenario_id, failing_check):
    settings, client = serve(provider)
    result = run_scenario(scenario_id, settings, client, 0)
    assert result.outcome == "FAIL", result.note
    assert failing_check in {c.name for s in result.steps for c in s.checks if not c.passed}


def test_refused_profile_trip_is_inconclusive(serve):
    settings, client = serve(Provider(ProviderConfig(unsupported_requirements=frozenset({"WHEELCHAIR_LIFT"}))))
    assert run_scenario("nemt_booking", settings, client, 0).outcome == "INCONCLUSIVE"


@pytest.mark.parametrize("level", ["paratransit", "nemt"])
def test_cli_levels(serve, sync_provider, tmp_path, capsys, level):
    settings, _ = serve(sync_provider)
    code = conform_main(["run", "--url", settings.url, "--profile", level, "--report-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert code == 0, out
    assert f"CONFORMANT ({level})" in out and f"{level}_booking" in out and "book_confirm_cancel" in out
