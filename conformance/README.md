# tds-conform

Conformance kit and reference mock provider for the [TDS OpenAPI binding](../README.md).

- `tds-conform run --url URL --token TOKEN --profile core` runs the conformance scenarios
  against a provider endpoint and writes a JSON and HTML report.
- `tds-conform fuzz --url URL` (extra `tds-conform[fuzz]`) fuzzes every operation with
  schemathesis against the bundled OpenAPI document and writes `fuzz.json`; it finds contract
  breaks (5xx, undocumented statuses, bodies off-schema) but does not decide a level.
- `tds-mock` runs a stateful reference provider (trips persist across calls, idempotency
  and the trip state machine are enforced) for developing requesters without a live partner.

Schemas and example payloads come from the repo's `profiles/` and `examples/` (bundled into
the wheel), so the kit tests exactly what the spec publishes.

Other projects (e.g. a broker's test fakes) should reach them through the public API, not
repo paths: `tds_conform.examples` (`binding_version`, `examples_dir`, `profiles_dir`,
`scenarios`, `load_manifest`, `load_example`, `iter_examples`, `schema_errors`) and
`tds_conform.mock` (`Provider`, `ProviderConfig`, `Network`, `make_server`). See
[docs/demo.md](../docs/demo.md#use-tds-conform-from-another-project).
