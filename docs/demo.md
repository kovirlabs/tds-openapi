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

The response carries `signing_secret` once. Each event arrives as one POST with:

- `X-TDS-Timestamp`: Unix seconds
- `X-TDS-Signature`: `sha256=` + hex HMAC-SHA256(secret, `"<timestamp>.<raw body>"`)

Verify, reject skew over 300 s, return 2xx fast, dedupe on `event_id`, order by
`occurred_at`. Failed deliveries are retried with backoff (1, 2, 4… s). Plain
`http://` callbacks are accepted for `localhost` / `127.0.0.1` only.
`tds_conform.events.verify()` is a reference implementation of the check.

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
| `POST /_sim/{provider}/policy` | e.g. `{"tender_decision": "IGNORE"}` (or `REJECT`), `{"hold_decision": "PARTIAL"}` (`AUTO`, `GRANT`, `DENY`, `IGNORE`), `{"decision": "async"}`, `{"offer_ttl_s": 600}` (offers lapse to `EXPIRED`), `{"unsupported_requirements": ["STRETCHER"]}` (refused with 422), `{"refused_verification_methods": ["SELF_DECLARED", "UNSTATED"]}` (attestations verified that way, or without a method, refused with 422 `ELIGIBILITY_NOT_ACCEPTED`), `{"booking_window": {"max_advance_s": 1209600, "min_notice_s": 86400}}` or `{"booking_window_by_profile": {"paratransit": {"same_day": false}}}` (advertised on `/health`; requests outside refused with 422 `OUTSIDE_BOOKING_WINDOW`, tenders rejected with that reason; `null` clears) |
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
    binding_version,  # "0.5.3": the binding version these examples and schemas belong to
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
