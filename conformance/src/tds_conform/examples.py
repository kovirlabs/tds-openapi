"""Public accessors for the binding's published examples and schemas.

This is the stable API for projects that depend on tds-conform, for instance a broker
whose test fakes replay `examples/` or whose tests validate payloads against
`profiles/`. Use it instead of hard-coding the repo layout: it works the same from a
repo checkout, an editable install (``uv add --editable ../tds-openapi/conformance``)
and a wheel (which carries its own copy of `profiles/` and `examples/`).

    from tds_conform.examples import binding_version, examples_dir, load_manifest, load_example

    manifest = load_manifest("late-first-leg-hold")
    for name, entry in manifest["files"].items():
        body = load_example("late-first-leg-hold", name)

Manifest format (``examples/<scenario>/manifest.yaml``): ``description``, optional
``narrative`` and ``generated_by``, and ``files`` mapping each JSON file to an entry with
``schema`` (the schema ``$id``), and for API payloads ``operation`` (e.g. ``POST /tenders``)
and ``status`` (absent for a request body). Event logs have ``kind: event-stream`` and no
``schema``. ``load_manifest`` always returns entries as dicts.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from tds_conform import SPEC_VERSION
from tds_conform.data import data_root, schema_errors

__all__ = [
    "binding_version",
    "examples_dir",
    "iter_examples",
    "load_example",
    "load_manifest",
    "profiles_dir",
    "scenarios",
    "schema_errors",
]


def binding_version() -> str:
    """Version of the tds-openapi binding these examples and schemas belong to (SemVer)."""
    return SPEC_VERSION


def examples_dir() -> Path:
    """Directory holding one folder per example scenario."""
    return data_root() / "examples"


def profiles_dir() -> Path:
    """Directory holding the JSON Schemas, one folder per profile (core/, transfer/, ...)."""
    return data_root() / "profiles"


def scenarios() -> list[str]:
    """Names of the example scenarios (folders with a manifest.yaml), sorted."""
    return sorted(p.name for p in examples_dir().iterdir() if (p / "manifest.yaml").is_file())


def load_manifest(scenario: str) -> dict[str, Any]:
    """The scenario's manifest, with every `files` entry normalised to a dict."""
    manifest: dict[str, Any] = yaml.safe_load((_scenario_dir(scenario) / "manifest.yaml").read_text()) or {}
    files = manifest.get("files") or {}
    manifest["files"] = {name: e if isinstance(e, dict) else {"schema": e} for name, e in files.items()}
    return manifest


def load_example(scenario: str, filename: str) -> Any:
    """Parsed JSON of one example file. The file must be listed in the scenario's manifest."""
    if filename not in load_manifest(scenario)["files"]:
        raise FileNotFoundError(f"{filename!r} is not listed in examples/{scenario}/manifest.yaml")
    return json.loads((_scenario_dir(scenario) / filename).read_text())


def iter_examples(scenario: str) -> Iterator[tuple[str, dict[str, Any], Any]]:
    """(filename, manifest entry, parsed JSON) for every file of a scenario, in manifest order."""
    for name, entry in load_manifest(scenario)["files"].items():
        yield name, entry, json.loads((_scenario_dir(scenario) / name).read_text())


def _scenario_dir(scenario: str) -> Path:
    known = scenarios()
    if scenario not in known:
        raise FileNotFoundError(f"no example scenario {scenario!r}; known: {', '.join(known)}")
    return examples_dir() / scenario
