# tds-conform

Conformance kit and reference mock provider for the [TDS OpenAPI binding](../README.md).

- `tds-conform run --url URL --token TOKEN --profile core` runs the conformance scenarios
  against a provider endpoint and writes a JSON and HTML report.
- `tds-mock` runs a stateful reference provider (trips persist across calls, idempotency
  and the trip state machine are enforced) for developing requesters without a live partner.

Schemas and example payloads come from the repo's `profiles/` and `examples/` (bundled into
the wheel), so the kit tests exactly what the spec publishes.
