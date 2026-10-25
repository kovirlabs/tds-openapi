# Demo and integration guide: transfer profile against tds-mock

For broker developers (ThroughRide first) pointing adapters at the reference providers,
and for rehearsing the SE Michigan pilot demo. Everything here runs locally.

## Start the providers

```sh
uv run tds-mock --demo                 # or: docker compose -f mock-server/docker-compose.yml up
```

`--demo` runs three providers in one process, each with its own base URL:

| provider_id | Base URL | Role in the demo |
|---|---|---|
| `aaata-a-ride` | `http://localhost:4010/p/aaata-a-ride` | feeder leg (home → hub) |
| `oakland-go` | `http://localhost:4010/p/oakland-go` | onward leg (hub → clinic) |
| `county-connector` | `http://localhost:4010/p/county-connector` | fallback when a tender expires |

Configure one adapter per provider with its base URL. Un-prefixed URLs
(`http://localhost:4010/trips`) go to the first provider. Feeder heartbeats come every
10 s in demo mode (`--heartbeat` to change).

**Auth.** `Authorization: Bearer <requester_id>`; the token *is* the requester id
(use `throughride-demo` to match the examples). Every POST and PATCH needs an
`Idempotency-Key`.

## Receive events

Subscribe at **each** provider you work with:

```http
POST /p/aaata-a-ride/subscriptions
Authorization: Bearer throughride-demo
Idempotency-Key: 4f1c…
Content-Type: application/json

{"callback_url": "http://localhost:8000/tds/events",
 "event_types": ["TRIP_STATUS_CHANGED", "TRIP_SCHEDULED", "TRIP_CANCELLED", "TENDER_RESPONSE",
                 "HOLD_RESPONSE", "MILESTONE", "ETA_UPDATE", "CUSTODY_HANDOFF"]}
```

The response carries `signing_secret` once (`whsec_…`). Each event arrives as one POST with:

- `webhook-id` (= `event_id`), `webhook-timestamp` (Unix seconds) and `webhook-signature`
  (`v1,` + base64 HMAC-SHA256 of `"<id>.<timestamp>.<raw body>"`): Standard Webhooks, so
  its libraries verify them as they are
- `X-TDS-Timestamp` and `X-TDS-Signature` (`sha256=` + hex HMAC-SHA256(secret,
  `"<timestamp>.<raw body>"`)): deprecated since 0.6.0, still sent
- with `"cloudevents": true` in the subscription, CloudEvents binary-mode `ce-*` headers
  (SPEC.md §9.1); the body is the same

Verify either signature, reject skew over 300 s, return 2xx fast, dedupe on (`provider_id`,
`event_id`), order by `occurred_at`. Failed deliveries are retried with backoff (1, 2, 4… s).
Plain `http://` callbacks are accepted for `localhost` / `127.0.0.1` only.
`tds_conform.events.verify_any()` is a reference implementation of the receiver check;
`examples/webhook-delivery/` shows one delivery on the wire.

To rotate the secret (SPEC.md §12.1), `POST /subscriptions/<subscription_id>/secrets` with
`{"overlap_s": 86400}` (and an `Idempotency-Key`): the answer carries the new secret once; the
old one keeps working until its `expires_at`, and deliveries carry a `webhook-signature` entry
for each. `DELETE /subscriptions/<subscription_id>/secrets/<secret_id>` retires the old one
early; `GET …/secrets` lists what is active. `examples/secret-rotation/` walks through it.

To take another event type later (SPEC.md §9.3), `PATCH /subscriptions/<subscription_id>` with
the full new list, e.g. `{"event_types": ["TRIP_STATUS_CHANGED", …, "ETA_UPDATE"]}` (and an
`Idempotency-Key`): the subscription keeps its id and secret. `GET /subscriptions` lists yours
(never with secrets) and `DELETE /subscriptions/<subscription_id>` stops deliveries to one.
`examples/subscription-changes/` walks through it.

## The broker flow

1. **Tender both legs** with the same `interchange` (`POST /tenders` at each provider).
   201 `ACCEPTED` creates the trip under `trip.trip_id`; read `accepted_terms` (the
   provider may cap `max_hold_s` / `auto_hold_s`). 202 `OFFERED` means wait for
   `TENDER_RESPONSE`.
2. **Watch** `MILESTONE` and `ETA_UPDATE` from the feeder. Milestones are relative to each
   leg's own pickup and dropoff (no transfer-specific values; label hub arrivals in your UI).
3. **Protect the connection.** When the feeder's ETA band threatens the onward pickup,
   `POST /p/oakland-go/trips/{trip_id}/hold` with `requested_until`. `GRANTED`/`PARTIAL`
   move the onward `scheduled_pickup` (and emit `TRIP_SCHEDULED`).
4. **Re-protect** if the onward provider rejects or lets the tender expire: cancel with
   `MISSED_CONNECTION` if a trip exists, then tender the leg to the next provider
   (with human approval if your fallback policy says `approval: REQUIRED`).
5. **Custody.** For `HAND_TO_HAND` riders or `supervision_required` interchanges, expect
   `CUSTODY_HANDOFF` at each pickup and dropoff.

## Drive the simulation

Vehicles don't move on their own. `/_sim` (no auth) moves legs and changes how a provider answers:

| Call | Effect |
|---|---|
| `POST /_sim/{provider}/trips/{trip_id}/advance` | next milestone; body `{"until": "ARRIVED_DROPOFF"}` runs through to it |
| `POST /_sim/{provider}/trips/{trip_id}/delay` | `{"seconds": 900, "cause": "TRAFFIC"}`: the leg runs late; emits `ETA_UPDATE` |
| `POST /_sim/{provider}/trips/{trip_id}/no-show` | rider no-show (after `ARRIVED_PICKUP`) |
| `POST /_sim/{provider}/trips/{trip_id}/fail` | `{"cause": "VEHICLE"}`: leg fails |
| `POST /_sim/{provider}/trips/{trip_id}/cancel` | `{"reason": "PROVIDER_UNABLE"}`: the provider cancels the trip itself |
| `POST /_sim/{provider}/policy` | e.g. `{"tender_decision": "IGNORE"}` (or `REJECT`), `{"hold_decision": "PARTIAL"}` (`AUTO`, `GRANT`, `DENY`, `IGNORE`), `{"decision": "async"}`, `{"offer_ttl_s": 600}` (offers lapse to `EXPIRED`), `{"unsupported_requirements": ["STRETCHER"]}` (refused with 422), `{"refused_verification_methods": ["SELF_DECLARED", "UNSTATED"]}` (attestations verified that way, or without a method, refused with 422 `ELIGIBILITY_NOT_ACCEPTED`), `{"booking_window": {"max_advance_s": 1209600, "min_notice_s": 86400}}` or `{"booking_window_by_profile": {"paratransit": {"same_day": false}}}` (advertised on `/health`; requests outside refused with 422 `OUTSIDE_BOOKING_WINDOW`, tenders rejected with that reason; `null` clears), `{"rate_limit": {"requests": 5, "per_s": 60}}` (per requester; more get 429 `RATE_LIMITED` with `Retry-After`, SPEC.md §7.2; `null` clears) |
| `POST /_sim/{provider}/unavailable` | `{"for_s": 120}`: every call, `/health` included, gets 503 `TEMPORARILY_UNAVAILABLE` with `Retry-After` until then; `{"for_s": 0}` ends it |
| `GET /_sim/{provider}/events?trip_id=…` | everything that provider emitted |
| `GET /_sim/deliveries` | webhook delivery attempts and outcomes |

## Rehearse the transfer scenarios

```sh
uv run tds-demo run                                    # all six, 1 demo minute = 1 s
uv run tds-demo run --scenario late-first-leg-hold --callback-url http://localhost:8000/tds/events
```

`tds-demo` plays the broker so you can see the expected calls and events before your
adapter does the same. Recorded runs of each scenario are in `examples/<scenario>/`
(`manifest.yaml` narrates them; `events.json` is the full event stream).

## Check your event log

Save what your webhook received as a JSON array and run:

```sh
uv run tds-conform check-events received.json --interchange tender-onward.request.json
```

It checks schemas, milestone order, custody records and heartbeat gaps.

## Check batch files

Providers without an API exchange CSV files instead (SPEC.md §9.2; `examples/csv-batch/` shows
two rounds of requests and answers and a statuses file):

```sh
uv run tds-conform check-csv tds_requests_20261104T150000Z.csv tds_answers_20261104T204000Z.csv
```

It checks each file's name, encoding, columns and rows, what each row stands for in the API,
and that every answer row answers a request row. `tds_conform.csvfile` reads, writes and
converts rows (`row_to_json`, `json_to_row`).

## Relay mailbox

A provider whose scheduler cannot accept connections swaps those files through the
requester's relay mailbox (SPEC.md §9.4, `spec/relay-mailbox.yaml`): its agent polls
`GET /outbox`, downloads and acknowledges request files, and uploads answers and statuses with
`PUT /inbox/{file_name}`. `tds-mock` serves one per provider at `/mailbox`; the token is the
provider id. To develop an agent against it:

```sh
uv run tds-mock --provider county-dial-a-ride
curl -X POST localhost:4010/_sim/mailbox/county-dial-a-ride/outbox -d '{}'   # queue a request file
curl -H 'Authorization: Bearer county-dial-a-ride' localhost:4010/mailbox/outbox
curl localhost:4010/_sim/mailbox/county-dial-a-ride        # last poll, acks, what was uploaded
```

To check a requester's mailbox (yours, or a test deployment's), queue a request file for a test
provider and run:

```sh
uv run tds-conform mailbox --url https://broker.example.org/api/relay --token <mailbox token> --take-files
```

`--take-files` lets the kit acknowledge the waiting file; leave it off on a mailbox a real agent
uses. The upload scenario leaves one header-only answers file named `…_conform-<hex>.csv`.

## Use tds-conform from another project

Brokers can depend on the kit instead of copying it, for test fakes that replay
`examples/`, payload validation against `profiles/`, or the mock in-process. With uv, as a
dev-only path dependency (production images never need it):

```toml
[dependency-groups]
dev = ["tds-conform"]

[tool.uv.sources]
tds-conform = { path = "../tds-openapi/conformance", editable = true }
```

Then use the public accessors rather than paths into this repo:

```python
from tds_conform.examples import (
    binding_version,  # "0.8.0": the binding version these examples and schemas belong to
    examples_dir,  # Path to examples/ (one folder per scenario)
    profiles_dir,  # Path to profiles/ (JSON Schemas)
    scenarios,  # ["cancellation", ..., "two-provider-transfer"]
    load_manifest,  # load_manifest("late-first-leg-hold")["files"] -> {filename: {schema, operation?, status?}}
    load_example,  # load_example("late-first-leg-hold", "06-hold-onward.response.json") -> parsed JSON
    iter_examples,  # (filename, manifest entry, parsed JSON) for each file of a scenario
    schema_errors,  # schema_errors(payload, "tender", "transfer") -> [] when valid
)
from tds_conform.mock import Network, Provider, ProviderConfig, make_server
```

The same calls work from an editable install (they read this checkout) and from a wheel
(which carries its own copy of `profiles/` and `examples/` under `tds_conform/_data/`).
Build wheels with `uv build --wheel conformance`; the sdist does not include the data.

To vendor examples at a pinned version, copy `examples_dir()` and record
`binding_version()`; a test comparing the recorded version with `binding_version()` then
catches a stale copy after the sister repo moves on.
