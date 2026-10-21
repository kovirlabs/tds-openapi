# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project is
A machine-readable **binding** of the Transactional Data Specification (TDS, TCRP G-16 / Report 210, 2020) for demand-response transit trip booking:
- OpenAPI 3.1 for request/response operations
- AsyncAPI 3.0 for events (status, milestones, ETAs)
- JSON Schema 2020-12 for data
- A conformance test kit and a mock server

It is **not a new standard**. It makes TDS implementable and testable. ThroughRide (Evan's Django transfer broker) is the reference implementation and lives in a separate repo.

Read `SPEC.md` before designing anything. Read `reference/throughride-session-summary-2026-10-20.md` for background and decisions. Check `docs/notes/throughride-alignment.md` for open spec ↔ ThroughRide mismatches; treat them as proposals, not instructions.

## Owner and constraints
- Maintainer: Evan (Kovir Labs). Solo, after-hours. Favor small, shippable increments over completeness.
- Intended to be donated to a neutral steward (MobilityData or Open Mobility Foundation). Keep it vendor-neutral, no Kovir/ThroughRide-specific fields in `profiles/core`.
- Host: GitHub (public), mirrored to Evan's Forgejo.

## Repo layout
```
spec/            openapi.yaml, paths/ (one file per path), components/ (shared params/responses), asyncapi.yaml
profiles/        core/, transfer/, paratransit/, nemt/
mappings/        tds-2020-crosswalk.md, gtfs-flex.md, gtfs-realtime.md,
                 netex-interchange.md, logistics-patterns.md
examples/        one folder per scenario, request/response/event JSON
conformance/     Python package tds_conform: CLIs tds-conform (kit), tds-mock (reference providers), tds-demo
mock-server/     docker-compose.yml: tds-mock --demo on :4010, Prism (static examples) on :4011
sdks/            GENERATED ONLY
docs/            mkdocs site, for-agencies.md, for-vendors.md, rfcs/
.github/workflows/ lint.yml, bundle.yml, conformance.yml
reference/       background research + proposal (not part of the spec; don't cite in spec text)
```

## Architecture (how the pieces fit)
- **One data model, two transports.** Schemas in `profiles/<name>/` are shared by `openapi.yaml` (requester → provider calls, SPEC §7) and `asyncapi.yaml` (provider → requester webhooks, SPEC §9). Don't define a payload twice.
- **Core vs profiles.** Core = one leg per provider trip, close to TDS 2020. Multi-leg composition, tenders, milestones, ETA bands, holds, custody and settlement split all live in `profiles/transfer` (SPEC §10). Paratransit/NEMT are placeholders.
- **examples/ drives everything downstream.** Each scenario folder is consumed by the mock server (Prism responses), the docs, and the conformance kit. Scenario names in SPEC §13 (`book_confirm_cancel`, `milestone_sequence`, `custody_required`, …) are the conformance test IDs.
- **Schema identity.** Schemas live in `profiles/<profile>/<name>.yaml` with `$id` = `https://tds-openapi.org/schemas/<profile>/<name>.yaml` (validator enforces the match; domain is a placeholder). Path files in `spec/paths/` reference them by relative `$ref`. Rules JSON Schema can't express (window ordering, state transitions) are left to the conformance kit and noted in the schema `description`.
- **Examples are manifest-driven.** Each `examples/<scenario>/manifest.yaml` maps file → `schema` `$id`, plus `operation` (`POST /trips`) and `status` (omit for a request body) for API payloads. Path files reference the same files with `externalValue`; the validator fails if the two disagree. An unlisted JSON file fails validation.
- **Transfer examples are generated.** `examples/{two-provider-transfer,late-first-leg-hold,tender-expired-retender,missed-connection-reprotect,supervised-rider-custody-handoff}/` come from `tds_conform/demo.py` on a fake clock; never hand-edit them. Change the demo or the mock, rerun `tds-demo generate-examples`, then re-wire any renamed files in `spec/paths/`. `conformance/tests/test_demo.py` fails on drift. Each has `events.json` (`kind: event-stream`), validated per event and against the milestone/custody rules.
- **Mock layout.** `mock/provider.py` (core + event emission), `mock/transfer.py` (tenders, holds, milestones, custody, ETA, `/_sim` methods), `mock/network.py` (providers per process, ticker, signed webhook `Dispatcher`), `mock/server.py` (routing: `/p/<id>/…`, `/_sim/…`). Time-based behaviour goes through `tick()` so tests can use a fake clock. Every change must `_emit` an event (SPEC §9).
- **Bundle quirks (don't "fix" them back).** Lint runs on the bundle because Spectral mis-validates `$ref`'d path items in OAS 3.1 sources. `finalize_bundle.py` strips `$id`/`$schema` (2020-12 `$id` makes in-bundle `#/components` refs unresolvable) and schema-level `examples` (Prism would serve them instead of examples/). Don't add a `components.schemas` list; operations reference profile files directly. Use `externalValue`, not `value: {$ref}` (OAS 3.1 treats `value` as literal). Crosswalk rows are parsed by the validator: keep the `` `schema.field` `` key format.
- **State machines are normative.** Trip status (SPEC §8.1), tender states (§10.2) and milestone order (§10.3) are what conformance enforces; keep enums, examples and tests consistent with them.

## Conventions
- **YAML** for spec and schema source; **JSON** for examples.
- Property names: `snake_case` (consistent with GTFS). Enum values: `UPPER_SNAKE_CASE`.
- Times: RFC 3339 with offset. Durations: integer seconds, field suffix `_s`. Coordinates: WGS84, `lat`/`lon`.
- IDs are opaque strings, scoped by issuing party (`provider_id` + `trip_id`).
- Every schema has `$id`, `title`, `description`, and `examples`. Every field has a `description`.
- Every field in `profiles/core` must map to a TDS 2020 element in `mappings/tds-2020-crosswalk.md`, or be marked `x-tds-extension: true` with a reason.
- Profile-specific fields carry `x-profile: transfer` (etc.).
- One schema per file; reference with relative `$ref`.
- Additive changes only within a minor version. Breaking changes need an RFC in `docs/rfcs/`.

## Rules
- **examples/ is the source of truth for behavior.** Docs, mock server and conformance tests all read from it. When you change a schema, update or add examples in the same change, and make sure every example validates.
- **Never hand-edit `sdks/`**; regenerate.
- **Don't copy TCRP report text verbatim** into spec descriptions. Describe and cite (e.g. "Corresponds to TDS 2020 element X"). Reuse terms are unverified.
- **Privacy by default:** rider schemas carry minimum necessary data. No diagnosis or health data in core. Accessibility needs are expressed as service requirements (e.g. `WHEELCHAIR_LIFT`), not conditions.
- Don't invent TDS element names. If unsure what TDS 2020 calls something, add `TODO(crosswalk)` and leave it for Evan to verify against the report.
- Prefer existing standards' vocabulary (GTFS, GTFS-Flex, NeTEx, EDI 214 milestone semantics) over new terms; record the borrowing in `mappings/`.

## Tooling and commands
Node tools are pinned in `package.json`; Python tooling runs through `uv` (root `pyproject.toml`, Python 3.12; system python is 3.9, so don't call `python` directly).
```
npm install
npm run bundle                                  # Redocly → dist/openapi.yaml, then scripts/finalize_bundle.py
npm run lint                                    # bundle, then Spectral on dist/ (line numbers refer to dist/)
npm run mock:static                             # Prism on :4011; Prefer: code=202 / example=<name> picks a response
uv run tds-mock [--demo] [--decision async]     # providers on :4010 at /p/<id>; Authorization: Bearer <requester_id>
uv run tds-conform run --url http://localhost:4010 --profile transfer   # kit → tds-conform-report/report.{json,html}
uv run tds-conform check-events events.json --interchange tender.json  # milestone/custody/heartbeat rules
uv run tds-demo run                             # narrate the transfer scenarios against tds-mock --demo
uv run tds-demo generate-examples               # REGENERATE the five transfer example folders
uv run scripts/sync_crosswalk.py                # add TODO crosswalk rows for new core fields
uv run scripts/validate_examples.py             # schemas, conventions, examples, crosswalk coverage
uv run --group dev pytest                       # tests/ (schemas, validator) + conformance/tests/ (mock, kit)
uv run --group dev pytest tests/test_core_schemas.py::test_rider_contact_requires_consent   # single test
uv run --group dev ruff check . && uv run --group dev ruff format .
```
```
docker compose -f mock-server/docker-compose.yml up   # both mocks in Docker (static one needs npm run bundle first)
```

`ruff` for lint/format, `pytest`. Scenarios are hand-written (`conformance/src/tds_conform/scenarios/`); `schemathesis` fuzzing is planned, not wired in.

**Public API for other projects.** `tds_conform.examples` (`binding_version`, `examples_dir`, `profiles_dir`, `scenarios`, `load_manifest`, `load_example`, `iter_examples`, `schema_errors`) and `tds_conform.mock` (`Provider`, `ProviderConfig`, `Network`, `make_server`) are what ThroughRide imports (editable path dependency). Keep them stable; additions are fine, renames need a CHANGELOG entry under *Changed* and a note in `docs/notes/throughride-alignment.md`. Every version declaration must agree (a test checks).

**Kit internals worth knowing.** `Context.call(...)` checks status, problem+json media type and the response schema from `runner.RESPONSE_SCHEMAS`; a root test fails if that table drifts from `spec/openapi.yaml`, so update both. A new scenario = a function decorated `@scenario(id, profile, title)` in `scenarios/`, with an id from SPEC §13. `tds-mock` is the executable reading of the spec: when you change behaviour in SPEC.md, change `mock/provider.py` and add a scenario so the kit and the mock agree. Kit tests include faulty provider subclasses to prove each scenario can fail; add one for each new scenario.

## Definition of done (any change)
1. Spectral lint passes.
2. All examples validate.
3. Crosswalk updated for any core field.
4. CHANGELOG.md entry.
5. If behavior changed: example added/updated and a conformance scenario covers it.

## Current status and next task
- Status: **v0.4 drafted** (2026-10-20): transfer profile schemas + operations, AsyncAPI events with signed delivery, multi-provider `tds-mock` with `/_sim`, `tds-demo`, five generated transfer examples (v0.4.1 adds `missed-connection-reprotect`), kit scenarios `webhook_delivery`/`tender_deadline`/`hold_request` and `check-events`. SPEC.md is Draft 2. Local git repo since v0.4.0 (no remote yet).
- Open for Evan: fill TDS columns in the crosswalk from Report 210; confirm the licence split in README; pick the real `$id` domain.
- v0.4.1 (alignment audit), v0.4.2 (`tds_conform.examples` public API) released 2026-10-21; v0.5 under way on `main` (CHANGELOG *Unreleased*): settlement split (§10.8) done.
- **Next:** v0.5 per SPEC.md §15 (settlement split and `docs/for-agencies.md` done): paratransit/NEMT profiles, schemathesis fuzzing; a settlement API operation needs Evan's decision.

## Near-term demo target
A SE Michigan pilot is being pitched (TheRide/AAATA + Oakland County transit + Jewish Family Services, supervised riders on cross-county medical trips). By v0.4 the mock server should simulate **two providers** so ThroughRide can demonstrate a protected transfer: tender → accept → milestones → ETA band → hold → handoff with custody record.
