"""Post-process the Redocly bundle so in-document refs resolve.

Redocly inlines each profile schema into components.schemas but keeps its `$id`.
Under JSON Schema 2020-12 a `$id` starts a new resource, so a
`#/components/schemas/...` ref inside it resolves against that `$id` instead of the
OpenAPI document, and tools (Prism, Spectral) cannot follow it. The bundle relies on
the document's `jsonSchemaDialect`, so `$id` and `$schema` are dropped from every
component schema.

Schema-level `examples` are dropped too: Prism prefers them over the operation
examples taken from examples/, and examples/ is what the mock must serve.

Source schemas keep all three; validate_examples.py checks them there.

Usage: uv run scripts/finalize_bundle.py dist/openapi.yaml
"""

import sys
from pathlib import Path

import yaml


def finalize(spec: dict) -> dict:
    for schema in spec.get("components", {}).get("schemas", {}).values():
        schema.pop("$id", None)
        schema.pop("$schema", None)
        schema.pop("examples", None)
    return spec


def main(path: str) -> None:
    p = Path(path)
    spec = finalize(yaml.safe_load(p.read_text()))
    p.write_text(yaml.safe_dump(spec, sort_keys=False, allow_unicode=True, width=100))


if __name__ == "__main__":
    main(sys.argv[1])
