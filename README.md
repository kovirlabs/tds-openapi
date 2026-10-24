# tds-openapi

A machine-readable binding of the **Transactional Data Specification** (TDS, TCRP G-16 / Report 210, 2020) for booking demand-response and microtransit trips across providers.

TDS describes what a trip-booking exchange should contain. This project makes it implementable and testable:

- **JSON Schema 2020-12** for the data model (`profiles/`)
- **OpenAPI 3.1** for request/response operations (`spec/openapi.yaml`)
- **AsyncAPI 3.0** for status, milestone, ETA and custody events (`spec/asyncapi.yaml`)
- A **conformance kit** (`tds-conform`) vendors can run and agencies can cite in RFPs ([docs/for-agencies.md](docs/for-agencies.md))
- A **reference provider** (`tds-mock`) and a static example mock for building without a live partner

It is not a new standard and does not replace TDS, GTFS-Flex or GTFS-Realtime. See [SPEC.md](SPEC.md) for scope and design.

## Status

**v0.7 (draft).** Core and transfer profiles: trip booking, plus tenders, hold requests, milestones, ETA bands and custody records for protected connections between providers, with signed webhook events (AsyncAPI, Standard Webhooks signatures, optional CloudEvents headers, secret rotation over the API) for every state change, and a CSV file format for providers that exchange trips in batches instead of calling an API. Draft paratransit and NEMT profiles, and a settlement split format for after-trip reconciliation. A conformance kit tests any endpoint at the Core, Core + Transfer, Core + Paratransit or Core + NEMT level and can fuzz it against the OpenAPI document, and a stateful reference mock runs several providers for development and demos ([docs/demo.md](docs/demo.md)). Field names are proposed and are being checked against TDS 2020 in [the crosswalk](mappings/tds-2020-crosswalk.md). Expect changes before 1.0.

| Version | Scope |
|---|---|
| v0.1 | Core schemas, crosswalk template, lint and validation tooling |
| v0.2 | Core operations, first examples, mock server, CI |
| v0.3 | `tds-conform` CLI running core scenarios against the mock |
| v0.4 | Transfer profile: protected cross-provider connections |
| v0.5 | Settlement split, paratransit/NEMT drafts and levels, complete events, fuzzing, RFP guidance |

## Layout

```
profiles/        core/ and transfer/ JSON Schemas (one per file)
spec/            openapi.yaml + paths/ + components/, asyncapi.yaml (reference profiles/)
conformance/     tds-conform package: kit (tds-conform), reference mock (tds-mock), demo (tds-demo)
mock-server/     docker-compose running both mocks
mappings/        crosswalks to TDS 2020 and borrowed vocabularies
examples/        one folder per scenario + manifest.yaml naming each file's schema
scripts/         validate_examples.py, finalize_bundle.py
tests/           tests for the schemas and tooling
```

## Working on it

Requires [uv](https://docs.astral.sh/uv/) (Python 3.12) and Node 20+.

```sh
npm install                          # pinned Spectral + Redocly
npm run bundle                       # single-file bundle at dist/openapi.yaml
npm run lint                         # bundle, then Spectral
npm run mock:static                  # Prism serving examples/ on :4011
uv run tds-mock --demo               # three reference providers on :4010 (see docs/demo.md)
uv run tds-demo run                  # narrate the six demo scenarios against it
uv run tds-conform run --url http://localhost:4010 --profile transfer   # conformance run → tds-conform-report/
uv run tds-conform fuzz --url http://localhost:4010   # schemathesis fuzzing vs the bundle (npm run bundle first)
uv run scripts/validate_examples.py  # schemas, conventions, examples, crosswalk coverage
uv run --group dev pytest            # schema, tooling, mock and kit tests
docker compose -f mock-server/docker-compose.yml up   # both mocks in Docker
```

### Testing your own endpoint

```sh
uv run tds-conform run --url https://provider.example.org/tds --token "$ACCESS_TOKEN" \
  --requester-id your-requester-id --provider-id your-provider-id \
  --trip-template my-trip.json      # a TripRequest your service area can actually serve
```

The report in `tds-conform-report/report.html` is self-contained and can be attached to an RFP response. Milestone, custody and heartbeat rules need real trips: capture your events and run `tds-conform check-events events.json --interchange tender.json`.

## Licence

Machine-readable artefacts and code (`spec/`, `profiles/`, `examples/`, `conformance/`, `scripts/`, `tests/`) are licensed under [Apache-2.0](LICENSE). Prose specification and documentation (`SPEC.md`, `mappings/`, `docs/`) are licensed under [CC-BY-4.0](LICENSE-SPEC).

The project is maintained by Kovir Labs and intended for donation to a neutral steward. See [CONTRIBUTING.md](CONTRIBUTING.md).
