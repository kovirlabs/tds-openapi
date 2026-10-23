"""The public accessors other projects use to find examples and schemas (tds_conform.examples)."""

import json
import shutil
import tomllib
from pathlib import Path

import pytest
import yaml

import tds_conform.data as data
from tds_conform import __version__
from tds_conform.examples import (
    binding_version,
    examples_dir,
    iter_examples,
    load_example,
    load_manifest,
    profiles_dir,
    scenarios,
    schema_errors,
)

REPO = Path(__file__).resolve().parents[2]


def test_dirs_resolve_to_the_repo_checkout():
    assert examples_dir() == REPO / "examples"
    assert profiles_dir() == REPO / "profiles"
    assert (profiles_dir() / "core" / "trip.yaml").is_file()


def test_scenarios_lists_every_manifest_folder():
    names = scenarios()
    assert {"single-trip", "two-provider-transfer", "late-first-leg-hold", "missed-connection-reprotect"} <= set(names)
    assert names == sorted(p.parent.name for p in (REPO / "examples").glob("*/manifest.yaml"))


@pytest.mark.parametrize("scenario", scenarios())
def test_manifest_entries_are_dicts_and_files_load(scenario):
    manifest = load_manifest(scenario)
    assert manifest["description"]
    assert manifest["files"]
    for name, entry, body in iter_examples(scenario):
        assert isinstance(entry, dict)
        assert "schema" in entry or entry.get("kind") in ("event-stream", "webhook-delivery")
        assert body == json.loads((REPO / "examples" / scenario / name).read_text())
        assert load_example(scenario, name) == body


def test_api_payloads_validate_against_their_schema():
    for name, entry, body in iter_examples("missed-connection-reprotect"):
        if "schema" in entry:
            profile, schema = entry["schema"].removeprefix(data.ID_BASE).removesuffix(".yaml").split("/")
            assert schema_errors(body, schema, profile) == [], name


def test_unknown_scenario_and_unlisted_file_are_refused():
    with pytest.raises(FileNotFoundError, match="known: "):
        load_manifest("no-such-scenario")
    with pytest.raises(FileNotFoundError):
        load_manifest("../profiles")
    with pytest.raises(FileNotFoundError, match="not listed"):
        load_example("health", "../../pyproject.toml")


def test_binding_version_matches_every_version_declaration():
    versions = {
        "tds_conform.__version__": __version__,
        "conformance/pyproject.toml": tomllib.loads((REPO / "conformance/pyproject.toml").read_text())["project"][
            "version"
        ],
        "pyproject.toml": tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"],
        "package.json": json.loads((REPO / "package.json").read_text())["version"],
        "spec/openapi.yaml": yaml.safe_load((REPO / "spec/openapi.yaml").read_text())["info"]["version"],
        "spec/asyncapi.yaml": yaml.safe_load((REPO / "spec/asyncapi.yaml").read_text())["info"]["version"],
    }
    assert set(versions.values()) == {binding_version()}, versions


def test_wheel_layout_is_preferred_over_the_repo(tmp_path, monkeypatch):
    """A wheel carries profiles/ and examples/ under tds_conform/_data; that copy wins."""
    packaged = tmp_path / "_data"
    shutil.copytree(REPO / "profiles", packaged / "profiles")
    shutil.copytree(REPO / "examples" / "health", packaged / "examples" / "health")
    monkeypatch.setattr(data, "_PACKAGED", packaged)
    assert examples_dir() == packaged / "examples"
    assert profiles_dir() == packaged / "profiles"
    assert scenarios() == ["health"]
    assert load_example("health", "direct.json")["operated_via"] == "DIRECT"


def test_missing_data_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "_PACKAGED", tmp_path / "nothing")
    monkeypatch.setattr(data, "_REPO", tmp_path / "nothing")
    with pytest.raises(FileNotFoundError, match="profiles/ not found"):
        examples_dir()


def test_mock_public_names():
    from tds_conform.mock import Network, Provider, ProviderConfig, make_server

    provider = Provider(ProviderConfig(provider_id="p1"))
    assert Network.of(provider) and callable(make_server)
