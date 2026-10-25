# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the spec uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Upload reports on the relay mailbox (SPEC.md §9.4, TQ-23), optional for requesters and
  declared with `upload_reports: true` in the `GET /outbox` listing. `GET /inbox/{file_name}`
  answers `csv/mailbox-report`: `RECEIVED`; `PROCESSED` with `row_count`, `applied_count`,
  `duplicate_count`, `rejected_count` and `rows`, one `csv/mailbox-row-outcome` (`row`,
  `outcome` `DUPLICATE` or `REJECTED`, `code`, `column`) for every row not applied; or
  `REFUSED` with `refusal` (`INVALID_ENCODING`, `INVALID_HEADER`, `OTHER`). Row codes are
  `csv/row-problem`: `MISSING_VALUE`, `INVALID_VALUE`, `WRONG_PROVIDER`, `WRONG_REQUESTER`,
  `UNKNOWN_REQUEST`, `UNKNOWN_TRIP`, `KEY_REUSED`, `INVALID_STATE`, `OTHER`. No cell contents;
  kept at least 7 days; 404 `NOT_FOUND` otherwise.
- The listing may carry `requester_id` and `upload_reports`.
- `tds_conform.mailbox.upload_report` computes a report for a file (used by `tds-mock` and the
  example validator).
- Example `relay-mailbox-report` (an upload with a duplicate, a reused key, an unknown request,
  another provider's row and a bad time); the validator recomputes each report marked
  `report_of`, and a new manifest kind `csv-upload` holds an upload that may have bad rows.
- `tds-mock` reads uploads after `Mailbox(process_delay_s=…)` (default 0), in arrival order,
  and serves their reports; `Mailbox(upload_reports=False)` turns them off.
- Scenario `relay_mailbox_report` in the Relay mailbox level (`tds-conform mailbox`, new
  `--max-wait`, default 120 s); passes with a note when the mailbox does not report.

## [0.9.0] - 2026-10-25

A relay mailbox for CSV profile files (TQ-22), so an agent beside a scheduler that can only
call out works with any requester: the RideSheet TDS Connector and ThroughRide's relay agent
become broker-neutral. Additive: a new, separate OpenAPI document served by requesters, new
error codes, and a looser CSV file-name tag. Generated event streams change only in
`schema_version`.

### Added
- Relay mailbox for CSV profile files (SPEC.md §9.4, TQ-22), for providers whose scheduler
  can only call out (behind a firewall, or in Google Apps Script). A new OpenAPI document,
  `spec/relay-mailbox.yaml` (bundled to `dist/relay-mailbox.yaml` and linted), served by the
  **requester**; the provider's agent is the client. `Authorization: Bearer <token>`, the
  token naming one provider's mailbox; optional `X-Relay-Agent`. No `Idempotency-Key`: every
  call is safe to repeat as it stands.
  - `GET /outbox`: request files not yet acknowledged, oldest first (`csv/mailbox-listing`
    with `provider_id`, `poll_interval_s`, optional `max_file_bytes`; entries
    `csv/mailbox-file`: `name`, `size`, `sha256`, `created_at`).
  - `GET /outbox/{file_name}`: the bytes (`text/csv`) with `X-Relay-SHA256`.
  - `POST /ack/{file_name}`: idempotent, the first `acked_at` kept (`csv/mailbox-ack`).
  - `PUT /inbox/{file_name}`: an answers or statuses file, write-once by name: 201 stored,
    200 the same bytes again, 409 `FILE_NAME_TAKEN` for other bytes (`csv/mailbox-receipt`
    with `stored`, `sha256`, `size`, `received_at`).
  - Error codes `INVALID_FILE_NAME` (400), `EMPTY_FILE` (400), `FILE_TOO_LARGE` (413),
    `FILE_NAME_TAKEN` (409) in `core/error`; unknown files are `NOT_FOUND` (404); 429/503
    with `Retry-After` and `retry_after_s` as in §7.2.
- Example `relay-mailbox`: list, download, acknowledge, upload, retry, and every refusal. The
  validator checks that a listing's sizes and hashes match the CSV files it names, and wires
  examples into both OpenAPI documents.
- `tds-mock` serves a mailbox per provider at `/mailbox` (token = provider id), with
  `GET /_sim/mailbox/<provider_id>` (last poll, acknowledgements, uploads with their text),
  `POST /_sim/mailbox/<provider_id>/outbox` (queue a request file; default the example) and
  `POST /_sim/mailbox/<provider_id>/unavailable`. `Network(..., mailboxes=[...])` takes others.
- `tds-conform mailbox --url <mailbox base URL> --token <token> [--take-files]` runs the
  scenarios `relay_mailbox_upload` and `relay_mailbox_download` for the new **Relay mailbox**
  level (a requester's, not a provider's). Without a waiting file, or without `--take-files`
  for the acknowledgement, the download scenario is INCONCLUSIVE. Reports never copy file
  contents. `tds_conform.mailbox` holds the name rules (`inbox_name_problem`,
  `is_outbox_name`) and `sha256_hex`.
- The kit's `Context.call` takes `content` (raw bytes) and `headers`; `Settings.take_files`.

### Changed
- CSV profile file names (SPEC.md §9.2): the optional suffix after the time is now a tag of
  1 to 40 letters, digits or `-` (`tds_answers_20261104T204000Z_1a2b3c4d.csv`), not only a
  number, so writers that may write twice in one second can add a random tag.
  `tds_conform.csvfile.FILE_NAME` accepts it; every name valid before is still valid.

## [0.8.0] - 2026-10-25

Change or end a webhook subscription over the API, so a broker can take a new event type
(such as `ETA_UPDATE`) without a second subscription, callback and signing secret (TQ-21).
Additive and optional for providers (declared on `/health`); minor version because it adds
operations. Generated event streams change only in `schema_version`.

### Added
- Reading, changing and ending webhook subscriptions (SPEC.md §9.3), optional for providers,
  declared with `subscription_management: true` on `/health` (TQ-21). A subscriber can take a
  new event type such as `ETA_UPDATE` without a second subscription, callback and secret:
  - `GET /subscriptions` lists the requester's own subscriptions, oldest first
    (`subscription-list`); `GET /subscriptions/{subscription_id}` reads one. Neither returns
    `signing_secret`.
  - `PATCH /subscriptions/{subscription_id}` (`Idempotency-Key`; body `subscription-update`
    with at least one of `event_types`, `callback_url`, `cloudevents`) replaces the fields it
    carries; `event_types` is replaced as a whole. 200 with the subscription, `updated_at`
    set, no `signing_secret`. The id, secrets and `signing_secret_id` stay the same, so
    deliveries keep verifying with the secret the subscriber holds. Events raised after the
    answer follow the new settings.
  - `DELETE /subscriptions/{subscription_id}` (`Idempotency-Key`) ends it: 200 with the
    subscription, no delivery attempt starts afterwards (retries included), and later calls
    for it (including its secret operations) are `NOT_FOUND`.
  - Scope `events:subscribe`; another requester's subscription is `NOT_FOUND`; replays with
    the same key return the same answer.
  - New core schemas `subscription-update`, `subscription-list`; `subscription.updated_at`;
    `health.subscription_management`. Crosswalk rows (TODO, protocol fields).
- Example `subscription-changes`: subscribe, read, add `ETA_UPDATE` with PATCH, list, an
  `ETA_UPDATE` delivery that verifies with the secret from subscribing, end, read after the end
  (404).
- `tds-mock` implements the four operations (`ProviderConfig.subscription_management`,
  default on). Its webhook sender drops queued attempts for an ended subscription (delivery
  log outcome `dropped: subscription ended`; `Dispatcher.submit`'s `secrets` callable returns
  an empty list for one).
- Core scenario `subscription_management`: runs only when `/health` declares it (others pass
  with a note), so existing providers stay conformant. With a webhook receiver it also checks
  that after a change only the new event types arrive, signed with the original secret, and
  that nothing arrives after the end.

### Changed
- `POST /subscriptions` and the `events:subscribe` scope descriptions mention the new
  operations. SPEC.md §15: v0.8 is subscription management; settlement exchange moves to v0.9+.

## [0.7.1] - 2026-10-24

Back-pressure: providers say how long to wait, so brokers back off instead of retrying on
their own schedule (TQ-20). Additive: new optional field, new error codes (clients already
treat unknown codes as a generic failure), and 429/503 now documented on every operation.
Patch version, as earlier additive fields were. Generated event streams change only in
`schema_version`.

### Added
- SPEC.md §7.2 Back-pressure. Every operation may answer **429** (code `RATE_LIMITED`, this
  client is over the limit) or **503** (code `TEMPORARILY_UNAVAILABLE`, the provider cannot
  take requests for now). Nothing is processed and the `Idempotency-Key` is not consumed.
  Providers SHOULD send the wait as the `Retry-After` header (RFC 9110 §10.2.3,
  delta-seconds preferred, HTTP-date allowed) and as `retry_after_s` in the Problem Details;
  requesters SHOULD wait at least that long (the larger of the two when both are present) and
  repeat the same request with the same `Idempotency-Key`. A 429/503 is not a refusal.
- `core/error`: `retry_after_s` (integer ≥ 0) and codes `RATE_LIMITED`,
  `TEMPORARILY_UNAVAILABLE`. Crosswalk row (TODO, protocol field).
- OpenAPI: reusable header `components/headers.yaml#/RetryAfter` and responses
  `TooManyRequests` / `ServiceUnavailable`, referenced as 429 and 503 from every operation
  (inline on `POST /trips`, with examples).
- Examples `errors/rate-limited.json` (429) and `errors/temporarily-unavailable.json` (503).
- Webhook deliveries (§9): a receiver may answer 429/503 with `Retry-After`; the sender SHOULD
  not attempt again sooner. `tds-mock`'s sender does so (`Dispatcher.max_retry_after_s` caps
  it; the delivery log records `retry_after_s`).
- `tds_conform.backpressure`: `retry_after_s(headers, body)`, `parse_retry_after`,
  `retry_after_header`, `backpressure_problems`, `BACKPRESSURE_CODES`.
- `tds-mock`: `ProviderConfig.rate_limit` (`{"requests": N, "per_s": S}` per requester, also
  settable with `/_sim/<id>/policy`) answers 429; `POST /_sim/<id>/unavailable {"for_s": …}`
  answers 503 to every call for that long. Both send `Retry-After` and `retry_after_s`.
  `Provider.admit(requester)` is the hook.
- Kit: an unexpected 429/503 in any scenario is retried with the same `Idempotency-Key` after
  the hinted wait (up to `Settings.backpressure_retries`, default 3; a wait over
  `max_backpressure_wait_s`, default 30 s, makes the scenario INCONCLUSIVE). The hint is
  checked at SHOULD level: new advisory checks (`Check.advisory`, `Step.advise`) are shown
  as warnings and never fail a scenario; `report.json` `summary.warnings` counts them.
- Validator: every operation documents 429 and 503 with `Retry-After`; 429/503 examples carry
  the matching code and `retry_after_s`.

## [0.7.0] - 2026-10-24

Rotate a webhook subscription's signing secret over the API, so a second secret need not be
handed over out of band. Additive and optional for providers (declared on `/health`); minor
version because it adds operations. Generated event streams change only in `schema_version`;
generated subscribe responses gain `signing_secret_id`.

### Added
- Subscription secret rotation over the API (SPEC.md §12.1), optional for providers, declared
  with `secret_rotation: true` on `/health`:
  - `POST /subscriptions/{subscription_id}/secrets` mints a new signing secret (body
    `secret-rotation-request` with optional `overlap_s`, 0 to 30 days; default SHOULD be
    86400) and answers 201 with the active secrets newest first (`subscription-secret-list`),
    the new value only in the first entry. Older secrets get `expires_at` = now + overlap
    (never lengthened). A replay with the same `Idempotency-Key` returns the same secret.
  - `GET /subscriptions/{subscription_id}/secrets` lists active secrets without values.
  - `DELETE /subscriptions/{subscription_id}/secrets/{secret_id}` retires an older secret
    early (200, `subscription-secret`); the newest cannot be retired (409 `INVALID_STATE`).
  - New core schemas `subscription-secret`, `subscription-secret-list`,
    `secret-rotation-request`; `subscription.signing_secret_id`; `health.secret_rotation`.
    Crosswalk rows (TODO, protocol fields).
  - During an overlap, every delivery attempt carries a `webhook-signature` `v1` entry per
    active secret (newest first) and `X-TDS-Signature` with the newest; an expired secret
    signs nothing.
- Example `secret-rotation`: subscribe, rotate, list, a delivery signed with both secrets,
  retire the old one, refusal to retire the newest. Webhook-delivery manifest entries take
  `rotation: <file>`; the validator then requires a `v1` entry for the older secret too.
- `tds-mock` implements the three operations (`ProviderConfig.secret_rotation`,
  `default_overlap_s`, `max_overlap_s`, `max_active_secrets`), issues `signing_secret_id`
  (`sec-1`, `sec-2`, … per subscription), and signs each attempt with the secrets active at
  that moment (`Provider.signing_secrets`; `Dispatcher.submit` takes an optional `secrets`
  callable). The generated subscribe responses gain `signing_secret_id`.
- Core scenario `secret_rotation`: runs only when `/health` declares `secret_rotation`
  (others pass with a note), so existing providers stay conformant. Checks the one-time value
  and replay, the overlap expiry, value-free listings, retiring rules and, with a webhook
  receiver, the signatures during the overlap and after retiring.
- `tds_conform.events.delivery_problems(..., older_secrets=[...])`; `receiver.Delivery.path`.

## [0.6.0] - 2026-10-23

Event delivery that standard tooling understands, and a file format for providers without an
API. Additive for existing integrations: deliveries keep their body and `X-TDS-*` headers,
new subscription and file features are opt-in. Minor version because `X-TDS-Signature` and
`X-TDS-Timestamp` are deprecated (still sent and accepted). Generated event streams change
only in `schema_version`.

### Added
- CloudEvents rendering of webhook deliveries (SPEC.md §9.1): `POST /subscriptions` takes
  optional `cloudevents: true`; the subscription answers whether the provider will add
  CloudEvents 1.0 binary-mode headers (`ce-specversion`, `ce-id` = `event_id`, `ce-source` =
  `urn:tds:provider:<provider_id>`, `ce-type` = `org.tds-openapi.<event_type lower case>`,
  `ce-time`, `ce-subject` = `trips/<trip_id>[/legs/<leg_id>]`, `ce-dataschema` = the
  versioned event schema, extensions `ce-providerid` and `ce-schemaversion`). The body and
  `Content-Type` are unchanged. Crosswalk rows (TODO).
- AsyncAPI: `components.schemas` `DeliveryHeaders` = `SignatureHeaders` + `CloudEventsHeaders`,
  used by the `signed` message trait.
- `tds_conform.cloudevents` (`render`, `cloudevents_headers`, `cloudevents_problems`,
  `event_type_from_ce`, `dedupe_key`) and `tds_conform.events.delivery_problems` (body,
  signature and CloudEvents headers of one delivery).
- Example `webhook-delivery`: a subscription with `cloudevents: true` and one delivery as sent
  on the wire (headers and raw body). Example manifests gain `kind: webhook-delivery`; the
  validator checks the body, the headers against `DeliveryHeaders`, the signature and the
  CloudEvents mapping.
- Standard Webhooks signatures (SPEC.md §12): `webhook-id` (= `event_id`), `webhook-timestamp`
  and `webhook-signature` (space-separated `v1,<base64 HMAC-SHA256 of "id.timestamp.body">`,
  one per active secret, so secrets rotate without downtime), keyed with the decoded part of a
  `whsec_` secret. Senders SHOULD send them when the secret is `whsec_` + base64 of 24 to 64
  bytes; receivers accept a delivery when either this or `X-TDS-Signature` verifies.
  AsyncAPI `StandardWebhooksHeaders` (part of `DeliveryHeaders`).
- `tds_conform.events`: `sign_standard`, `verify_standard`, `verify_any` (the receiver rule,
  one or several secrets), `standard_key`, `new_standard_secret`, `get_header`. Tested
  against the Standard Webhooks reference test vector.
- `tds-mock` issues `whsec_` secrets and sends the Standard Webhooks headers on every
  delivery. Core scenario `webhook_delivery` checks them when sent (valid `v1` entry,
  `webhook-id` = `event_id`); the example delivery carries them.
- `tds-mock` adds the CloudEvents headers for subscriptions with `cloudevents: true`. Core scenario
  `webhook_delivery` subscribes with `cloudevents: true` and, when the provider agrees or sends
  `ce-*` headers anyway, checks them against the envelope. Providers that answer
  `cloudevents: false` pass as before.

- TDS CSV profile for batch providers without an API (SPEC.md §9.2, new `profiles/csv`):
  requests, answers and statuses files (`tds_<kind>_<UTC time>.csv`), RFC 4180, UTF-8 with
  a tolerated byte-order mark, RFC 3339 times, `|`-separated lists. Row schemas
  `csv/request-row`, `csv/answer-row`, `csv/status-row`, each column with `x-maps-to`
  (JSON Pointer per action or result). Request rows stand for `POST /trips`, confirm, `PATCH`
  and cancel, keyed on (requester, provider, trip, leg, action, revision); answer rows repeat
  the key and stand for the Trip or the Problem Details; status rows are events.
  A missing answer is pending, never a refusal or cancellation.
- `tds_conform.csvfile`: `read`, `write`, `row_to_json`, `json_to_row`, `row_key`,
  `idempotency_key`, `file_problems`, `exchange_problems`, `kind_from_name`, `file_name`,
  `columns`, `row_at`. CLI `tds-conform check-csv FILE... [--kind] [--schema-version]`.
- Example `csv-batch`: two rounds of requests and answers and a statuses file with a completed
  trip's milestones, plus JSON equivalents (`row_of:` in the manifest; the validator checks
  they equal the converted rows). Example manifests gain `kind: csv`; the validator checks
  each file and that every answer row answers a request row.
- `tds_conform.examples.load_example` / `iter_examples` return CSV files as text.

### Deprecated
- `X-TDS-Signature` and `X-TDS-Timestamp`, in favour of the Standard Webhooks headers. Senders
  still send them on every delivery and receivers still accept them; removal needs a major
  version and an RFC. Marked `deprecated: true` in the AsyncAPI header schema.

### Changed
- `subscription.signing_secret`: providers SHOULD issue `whsec_` + base64 secrets (any string
  of 32+ characters stays valid; it gets `X-TDS-Signature` only).
- Receivers dedupe on (`provider_id`, `event_id`), which equals CloudEvents (`source`, `id`);
  senders keep `event_id` unique among their own events (SPEC.md §9). `tds_conform.events.dedupe`
  uses the pair. Event ids that were already globally unique behave as before.
- `tds_conform.events.verify` matches header names case-insensitively.

## [0.5.3] - 2026-10-21

Rider-category fact codes for reporting and funder rules, and provider booking windows.
Additive: new optional fields, a new refusal code, conventions checked on examples only, and a
new Core scenario that passes with a note for a provider advertising no booking window.
Generated event streams change only in `schema_version`.

### Added
- Fact codes for rider categories and funder rules (SPEC.md §6.5.2): `DISABILITY` (counts as a
  person with a disability for program purposes; yes/no, never the condition or type),
  `LOW_INCOME` (meets the named program's income test) and `RESIDENT_<CC>_<area>`, in the US
  `RESIDENT_US_<5-digit county FIPS>` (e.g. `RESIDENT_US_26161`). Medicaid enrollment stays a
  program code. Guidance on reporting by rider category: providers derive categories from
  attested facts, a missing fact means general/unknown, and requesters send only the facts the
  receiving provider's agreement asks for.
- `tds_conform.eligibility.fact_code_problems` (SHOULD-level pattern checks for `AGE_`,
  `DISABILITY`, `LOW_INCOME` and `RESIDENT_` codes); the example validator now uses it in place
  of `age_code_problems` (kept).
- Example `rider-category-facts`: a senior and disabled county resident attested as
  `AGE_65_PLUS`, `DISABILITY`, `RESIDENT_US_26161`, with the wheelchair lift as a service
  requirement. `eligibility` schema example for residency.
- Booking windows (SPEC.md §7.1): `/health` may advertise `booking_window` (new schema
  `core/booking-window.yaml`: `max_advance_s`, `min_notice_s`, `same_day`, `time_zone`) and
  `booking_window_by_profile` (`paratransit`, `nemt`, `transfer`; replaces the default). The
  trip time is the `earliest` of the pickup window, else the dropoff window. Crosswalk rows
  (TODO, with the GTFS-Flex `booking_rules` fields borrowed).
- Refusal reason `OUTSIDE_BOOKING_WINDOW`, checked after `INVALID_WINDOW` and before any other
  refusal; Problem Details field `bookable_from` (when a too-early request can be made, as a
  new trip). Tenders are rejected with the same reason.
- `tds_conform.booking`: `booking_window_for`, `trip_time`, `opens_at`, `closes_at`,
  `booking_window_problem`, `health_booking_problems`, for requesters planning submissions
  and for providers; the example validator checks `/health` examples with it.
- `tds-mock`: `ProviderConfig.booking_window` / `booking_window_by_profile` (also `/_sim`
  policies), advertised on `/health` and enforced on `POST /trips`, tenders and `PATCH`.
- Core scenario `booking_window`: with a window advertised, a trip inside it is not refused
  for timing, one beyond `max_advance_s` is refused with `OUTSIDE_BOOKING_WINDOW` and a
  sensible `bookable_from`, and one with too little notice (or same day, when `same_day` is
  false) is refused too. Passes with a note when no window is advertised. Faulty-provider
  tests included.
- Example `booking-window` (health, a too-early request and both refusals).

### Changed
- Kit: test trips are moved forward by whole days into the provider's advertised booking
  window, so providers needing several days' notice can run every scenario.
  `run_scenario` takes an optional `health` argument (the CLI passes `/health`).
- `eligibility.program` and `rider.eligibility` descriptions state the "what, never why" rule
  for every fact code and the minimum-necessary rule.

## [0.5.2] - 2026-10-21

Eligibility attestations say how eligibility was verified and can attest a fact (an age
threshold) instead of the data behind it. Additive: new optional fields, a new refusal code and
a new Core scenario that any provider keeping trips as sent already passes.

### Added
- `eligibility.verification_method` (`STAFF_VERIFIED`, `DIGITAL_ID`, `AGENCY_DETERMINATION`,
  `SELF_DECLARED`, `OTHER`), `verified_at` (date) and `verifier_ref` (the identity platform or
  credential issuer, never the credential). Descriptions now say explicitly that no evidence,
  document number or date of birth is carried. Crosswalk rows added (TODO, with notes on the
  borrowed ISO/IEC 18013-5 and OpenID Connect for Identity Assurance concepts).
- Fact codes for `eligibility.program`: SHOULD be `AGE_<N>_PLUS` / `AGE_UNDER_<N>`
  (SPEC.md §6.5.1), alongside program codes.
- SPEC.md §6.5.1: eligibility attestations and the provider's acceptance policy (which
  `attested_by` and `verification_method` combinations it accepts, set in its agreement with
  the requester).
- Refusal reason `ELIGIBILITY_NOT_ACCEPTED`: the request carries an attestation the provider
  does not accept (vs `ELIGIBILITY_REQUIRED`: none was sent).
- `tds_conform.eligibility`: `eligibility_problems` (`verified_at` not after `valid_until`) and
  `age_code_problems` (the `AGE_` convention); the example validator applies both to trip,
  tender and profile trip examples.
- `tds-mock`: refuses an attestation with `verified_at` after `valid_until` (400), and
  `ProviderConfig.refused_verification_methods` (also a `/_sim` policy; `UNSTATED` = no method)
  refuses trips and tenders with `ELIGIBILITY_NOT_ACCEPTED`.
- Core scenario `eligibility_attestation`: a request with an `AGE_60_PLUS` / `DIGITAL_ID`
  attestation is accepted with the attestations kept as sent, or refused with an eligibility
  code; 400 fails, another refusal code is INCONCLUSIVE. Faulty-provider tests included.
- Example `eligibility-attestation` (request, offer, and the `ELIGIBILITY_NOT_ACCEPTED`
  refusal); `single-trip` attestation now states `AGENCY_DETERMINATION`.
- `docs/for-agencies.md`: ask for an attestation acceptance policy.

## [0.5.1] - 2026-10-21

ThroughRide alignment proposal "No exception type for 'a person has been asked to act'". Additive.

### Added
- Exception type `ESCALATED` (transfer profile, SPEC.md §10.6) with `escalates_exception_id` and
  `escalation_level`. The schema requires `escalates_exception_id` and severity `AT_RISK` or
  `CRITICAL` for `ESCALATED`, and refuses the escalation fields on other types. No crosswalk row:
  transfer-profile fields are not mapped to TDS 2020.
- `tds_conform.events.broker_event_problems` (exception events only from brokers) and
  `escalation_problems` (escalations name an earlier condition from the same broker, rise in
  level and are resolved with it); applied by the example validator and `tds-conform
  check-events`.
- Kit: `webhook_delivery` fails a provider that delivers `EXCEPTION_RAISED` or
  `EXCEPTION_RESOLVED`; faulty-provider test included.
- `examples/exceptions`: an exhausted tender escalated to levels 1 and 2, and an unanswered hold
  escalated and resolved with its condition. `exception` schema example for `ESCALATED`.
- `docs/for-agencies.md`: ask for escalation events.

## [0.5.0] - 2026-10-21

Settlement split, draft paratransit and NEMT profiles with conformance levels, AsyncAPI
completeness, schemathesis fuzzing and RFP guidance for agencies (SPEC.md §15 v0.5).
Additive over 0.4.2: no field removed or renamed.

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
- Conformance levels **Core + Paratransit** and **Core + NEMT** (`--profile paratransit|nemt`) with
  scenarios `paratransit_booking`, `paratransit_rules`, `nemt_booking`, `nemt_rules`; kit tests
  include a faulty provider for each. `tds-mock` declares `PARATRANSIT` and `NEMT` on `/health`
  (configurable with `ProviderConfig.profiles`) and refuses profile objects it doesn't declare.
- **AsyncAPI completeness** (SPEC.md §9): broker events `EXCEPTION_RAISED` and
  `EXCEPTION_RESOLVED` (`transfer/event-exception-*.yaml`, payload `exception`), added to the
  AsyncAPI channel, operations and `core/event-types`. The example validator now checks that
  every event type and every state an event reports (all trip statuses, cancellation by
  requester and provider, tender and hold outcomes, all milestones, all exception types) appears
  in an example event stream.
- `trip.offer_expires_at`: when an `OFFERED` trip lapses to `EXPIRED`.
- Generated example `refusals-and-failures` (trip `REJECTED`, offer `EXPIRED`, tender `REJECTED`,
  hold `DENIED`, `RIDER_NO_SHOW`, `LEG_FAILED`, cancellation by the provider) and hand-written
  `exceptions` (one `EXCEPTION_RAISED` per type, with resolutions).
- `tds-mock`: `offer_ttl_s` and `unsupported_requirements` as runtime policies;
  `POST /_sim/{provider}/trips/{trip_id}/cancel` (provider cancels).
- `tds-conform fuzz` (extra `tds-conform[fuzz]`, schemathesis 4): positive and negative
  generated requests to every operation of `dist/openapi.yaml`; checks no 5xx, documented
  status codes and media types, response schemas and headers, and rejection of invalid input.
  Writes `fuzz.json`. `POST /subscriptions` is skipped by default. CI job
  `fuzz-vs-reference-mock`; tests include faulty providers that fuzzing must catch.

### Changed
- `fare-funding.funding_source` references `core/funding-source.yaml` (same values).
- `core/event.provider_id` is the emitting party (the broker, for broker events).
- SPEC.md §8.1: `NO_SHOW` may follow `IN_PROGRESS` (vehicle at the pickup), as the mock does.
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
