"""Property-based fuzzing of a provider endpoint against the OpenAPI document (schemathesis).

Complements the scenarios: they check behaviour step by step, fuzzing throws many generated
requests at every operation and checks the answers stay inside the contract (no 5xx,
documented status codes and media types, response bodies and headers matching their
schemas; in negative mode, invalid requests rejected). It does not decide a conformance
level. Needs the `fuzz` extra (`schemathesis`) and the bundled spec (`npm run bundle` →
dist/openapi.yaml).

    tds-conform fuzz --url http://localhost:4010 [--spec dist/openapi.yaml] [--max-examples 25]
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from tds_conform.data import data_root

# Delivers webhooks to whatever callback_url is generated, so it is opt-in.
EXCLUDED_BY_DEFAULT = {"POST /subscriptions"}
MODES = ("positive", "negative")


@dataclass
class FuzzResult:
    operation: str
    mode: str
    status: str  # PASS | FAIL | ERROR | SKIPPED
    detail: str = ""


def default_spec() -> Path:
    return data_root() / "dist" / "openapi.yaml"


def fuzz(
    url: str,
    token: str,
    requester_id: str,
    provider_id: str,
    *,
    spec: Path | None = None,
    max_examples: int = 25,
    modes: tuple[str, ...] = MODES,
    include: set[str] | None = None,
    exclude: set[str] = EXCLUDED_BY_DEFAULT,
    seed: int | None = 0,
) -> list[FuzzResult]:
    """Fuzz every operation (or those in `include`) and return one result per operation and mode."""
    try:
        import schemathesis
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise RuntimeError("fuzzing needs the extra: install tds-conform[fuzz]") from exc

    spec = spec or default_spec()
    if not spec.is_file():
        raise FileNotFoundError(f"{spec} not found; run `npm run bundle` in tds-openapi or pass --spec")
    schema = schemathesis.openapi.from_path(str(spec))
    headers = {"Authorization": f"Bearer {token}"}
    results = []
    for entry in schema.get_all_operations():
        operation = entry.ok()
        label = operation.label
        if include and label not in include:
            continue
        if not include and label in exclude:
            results += [FuzzResult(label, mode, "SKIPPED", "excluded by default") for mode in modes]
            continue
        for mode in modes:
            results.append(_fuzz_one(operation, mode, url, headers, requester_id, provider_id, max_examples, seed))
    if include and (unknown := include - {r.operation for r in results}):
        raise ValueError(f"no such operation(s) in {spec.name}: {', '.join(sorted(unknown))}")
    return results


def _fuzz_one(operation, mode, url, headers, requester_id, provider_id, max_examples, seed) -> FuzzResult:
    import schemathesis
    from hypothesis import HealthCheck, Phase, given, settings
    from hypothesis import seed as hypothesis_seed
    from hypothesis.errors import Unsatisfiable
    from schemathesis import checks

    run_checks = [
        checks.not_a_server_error,
        checks.status_code_conformance,
        checks.content_type_conformance,
        checks.response_headers_conformance,
        checks.response_schema_conformance,
    ]
    if mode == "negative":
        run_checks.append(checks.negative_data_rejection)
    positive = mode == "positive"

    @settings(
        max_examples=max_examples,
        deadline=None,
        database=None,
        phases=[Phase.generate, Phase.shrink],
        suppress_health_check=list(HealthCheck),
    )
    @given(case=operation.as_strategy(generation_mode=schemathesis.GenerationMode(mode)))
    def run(case) -> None:
        if positive:
            _own(case.body, requester_id, provider_id)
        response = case.call(base_url=url, headers=headers, timeout=10)
        case.validate_response(response, checks=run_checks)

    if seed is not None:
        run = hypothesis_seed(seed)(run)
    try:
        run()
    except Unsatisfiable:  # negative mode on an operation with nothing to make invalid
        return FuzzResult(operation.label, mode, "SKIPPED", "no invalid input can be generated")
    except (checks.Failure, checks.FailureGroup, AssertionError) as exc:  # a failing check, after shrinking
        return FuzzResult(operation.label, mode, "FAIL", _summary(exc))
    except Exception as exc:  # network trouble or a schema the generator can't handle
        return FuzzResult(operation.label, mode, "ERROR", f"{type(exc).__name__}: {_summary(exc)}")
    return FuzzResult(operation.label, mode, "PASS")


def write_report(results: list[FuzzResult], directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "fuzz.json"
    path.write_text(json.dumps([asdict(r) for r in results], indent=2) + "\n")
    return path


def _own(body: Any, requester_id: str, provider_id: str) -> None:
    """Make generated bodies speak for the configured requester and provider, so they get past
    the identity checks and exercise the provider's deeper validation."""
    for node in (body, body.get("trip") if isinstance(body, dict) else None):
        if isinstance(node, dict):
            if "requester_id" in node:
                node["requester_id"] = requester_id
            if "provider_id" in node:
                node["provider_id"] = provider_id


def _summary(exc: BaseException, limit: int = 2000) -> str:
    text = str(exc).strip() or type(exc).__name__
    return text if len(text) <= limit else text[:limit] + " …"
