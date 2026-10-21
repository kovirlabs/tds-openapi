"""Locate the published schemas and examples and validate payloads against them."""

import json
from functools import cache
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ID_BASE = "https://tds-openapi.org/schemas/"
_PACKAGED = Path(__file__).parent / "_data"
_REPO = Path(__file__).resolve().parents[3]


def data_root() -> Path:
    """Directory holding profiles/ and examples/: the wheel's copy, else the repo checkout."""
    for root in (_PACKAGED, _REPO):
        if (root / "profiles").is_dir():
            return root
    raise FileNotFoundError("profiles/ not found; reinstall tds-conform or run from a repo checkout")


def schema_id(name: str, profile: str = "core") -> str:
    return f"{ID_BASE}{profile}/{name}.yaml"


@cache
def registry() -> Registry:
    resources = []
    for path in sorted((data_root() / "profiles").rglob("*.yaml")):
        contents = yaml.safe_load(path.read_text())
        resources.append((contents["$id"], Resource.from_contents(contents, DRAFT202012)))
    return Registry().with_resources(resources)


def schema_errors(instance, name: str, profile: str = "core") -> list[str]:
    """Validation messages for `instance` against a profile schema; empty when valid.

    `name` may carry its profile ("transfer/tender"), overriding `profile`.
    """
    if "/" in name:
        profile, name = name.split("/", 1)
    reg = registry()
    validator = Draft202012Validator(
        reg.contents(schema_id(name, profile)), registry=reg, format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}" for e in validator.iter_errors(instance)]


def example(scenario: str, filename: str):
    return json.loads((data_root() / "examples" / scenario / filename).read_text())
