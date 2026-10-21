"""Validate every schema and example in the repo.

Checks:
  1. Each schema under profiles/ is valid JSON Schema 2020-12 and its $id matches its path.
  2. Repo conventions: $id/title/description/examples present, every property
     described, snake_case property names, UPPER_SNAKE_CASE enum values, and
     `x-profile: <profile>` on every non-core schema.
  3. Each schema's embedded `examples` validate against it.
  4. Each examples/<scenario>/*.json is listed in that scenario's manifest.yaml
     and validates against the schema the manifest names. An entry with
     `kind: event-stream` is a JSON array of events: each validates against the
     schema spec/asyncapi.yaml gives its event_type, and the stream follows the
     milestone order and custody rules (SPEC.md §10.3, §10.7). Tender requests
     also follow the interchange rules (auto_hold_s ≤ max_hold_s; HAND_TO_HAND on a
     PROTECTED interchange needs supervision_required, SPEC.md §10.1); settlement
     splits add up in one currency (SPEC.md §10.8).
  5. Manifest entries that name an `operation` are wired into spec/openapi.yaml at
     that operation and status (so the mock serves them), the operation declares
     the same schema, and every example the spec references has such an entry.
     Entries that name an `event` use the schema spec/asyncapi.yaml declares for
     that message.
  6. Every profiles/core property appears in mappings/tds-2020-crosswalk.md or
     is marked `x-tds-extension: true`.

Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ROOT / "profiles"
EXAMPLES = ROOT / "examples"
OPENAPI = ROOT / "spec" / "openapi.yaml"
ASYNCAPI = ROOT / "spec" / "asyncapi.yaml"
HTTP_METHODS = ("get", "put", "post", "patch", "delete")
CROSSWALK = ROOT / "mappings" / "tds-2020-crosswalk.md"
ID_BASE = "https://tds-openapi.org/schemas/"

SNAKE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
UPPER_SNAKE = re.compile(r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")
# Crosswalk rows start with a backticked `<schema>.<field.path>` key.
CROSSWALK_KEY = re.compile(r"^\|\s*`([a-z0-9-]+(?:\.[a-z0-9_]+)+)`\s*\|", re.MULTILINE)


def expected_id(path: Path) -> str:
    return ID_BASE + path.relative_to(PROFILES).as_posix()


def load_schemas(root: Path = PROFILES) -> dict[Path, dict]:
    return {p: yaml.safe_load(p.read_text()) for p in sorted(root.rglob("*.yaml"))}


def build_registry(schemas: dict[Path, dict]) -> Registry:
    resources = [(s["$id"], Resource.from_contents(s, DRAFT202012)) for s in schemas.values() if "$id" in s]
    return Registry().with_resources(resources)


def validator_for(schema: dict, registry: Registry) -> Draft202012Validator:
    return Draft202012Validator(schema, registry=registry, format_checker=Draft202012Validator.FORMAT_CHECKER)


def iter_properties(schema: dict, prefix: str = "") -> Iterator[tuple[str, dict]]:
    """Yield (dotted_path, subschema) for declared properties, recursing into inline objects and arrays."""
    for name, sub in schema.get("properties", {}).items():
        path = f"{prefix}{name}"
        yield path, sub
        inner = sub.get("items", sub)
        if "properties" in inner:
            yield from iter_properties(inner, f"{path}.")


def iter_enums(schema: object) -> Iterator[list]:
    if isinstance(schema, dict):
        if isinstance(schema.get("enum"), list):
            yield schema["enum"]
        for key, value in schema.items():
            if key not in ("examples", "default", "const"):
                yield from iter_enums(value)
    elif isinstance(schema, list):
        for item in schema:
            yield from iter_enums(item)


def check_conventions(path: Path, schema: dict) -> list[str]:
    errors = []
    for key in ("$id", "title", "description", "examples"):
        if key not in schema:
            errors.append(f"missing top-level `{key}`")
    if schema.get("$id") not in (None, expected_id(path)):
        errors.append(f"$id {schema['$id']!r} should be {expected_id(path)!r}")
    profile = path.relative_to(PROFILES).parts[0] if PROFILES in path.parents else "core"
    if profile != "core" and schema.get("x-profile") != profile:
        errors.append(f"missing `x-profile: {profile}`")
    for prop, sub in iter_properties(schema):
        if not SNAKE.match(prop.rsplit(".", 1)[-1]):
            errors.append(f"property `{prop}` is not snake_case")
        if "description" not in sub:
            errors.append(f"property `{prop}` has no description")
    for enum in iter_enums(schema):
        for value in enum:
            if isinstance(value, str) and not UPPER_SNAKE.match(value):
                errors.append(f"enum value {value!r} is not UPPER_SNAKE_CASE")
    return errors


def check_schema(path: Path, schema: dict, registry: Registry) -> list[str]:
    meta = Draft202012Validator(Draft202012Validator.META_SCHEMA)
    errors = [f"metaschema: {e.message}" for e in meta.iter_errors(schema)]
    errors += check_conventions(path, schema)
    if not errors:
        v = validator_for(schema, registry)
        for i, example in enumerate(schema.get("examples", [])):
            errors += [f"examples[{i}] {format_error(e)}" for e in v.iter_errors(example)]
    return errors


def check_crosswalk(schemas: dict[Path, dict], crosswalk: Path = CROSSWALK) -> list[str]:
    mapped = set(CROSSWALK_KEY.findall(crosswalk.read_text())) if crosswalk.exists() else set()
    errors = []
    for path, schema in schemas.items():
        if path.parent.name != "core":
            continue
        for prop, sub in iter_properties(schema):
            key = f"{path.stem}.{prop}"
            if key not in mapped and not sub.get("x-tds-extension"):
                errors.append(f"`{key}` is not in the crosswalk and not marked x-tds-extension")
    return errors


def load_manifests(examples_root: Path = EXAMPLES) -> dict[Path, dict]:
    """Map each scenario directory to its manifest `files` entries, normalised to dicts with a `schema` key."""
    manifests = {}
    for scenario in sorted(p for p in examples_root.iterdir() if p.is_dir()) if examples_root.exists() else []:
        manifest_path = scenario / "manifest.yaml"
        files = yaml.safe_load(manifest_path.read_text()).get("files", {}) if manifest_path.exists() else {}
        manifests[scenario] = {name: e if isinstance(e, dict) else {"schema": e} for name, e in (files or {}).items()}
    return manifests


def check_event_stream(events, registry: Registry, wiring: dict[str, str]) -> list[str]:
    if not isinstance(events, list):
        return ["an event stream must be a JSON array"]
    errors = []
    for i, event in enumerate(events):
        schema_ref = wiring.get(event.get("event_type") if isinstance(event, dict) else None)
        if schema_ref is None:
            errors.append(f"[{i}] unknown event_type")
            continue
        validator = validator_for(registry.contents(schema_ref), registry)
        errors += [f"[{i}] {event['event_type']} {format_error(e)}" for e in validator.iter_errors(event)]
    if errors:
        return errors
    from tds_conform.events import custody_problems, milestone_problems

    supervised = {(e["provider_id"], e["trip_id"], e["leg_id"]) for e in events if e["event_type"] == "CUSTODY_HANDOFF"}
    return milestone_problems(events) + custody_problems(events, supervised)


def check_examples(registry: Registry, examples_root: Path = EXAMPLES, events: dict | None = None):
    results: dict[Path, list[str]] = {}
    for scenario, files in load_manifests(examples_root).items():
        manifest_path = scenario / "manifest.yaml"
        for json_path in sorted(scenario.glob("*.json")):
            if json_path.name not in files:
                results[json_path] = [f"not listed in {manifest_path.relative_to(examples_root.parent)}"]
        for name, entry in files.items():
            json_path = scenario / name
            if not json_path.exists():
                results[json_path] = ["listed in manifest but missing"]
                continue
            if entry.get("kind") == "event-stream":
                stream = json.loads(json_path.read_text())
                results[json_path] = check_event_stream(stream, registry, events or event_wiring())
                continue
            schema_id = entry["schema"]
            try:
                schema = registry.contents(schema_id)
            except Exception:
                results[json_path] = [f"unknown schema {schema_id!r}"]
                continue
            instance = json.loads(json_path.read_text())
            results[json_path] = [format_error(e) for e in validator_for(schema, registry).iter_errors(instance)]
            if not results[json_path] and schema_id in RULES:
                results[json_path] = RULES[schema_id](instance)
    return results


def interchange_problems(interchange: dict, legs: list[dict]) -> list[str]:
    """Interchange rules JSON Schema can't express (SPEC.md §10.1)."""
    errors = []
    if interchange.get("auto_hold_s", 0) > interchange.get("max_hold_s", 0):
        errors.append("interchange: auto_hold_s exceeds max_hold_s")
    hand_to_hand = any("HAND_TO_HAND" in leg.get("service_requirements", []) for leg in legs)
    if hand_to_hand and interchange["protection"] == "PROTECTED" and not interchange.get("supervision_required"):
        errors.append("interchange: HAND_TO_HAND leg on a PROTECTED interchange needs supervision_required: true")
    return errors


RULES = {
    f"{ID_BASE}transfer/tender-request.yaml": lambda t: interchange_problems(t["interchange"], t["trip"]["legs"]),
    f"{ID_BASE}transfer/interchange.yaml": lambda ix: interchange_problems(ix, []),
    f"{ID_BASE}transfer/settlement-split.yaml": lambda split: _settlement_problems(split),
}


def _settlement_problems(split: dict) -> list[str]:
    from tds_conform.settlement import settlement_problems

    return settlement_problems(split)


def _resolve(node, base: Path):
    """Inline $refs to YAML documents; turn profile-schema refs and example externalValues into resolved Paths."""
    if isinstance(node, list):
        return [_resolve(n, base) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target, _, pointer = node["$ref"].partition("#")
        path = (base.parent / target).resolve() if target else base
        if PROFILES in path.parents:
            return path
        doc = yaml.safe_load(path.read_text())
        for part in filter(None, pointer.split("/")):
            doc = doc[part]
        return _resolve(doc, path)
    if isinstance(node.get("externalValue"), str):
        node = node | {"externalValue": (base.parent / node["externalValue"]).resolve()}
    return {k: _resolve(v, base) for k, v in node.items()}


def spec_wiring(openapi: Path = OPENAPI) -> dict[tuple[str, str], dict]:
    """Map (operation, slot) to {"schema": $id or None, "examples": {Path}}; slot is "request" or a status code."""
    spec = _resolve(yaml.safe_load(openapi.read_text()), openapi)
    wiring = {}
    for route, item in spec.get("paths", {}).items():
        for method in HTTP_METHODS:
            if method not in item:
                continue
            op = item[method]
            slots = {"request": op.get("requestBody", {})} | {str(k): v for k, v in op.get("responses", {}).items()}
            for slot, body in slots.items():
                for media in body.get("content", {}).values():
                    schema = media.get("schema")
                    examples = {e["externalValue"] for e in media.get("examples", {}).values() if "externalValue" in e}
                    wiring[(f"{method.upper()} {route}", slot)] = {
                        "schema": expected_id(schema) if isinstance(schema, Path) else None,
                        "examples": examples,
                    }
    return wiring


def event_wiring(asyncapi: Path = ASYNCAPI) -> dict[str, str]:
    """Map each AsyncAPI message name (the event_type) to its payload schema $id."""
    spec = yaml.safe_load(asyncapi.read_text())
    wiring = {}
    for message in spec["components"]["messages"].values():
        path = (asyncapi.parent / message["payload"]["schema"]["$ref"]).resolve()
        wiring[message["name"]] = expected_id(path)
    return wiring


def check_events(manifests: dict[Path, dict], wiring: dict[str, str]) -> dict[Path, list[str]]:
    results: dict[Path, list[str]] = {}
    for scenario, files in manifests.items():
        for name, entry in files.items():
            if "event" not in entry:
                continue
            path = (scenario / name).resolve()
            declared = wiring.get(entry["event"])
            if declared is None:
                results[path] = [f"spec/asyncapi.yaml has no {entry['event']} message"]
            elif declared != entry["schema"]:
                results[path] = [f"{entry['event']} is declared as {declared}, manifest says {entry['schema']}"]
            elif json.loads(path.read_text()).get("event_type") != entry["event"]:
                results[path] = [f"event_type is not {entry['event']}"]
    return results


def check_wiring(manifests: dict[Path, dict], wiring: dict[tuple[str, str], dict]) -> dict[Path, list[str]]:
    results: dict[Path, list[str]] = {}
    claimed = set()
    for scenario, files in manifests.items():
        for name, entry in files.items():
            if "operation" not in entry:
                continue
            path = (scenario / name).resolve()
            key = (entry["operation"], str(entry.get("status", "request")))
            claimed.add((key, path))
            slot = wiring.get(key)
            if slot is None:
                results[path] = [f"spec has no {key[0]} {key[1]}"]
            elif path not in slot["examples"]:
                results[path] = [f"not referenced as an example of {key[0]} {key[1]}"]
            elif slot["schema"] != entry["schema"]:
                results[path] = [f"{key[0]} {key[1]} declares {slot['schema']}, manifest says {entry['schema']}"]
    for key, slot in wiring.items():
        for path in slot["examples"] - {p for k, p in claimed if k == key}:
            results.setdefault(path, []).append(f"used by {key[0]} {key[1]} but no manifest entry says so")
    return results


def format_error(error) -> str:
    where = "/".join(str(p) for p in error.absolute_path) or "(root)"
    return f"at {where}: {error.message}"


def main() -> int:
    schemas = load_schemas()
    registry = build_registry(schemas)
    failures = 0

    def report(label: str, errors: list[str]) -> None:
        nonlocal failures
        if errors:
            failures += len(errors)
            print(f"FAIL {label}")
            for e in errors:
                print(f"     {e}")
        else:
            print(f"ok   {label}")

    for path, schema in schemas.items():
        report(path.relative_to(ROOT).as_posix(), check_schema(path, schema, registry))
    for path, errors in check_examples(registry).items():
        report(path.relative_to(ROOT).as_posix(), errors)
    manifests = load_manifests()
    for path, errors in check_wiring(manifests, spec_wiring()).items():
        report(path.relative_to(ROOT).as_posix() + " (spec wiring)", errors)
    for path, errors in check_events(manifests, event_wiring()).items():
        report(path.relative_to(ROOT).as_posix() + " (event wiring)", errors)
    report(CROSSWALK.relative_to(ROOT).as_posix() + " coverage", check_crosswalk(schemas))

    print(f"\n{failures} problem(s)" if failures else "\nall checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
