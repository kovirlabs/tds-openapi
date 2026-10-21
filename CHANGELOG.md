# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the spec uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

Toward **0.5.0** (SPEC.md §15). Version fields stay at 0.4.2 until 0.5.0 is cut.

### Added
- **Settlement split** (`profiles/transfer/settlement-split.yaml`, SPEC.md §10.8): after-trip
  reconciliation of a linked journey, one line per leg with `trip_status`, `cost`,
  `funding_source`, optional `sponsor_ref`, `authorization_ref`, `cancellation_reason` and
  `delay_cause`, plus `total`. Data only; no API operation yet.
- `core/funding-source.yaml`: the funding source enum (`RIDER`, `SECTION_5310`, `SECTION_5311`,
  `MEDICAID_NEMT`, `AGENCY`, `SPONSOR`, `OTHER`), now shared by `fare_funding` and the split.
- `tds_conform.settlement.settlement_problems()`: one currency, total = sum of lines, each leg
  once, terminal statuses only; the example validator applies it.
- `docs/for-agencies.md`: what to ask for in RFPs and contracts, sample clauses, how to check
  conformance reports.
- Example `settlement-split` with splits for the `two-provider-transfer` and
  `missed-connection-reprotect` journeys (a test checks they match the generated trips).

- **Draft paratransit and NEMT profiles** (SPEC.md §11): `paratransit/trip-request` (eligibility
  category, mobility devices, PCA) and `nemt/trip-request` (authorization by reference, level of
  service, will-call), each a core TripRequest plus one profile object. The NEMT object is closed
  and has no free-text fields (a test enforces it). `tds_conform.profiles.profile_problems()`
  holds the cross-field rules; `tds-mock` validates both objects when present. Examples
  `paratransit-trip` and `nemt-trip`.
- `leg.service_requirements`: `STRETCHER`.
- `tds-conform fuzz` (extra `tds-conform[fuzz]`, schemathesis 4): positive and negative
  generated requests to every operation of `dist/openapi.yaml`; checks no 5xx, documented
  status codes and media types, response schemas and headers, and rejection of invalid input.
  Writes `fuzz.json`. `POST /subscriptions` is skipped by default. CI job
  `fuzz-vs-reference-mock`; tests include faulty providers that fuzzing must catch.

### Changed
- `fare-funding.funding_source` references `core/funding-source.yaml` (same values).
- `POST /trips/{trip_id}/confirm` documents 400 (e.g. missing `Idempotency-Key`); found by fuzzing.

### Fixed
- `tds-mock` answered a body that is not UTF-8 JSON by dropping the connection; it now returns
  400 `INVALID_REQUEST` (found by fuzzing).
- SPEC.md Draft 3: §10.8 written out; §7 notes settlement endpoints stay deferred.
- SPEC.md §12: one scope list (`tenders:write`, not `tenders:respond`); dropped the reference to a
  nonexistent `security/auth.yaml`.

## [0.4.2] - 2026-10-21

### Added
- `tds_conform.examples`: public accessors so other projects can use the published examples
  and schemas without hard-coding the repo layout: `binding_version()`, `examples_dir()`,
  `profiles_dir()`, `scenarios()`, `load_manifest(scenario)` (file entries always dicts),
  `load_example(scenario, filename)`, `iter_examples(scenario)`, `schema_errors(...)`. Works
  from a checkout, an editable path install and a wheel.
- `tds_conform.mock` re-exports `Provider`, `ProviderConfig`, `Network`, `make_server`.
- `tds_conform` ships `py.typed`.
- docs/demo.md: using tds-conform as a path dependency.
- Test that every version declaration (package.json, both pyprojects, OpenAPI, AsyncAPI,
  `tds_conform.__version__`) agrees.

## [0.4.1] - 2026-10-21

Audit of the ThroughRide alignment items (`docs/notes/throughride-alignment.md` #1–12) against
schemas, examples, mock and kit. All were already in the schemas; this release fills the gaps
in examples, mock behaviour and validation. Additive only.

### Added
- Generated example `missed-connection-reprotect`: a hold answered `PARTIAL` (provider
  counter-offer with `scheduled_pickup`/`held_until`), a `RETIME` left unanswered until it is
  `EXPIRED` (broker raises `HOLD_UNANSWERED`), cancel with `MISSED_CONNECTION`, and a re-tender
  to another provider. Wired into `POST /trips/{trip_id}/hold` (201, 202),
  `GET /trips/{trip_id}/holds/{hold_id}` (200) and `POST /trips/{trip_id}/cancel`.
- `exception` schema example for `HOLD_UNANSWERED`.
- Validator: tender-request and interchange examples must follow the interchange rules
  (`auto_hold_s` ≤ `max_hold_s`; `HAND_TO_HAND` on a `PROTECTED` interchange needs
  `supervision_required`).

### Changed
- `tds-mock` refuses (400 `INVALID_REQUEST`) a tender whose leg has `HAND_TO_HAND` on a
  `PROTECTED` interchange without `supervision_required: true` (SPEC.md §10.1 already said MUST).
- SPEC.md §10: section headings name the actual schema files; §10.6 uses `interchange_id`
  (as the schema does), not `interchange_ref`.
- Generated event streams carry `schema_version: "0.4.1"`.

## [0.4.0] - 2026-10-20

### Added
- **Transfer profile** (`profiles/transfer/`, SPEC.md §10): `interchange`, `leg-ref`, `tender-request`, `tender`, `accepted-terms`, `hold-request`, `hold`, `eta-band`, `delay-cause`, `milestone`, `custody-party`, `custody-handoff`, `exception`, `fallback-policy`. Every transfer schema carries `x-profile: transfer` (now enforced by the validator).
- Operations: `POST /tenders`, `GET /tenders/{tender_id}`, `POST /trips/{trip_id}/hold`, `GET /trips/{trip_id}/holds/{hold_id}`.
- **Events**: `spec/asyncapi.yaml` (AsyncAPI 3.0) with signed webhook delivery; envelope `core/event` and typed events `TRIP_STATUS_CHANGED`, `TRIP_SCHEDULED`, `TRIP_CANCELLED`, `TENDER_RESPONSE`, `HOLD_RESPONSE`, `MILESTONE`, `ETA_UPDATE`, `CUSTODY_HANDOFF`.
- `core/refusal-reason` (shared by 422 errors and rejected tenders).
- `tds-mock`: several providers per process (`/p/<provider_id>` base URLs, `--provider`, `--demo`), webhook delivery with HMAC signatures and retries, tenders, holds, milestones, ETA bands and heartbeats, custody records, and `/_sim` controls (advance, delay, no-show, fail, answer policies, event log).
- `tds-demo`: broker-side walkthroughs of the four v0.4 scenarios (`run` live, `generate-examples` on a fake clock).
- Examples `two-provider-transfer`, `late-first-leg-hold`, `tender-expired-retender`, `supervised-rider-custody-handoff`, generated by `tds-demo` (a test fails if they drift). Manifest entries may be `kind: event-stream`.
- Kit scenarios `webhook_delivery` (core), `tender_deadline` and `hold_request` (transfer); `--profile transfer` (Core + Transfer); built-in webhook receiver (`--listen`, `--callback-url`); `tds-conform check-events` for `milestone_sequence`, `custody_required` and `eta_heartbeat_staleness` on captured event logs.
- Validator: AsyncAPI event wiring, event-stream validation (schemas plus milestone and custody rules).
- `scripts/sync_crosswalk.py` adds TODO rows for new core fields.
- `docs/demo.md`: integration guide for brokers pointing adapters at the mock.

### Changed
- OAuth scope `tenders:respond` → `tenders:write` (brokers send tenders; providers answer in the response or by event, so brokers expose no API).
- Hold actions are `HOLD` and `RETIME`; re-protecting is broker-side (cancel + new tender), not a hold action.
- `interchange.heartbeat_s` added; ETA `target` is `PICKUP` or `DROPOFF`.
- `subscription-request.callback_url` allows `http://` for loopback addresses only.
- Docker `provider` service runs `tds-mock --demo`; CI conformance runs at the transfer level and checks example event streams.
- Python pinned to 3.12 (`.python-version`).

### Not yet
- Settlement split (§10.8), paratransit and NEMT profiles: v0.5+.

## [0.3.0] - 2026-10-20

### Added
- `conformance/`: the `tds-conform` package (uv workspace member).
  - `tds-conform run --url URL --profile core` runs `book_confirm_cancel` and `idempotency` and writes `report.json` and a self-contained `report.html`. Every response is checked against the schema its operation declares; async providers are polled within 3× their declared `typical_response_s`; a provider that refuses the test trip makes the scenario INCONCLUSIVE, not FAIL (`--trip-template` supplies one it can serve).
  - `tds-mock`: stateful reference provider (sync or async decisions, idempotency store, state machine, window ordering, configurable unsupported requirements). Bearer token = requester id.
  - Wheels bundle `profiles/` and `examples/`; test trips come from `examples/single-trip/`.
- `error.code` value `TRIP_ID_IN_USE` (409 when a requester reuses a trip_id under a new Idempotency-Key).
- CI workflow `conformance.yml` (kit vs `tds-mock`, sync and async; reports uploaded).
- `mock-server/docker-compose.yml` runs both mocks: `tds-mock` on :4010 and Prism on :4011.

### Changed
- `npm run mock` is now `npm run mock:static` and serves on :4011; :4010 is the stateful mock.

## [0.2.0] - 2026-10-20

### Added
- Core operations in `spec/openapi.yaml` (SPEC.md §7): `POST /trips`, `GET`/`PATCH /trips/{trip_id}`, `POST /trips/{trip_id}/confirm`, `POST /trips/{trip_id}/cancel`, `POST /subscriptions`, `GET /health`. OAuth 2.0 client-credentials scopes; `Idempotency-Key` on every mutating call.
- Core schemas: `trip-request`, `trip-modification`, `cancel-request`, `cancel-reason`, `cancellation`, `subscription-request`, `subscription`, `event-types`, `health`. `trip.cancellation`.
- `error.code` protocol values: `INVALID_REQUEST`, `UNAUTHORIZED`, `NOT_FOUND`, `INVALID_STATE`, `IDEMPOTENCY_KEY_REUSED`.
- `leg.service_requirements`: `HAND_TO_HAND` and `CHILD_SEAT` (from ThroughRide alignment notes).
- `/health` declares `operated_via` (`DIRECT` / `BROKER_PROXY`) and `typical_response_s`; asynchronous (202) decisions are fully conformant.
- Examples: `single-trip` (sync offer → confirm → scheduled), `cancellation` (async request → cancel → 409), `errors`, `health`, `subscriptions`. Manifests now name each file's operation and status.
- `validate_examples.py` checks manifest ↔ spec wiring: every example the spec references is in a manifest, at the operation, status and schema the manifest says.
- `scripts/finalize_bundle.py`; Prism mock (`npm run mock`, `mock-server/docker-compose.yml`); CI workflows `lint.yml`, `bundle.yml`.
- Spectral rule `tds-mutations-require-idempotency-key`.

### Changed
- `rider.rider_ref_id`: requesters SHOULD use a stable pairwise pseudonym per provider.
- SPEC.md Draft 1: ThroughRide alignment items folded in (broker proxy, cancel reasons, `auto_hold_s`, fallback `approval`, milestones without transfer-specific values, ETA `source`/`delay_cause`, hold `respond_by`/`EXPIRED`/counter-offers, `HOLD_UNANSWERED`, timing conformance).
- `npm run lint` lints the bundle (Spectral mis-validates `$ref`'d path items in OAS 3.1 sources).
- Example files are referenced with `externalValue`.

### Removed
- Hand-maintained `components.schemas` list in `spec/openapi.yaml` (operations reference schema files directly).

## [0.1.0] - 2026-10-20

### Added
- Core JSON Schemas in `profiles/core/`: `trip`, `trip-status`, `leg`, `location`, `address`, `time-window`, `vehicle-ref`, `party`, `rider`, `contact`, `eligibility`, `fare-funding`, `money`, `error` (RFC 9457).
- Schema-level rules: core trips have exactly one leg; a leg needs a pickup or dropoff window; rider contact requires `contact_consent: true`.
- `spec/openapi.yaml` skeleton exposing the core schemas as components (no operations yet).
- `mappings/tds-2020-crosswalk.md` template listing every core field; all TDS mappings pending (`TODO`).
- `scripts/validate_examples.py`: metaschema, convention, embedded-example, scenario-example and crosswalk-coverage checks.
- `.spectral.yaml` with a snake_case path-parameter rule; pinned Spectral and Redocly in `package.json`.
- `examples/single-trip/trip-scheduled.json`.
- README, CONTRIBUTING (DCO), LICENSE (Apache-2.0), LICENSE-SPEC (CC-BY-4.0).
