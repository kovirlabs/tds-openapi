# SPEC.md — tds-openapi

Status: **Draft 3 (v0.9.0)** · Owner: Evan / Kovir Labs · Date: 2026-10-25

Field lists below are **proposed**. Names for `profiles/core` must be reconciled with TDS 2020 (TCRP Report 210) in `mappings/tds-2020-crosswalk.md`; anything marked `TODO(crosswalk)` is unverified.

---

## 1. Purpose
Demand-response and microtransit providers in the US can't book linked trips across each other's systems. TDS (2020) defines what a trip-booking exchange should contain but has no machine-readable API definition, no conformance tests and little adoption. This project supplies:

1. An OpenAPI 3.1 + AsyncAPI 3.0 **binding** of TDS.
2. **Profiles** layered on a small core (transfer, paratransit, NEMT).
3. A **conformance kit** vendors can run and agencies can cite in RFPs.
4. A **mock server** for development without a live partner.

## 2. Goals
- Implementable by a vendor engineer in days, testable in an afternoon.
- Core stays close to TDS 2020 so existing TDS pilots (DRCOG, NEORide, MnDOT) can adopt without redesign.
- Transfer profile enables **protected cross-provider connections** (DR ↔ DR), which no US deployment currently offers.
- Donatable to a neutral steward (MobilityData or Open Mobility Foundation).

## 3. Non-goals
- Not a new standard; not replacing TDS, GTFS-Flex or GTFS-RT.
- No trip planning/discovery (that's GTFS-Flex + OTP).
- No scheduling/optimization algorithms.
- No payment processing; settlement data only.
- No rider identity system; riders are referenced, not owned.

## 4. Parties and roles
| Role | Description |
|---|---|
| **Requester** | Party asking for a trip: a broker (e.g. ThroughRide), a coordinator tool, another provider. |
| **Provider** | Operates service and accepts/rejects trips (scheduling system of an agency or vendor). |
| **Broker** | Requester that composes multi-leg trips across providers and coordinates transfers. |
| **Coordinator** | Human (e.g. mobility manager, case worker) using a broker UI. Not an API party. |

A system may play several roles. All parties expose the same API surface for the operations they support.

A **broker proxy** may expose the provider API on behalf of a provider that has no software of its own (phone, legacy CAD or spreadsheet dispatch), relaying requests to staff by email or SMS. A proxied provider is a provider for every purpose in this spec. It declares `operated_via: BROKER_PROXY` on `/health` and answers asynchronously, typically in minutes.

## 5. Architecture
- **Synchronous (OpenAPI):** requester → provider calls to request, confirm, modify, cancel and query trips.
- **Asynchronous (AsyncAPI):** provider → requester webhooks for status changes, milestones and ETAs. Webhook registration via subscription endpoint.
- **Discovery is out of band:** which provider serves where comes from GTFS-Flex; references use GTFS `stop_id` / Flex location IDs where available.
- **Transport:** HTTPS + JSON. Webhooks signed (HMAC-SHA256 header) and idempotent via `event_id`.
- **One direction for calls.** Requesters (brokers) call providers; providers answer synchronously or report later by event. A broker exposes only a webhook receiver, never an API of its own. This holds for tenders and holds too (§10.2, §10.5).

## 6. Core data model (`profiles/core`)

### 6.1 Trip
| Field | Type | Req | Notes |
|---|---|---|---|
| `trip_id` | string | Y | Assigned by requester. Unique per requester. |
| `provider_trip_id` | string | N | Assigned by provider on acceptance. |
| `requester_id` | string | Y | |
| `provider_id` | string | Y | |
| `status` | enum | Y | See §8.1. |
| `rider_ref` | Rider | Y | |
| `party` | object | N | `companions` (int), `attendants` (int), `service_animals` (int). |
| `legs` | Leg[] | Y | Core: exactly one leg per provider trip. Multi-leg composition is a broker concern (transfer profile). |
| `fare_funding` | FareFunding | N | |
| `notes_to_driver` | string | N | Free text, max 500. No health data. |
| `created_at`, `updated_at` | datetime | Y | |

### 6.2 Leg
| Field | Type | Req | Notes |
|---|---|---|---|
| `leg_id` | string | Y | |
| `pickup` | Location | Y | |
| `dropoff` | Location | Y | |
| `pickup_window` | TimeWindow | C | One of pickup_window / dropoff_window required. |
| `dropoff_window` | TimeWindow | C | Appointment-anchored trips use dropoff. |
| `scheduled_pickup` | datetime | N | Set on confirmation. |
| `scheduled_dropoff` | datetime | N | |
| `service_requirements` | enum[] | N | `WHEELCHAIR_LIFT`, `RAMP`, `DOOR_TO_DOOR`, `HAND_TO_HAND` (release only to a responsible person), `ATTENDANT_REQUIRED`, `NO_STEPS`, `CHILD_SEAT`. TODO(crosswalk). |
| `vehicle_ref` | object | N | `vehicle_id`, `description`, `plate` (set near pickup). |

### 6.3 Location
`lat`, `lon` (required); `address` (structured: street, city, region, postal_code, country); `gtfs_stop_id` / `flex_location_id` (optional); `name`; `access_notes` (e.g. "east entrance").

### 6.4 TimeWindow
`earliest`, `latest` (datetime, required); `preferred` (optional). Validation: earliest ≤ preferred ≤ latest.

### 6.5 Rider (reference)
`rider_ref_id` (opaque, required; the requester SHOULD issue a different, stable value per provider so providers cannot link a rider across services); `display_name` (first name / initial, optional); `contact` (phone for SMS, optional, requester-controlled consent flag `contact_consent`); `language` (BCP 47); `eligibility` (array of attestations `{program, attested_by, valid_until, verification_method, verified_at, verifier_ref}`, §6.5.1 — e.g. `ADA_PARATRANSIT`, `SENIOR_5310`, `AGE_60_PLUS`, `DISABILITY`, `RESIDENT_US_26161`, §6.5.2). **No diagnoses, DOB, or health data in core.**

#### 6.5.1 Eligibility attestations
An attestation says that a party vouches for the rider's eligibility, and how it knows; it never carries the evidence. No date of birth, document or card numbers, document images, credential tokens or diagnoses, in any field (including `verifier_ref` and Problem Details `detail`).

- `program` (required): a program code (`ADA_PARATRANSIT`, `SENIOR_5310`) or a **fact code** for what a program depends on. Age thresholds SHOULD be written `AGE_<N>_PLUS` (`AGE_60_PLUS`, `AGE_65_PLUS`) or `AGE_UNDER_<N>` (`AGE_UNDER_18`), with `N` a whole number of years and no leading zeros. Attesting the fact a program needs, instead of sending the data behind it, lets a provider decide without determining eligibility itself. Identity platforms already disclose such facts on their own: ISO/IEC 18013-5 mobile driver's licences (`age_over_NN`), OpenID Connect providers with verified claims (OpenID Connect for Identity Assurance), and verifiable credentials presented with selective disclosure (OpenID4VP, SD-JWT).
- `attested_by` (required): the party that vouches (agency, provider or broker).
- `verification_method` (optional): `STAFF_VERIFIED` (a person checked documents), `DIGITAL_ID` (identity platform, mobile ID or verifiable credential), `AGENCY_DETERMINATION` (formal eligibility determination, e.g. ADA paratransit certification), `SELF_DECLARED` (the rider's statement, unchecked), `OTHER`. Absent means not stated.
- `verified_at` (optional, date): when `attested_by` last verified it. MUST NOT be after `valid_until`.
- `verifier_ref` (optional): opaque id of the identity platform or credential issuer that verified the fact, when it is not `attested_by`. It names the verifier, never the credential.
- `valid_until` (optional, date): last day the attestation holds.

**Acceptance policy.** Each provider decides which `(attested_by, verification_method)` combinations it accepts for each program, typically in its agreement with the requester (for example: `AGE_60_PLUS` from this broker by `DIGITAL_ID` or `STAFF_VERIFIED`; `ADA_PARATRANSIT` only by `AGENCY_DETERMINATION` from the certifying agency). The binding does not carry the policy. A provider that needs an attestation the request lacks refuses with `ELIGIBILITY_REQUIRED`; one that gets an attestation it does not accept (unknown attester, method, stale `verified_at`, expired `valid_until`) refuses with `ELIGIBILITY_NOT_ACCEPTED` (422, or a rejected tender's `reason`). It does not ask for the evidence instead; if it must verify for itself, that happens outside this API. A provider MAY refuse a request whose attestation breaks the `verified_at` rule with 400 `INVALID_REQUEST`. A provider that accepts the trip keeps the attestations exactly as sent (conformance scenario `eligibility_attestation`).

#### 6.5.2 Fact codes and reporting by rider category
Fact codes say **what** is true of the rider for program purposes, never **why**. Besides the age codes above, a `program` SHOULD use these patterns:

| Fact code | Meaning | Never |
|---|---|---|
| `AGE_<N>_PLUS`, `AGE_UNDER_<N>` | At or over / under an age threshold (§6.5.1). | The birth date. |
| `DISABILITY` | The rider counts as a person with a disability for the program's purposes. Yes/no only: the code takes no qualifier. | The condition, diagnosis, type or severity (no `DISABILITY_VISUAL`, no `DISABILITY_COGNITIVE`). What the vehicle and driver must do goes in `service_requirements`. |
| `LOW_INCOME` | The rider meets the low-income test of the program the agreement names. The threshold belongs to the program, not the code. | Income, household size, benefit amounts or case numbers. |
| `RESIDENT_<CC>_<area>` | The rider lives in an area. `<CC>` is the ISO 3166-1 alpha-2 country code; `<area>` is the country's official code for the area. In the US it is the 5-digit county FIPS code (2-digit state + 3-digit county, the census GEOID), e.g. `RESIDENT_US_26161`. | The address. |

Why `RESIDENT_US_<county FIPS>`: funders restrict by county; the 5-digit code already contains the state, so it is unambiguous without an ISO 3166-2 subdivision (which stops at states and would only repeat it); it is the code public funding and census data use; and it fits the `program` pattern (`^[A-Z][A-Z0-9_]*$`). Medicaid enrollment is not a fact code: attest it with the program code (or, for NEMT, `authorization_ref`, §11).

**Reporting by rider category.** Each provider counts and reports the trips it provides, often by rider category (for example general, senior, disabled, senior and disabled). It derives the category from the attested facts it received, by its funder's definitions: senior from an `AGE_` code at or over the funder's threshold, disabled from `DISABILITY` (or from a program that implies it, such as `ADA_PARATRANSIT`, where the funder allows). Absent a fact, the rider is reported as general (or unknown, if the funder distinguishes them); a provider does not ask for evidence to fill a category. **Minimum necessary:** a requester sends only the facts the receiving provider's agreement asks for, for that trip: residency only to a provider whose funding depends on it, `LOW_INCOME` only where a program needs it. The example validator checks these patterns as a SHOULD (`tds_conform.eligibility.fact_code_problems`); example `rider-category-facts`.

### 6.6 FareFunding
`fare` (`amount`, `currency`); `funding_source` enum (`RIDER`, `SECTION_5310`, `SECTION_5311`, `MEDICAID_NEMT`, `AGENCY`, `SPONSOR`, `OTHER`); `sponsor_ref`; `authorization_ref`. TODO(crosswalk).

### 6.7 Error
RFC 9457 Problem Details (`type`, `title`, `status`, `detail`, `instance`) + `code` enum: protocol errors (`INVALID_REQUEST`, `UNAUTHORIZED`, `NOT_FOUND`, `INVALID_STATE`, `IDEMPOTENCY_KEY_REUSED`, `TRIP_ID_IN_USE`), back-pressure (`RATE_LIMITED`, `TEMPORARILY_UNAVAILABLE`, with optional `retry_after_s`, §7.2) and booking refusals (e.g. `NO_CAPACITY`, `OUTSIDE_SERVICE_AREA`, `OUTSIDE_SERVICE_HOURS`, `ELIGIBILITY_REQUIRED`, `ELIGIBILITY_NOT_ACCEPTED` (§6.5.1), `OUTSIDE_BOOKING_WINDOW` (§7.1), `INVALID_WINDOW`, `UNSUPPORTED_REQUIREMENT`). With `OUTSIDE_BOOKING_WINDOW`, optional `bookable_from` (date-time, §7.1).

## 7. Core operations (OpenAPI)
| Method | Path | Purpose |
|---|---|---|
| POST | `/trips` | Request a trip. Returns 202 with `status: REQUESTED` (decision follows by event) or 201 with the decision if synchronous. Both are fully conformant. |
| GET | `/trips/{trip_id}` | Current state. |
| POST | `/trips/{trip_id}/confirm` | Requester confirms a provider's offer (if provider returned `OFFERED`). |
| PATCH | `/trips/{trip_id}` | Modify windows/party. Provider may re-offer. |
| POST | `/trips/{trip_id}/cancel` | Cancel with `reason` enum (`RIDER_REQUEST`, `PROVIDER_UNABLE`, `DUPLICATE`, `MISSED_CONNECTION`, `OTHER`), `cancelled_by`, optional `detail` (free text, no health data). |
| POST | `/subscriptions` | Register webhook URL + event types. |
| GET | `/subscriptions` | The requester's own subscriptions, without secrets (§9.3). Optional. |
| GET | `/subscriptions/{subscription_id}` | One subscription, without its secret (§9.3). Optional. |
| PATCH | `/subscriptions/{subscription_id}` | Replace event types, callback URL or CloudEvents setting; same id and secret (§9.3). Optional. |
| DELETE | `/subscriptions/{subscription_id}` | End a subscription; no further deliveries (§9.3). Optional. |
| POST | `/subscriptions/{subscription_id}/secrets` | Rotate the signing secret: mint a new one, keep older ones for an overlap (§12.1). Optional. |
| GET | `/subscriptions/{subscription_id}/secrets` | Active signing secrets, newest first, without values (§12.1). Optional. |
| DELETE | `/subscriptions/{subscription_id}/secrets/{secret_id}` | Retire an older secret before its overlap ends (§12.1). Optional. |
| GET | `/health` | Liveness, supported profiles and spec versions, `operated_via` (`DIRECT` / `BROKER_PROXY`), `typical_response_s` (how long an asynchronous decision usually takes), `booking_window` and `booking_window_by_profile` (§7.1), `secret_rotation` (§12.1), `subscription_management` (§9.3). |

All mutating calls require `Idempotency-Key` header. Settlement endpoints are deferred; v0.5 defines the settlement split format only (§10.8).

Requesters should set deadlines (`respond_by`) no shorter than the provider's declared `typical_response_s`.

### 7.1 Booking windows
Providers accept requests only within a window before the trip: up to 14 days ahead, at least 24 hours' notice, next day only. A provider SHOULD advertise its window on `/health` as `booking_window` (`profiles/core/booking-window.yaml`), so a broker composing a multi-provider journey can plan and submit each leg when that provider's window opens:

- `max_advance_s`: furthest ahead, in seconds before the trip time; `min_notice_s`: least notice; `same_day` (default `true`): `false` means a trip must be requested by the end of the previous service day; `time_zone` (IANA): the provider's service day for `same_day`, else the offset written in the trip's window. Absent fields set no limit; `min_notice_s` ≤ `max_advance_s`.
- The **trip time** is the `earliest` of the leg's `pickup_window`, or of its `dropoff_window` when it has none. The **request time** is when the provider receives `POST /trips`, `POST /tenders` or a `PATCH` that changes a window.
- `booking_window_by_profile` replaces (does not merge with) `booking_window` for one profile: `paratransit` or `nemt` when the request carries that object, else `transfer` for a tender.
- A request outside the window is refused with `OUTSIDE_BOOKING_WINDOW` (422; a rejected tender's `reason`; a `PATCH` refusal leaves the previous terms), checked after request validation and `INVALID_WINDOW` and before any other refusal, so a requester knows that only timing is wrong. When the request came too early the Problem Details carry `bookable_from` (trip time − `max_advance_s`). As with any 422 on `POST /trips` the trip is `REJECTED`; the later request is a new trip with a new `trip_id`.
- `tds_conform.booking` (`booking_window_for`, `trip_time`, `opens_at`, `closes_at`, `booking_window_problem`) implements these rules for requesters and providers; conformance scenario `booking_window`.

### 7.2 Back-pressure (429, 503, `Retry-After`)
A provider that is overloaded, in maintenance, or limiting how fast one client may call says so and says how long to wait, so a requester backs off instead of retrying on its own schedule:

- **429** `RATE_LIMITED`: this client is over the provider's limit. **503** `TEMPORARILY_UNAVAILABLE`: the provider cannot take requests from anyone for now. Every operation may answer either; nothing was processed (no trip created, no state changed, the `Idempotency-Key` not consumed).
- The provider SHOULD send the wait both as the HTTP `Retry-After` header (RFC 9110 §10.2.3; delta-seconds, which providers SHOULD use, or an HTTP-date) and as `retry_after_s` (integer seconds) in the Problem Details, for clients that cannot read headers. When both are present they SHOULD agree; a client that sees both uses the larger. `retry_after_s` MAY also accompany any other 5xx.
- The requester SHOULD wait at least that long, then repeat the **same request with the same `Idempotency-Key`**, so a request that did get through is never applied twice. The hint is a minimum: a client may wait longer (its own backoff), never shorter. A `Retry-After` that cannot be parsed is ignored. Without a hint the client uses its own backoff.
- A 429/503 is never a refusal: it says nothing about whether the provider can serve the trip, and the trip is not `REJECTED`. A requester does not offer the trip to another provider just because of back-pressure.
- Webhook receivers (§9) may answer a delivery with 429 or 503 and `Retry-After` as well; the sender SHOULD then make its next attempt no earlier than that (keeping its own backoff when that is longer, and its own upper limit).
- `tds_conform.backpressure` (`retry_after_s(headers, body)`, `parse_retry_after`, `backpressure_problems`) reads and checks the hint. `tds-mock` answers 429 under `ProviderConfig.rate_limit` and 503 after `POST /_sim/<provider_id>/unavailable {"for_s": …}`, both with the header and the field; its webhook sender honours a receiver's `Retry-After`. The kit retries an unexpected 429/503 the same way and reports a missing or inconsistent hint as a warning (§13).

## 8. State machines

### 8.1 Trip status
```
REQUESTED → OFFERED → CONFIRMED → SCHEDULED → IN_PROGRESS → COMPLETED
     │          │          │            │             └→ FAILED
     └→ REJECTED└→ EXPIRED └────────────┴→ CANCELLED
```
`NO_SHOW` is terminal from `SCHEDULED` or, once the vehicle is at the pickup, `IN_PROGRESS`. Providers that confirm synchronously may skip `OFFERED`. A provider whose offers lapse sets `offer_expires_at` on the `OFFERED` trip; unconfirmed by then, the trip is `EXPIRED`. A provider may cancel a trip itself (`cancellation.cancelled_by: PROVIDER`, e.g. `PROVIDER_UNABLE`).

## 9. Events (AsyncAPI)
Defined in `spec/asyncapi.yaml`; payload schemas in `profiles/*/event-*.yaml`.
Envelope: `event_id`, `event_type`, `occurred_at`, `trip_id`, `leg_id`, `provider_id`, `payload`, `schema_version`.
Core event types: `TRIP_STATUS_CHANGED` (every status change), `TRIP_SCHEDULED` (committed times or vehicle set or changed), `TRIP_CANCELLED` (sent with the status change, carries who cancelled and why).
Transfer event types: `TENDER_RESPONSE`, `HOLD_RESPONSE`, `MILESTONE`, `ETA_UPDATE`, `CUSTODY_HANDOFF`.
Broker event types: `EXCEPTION_RAISED`, `EXCEPTION_RESOLVED` (§10.6), sent by a broker to its own subscribers (coordinator tools, agency systems), never by providers; `provider_id` is then the broker's id.
- **Completeness.** Every state change has a typed event: trip status (`TRIP_STATUS_CHANGED`, all statuses), schedule and vehicle (`TRIP_SCHEDULED`), cancellation by either side (`TRIP_CANCELLED`), tender and hold outcomes, milestones, ETA bands, custody, and exceptions. The example validator fails unless every event type and every state those events report appears in an example event stream.
- Providers emit an event for **every** change, including changes the requester already saw in a synchronous response, so a receiver's event log is complete.
- Delivery: one event per HTTPS POST to the subscription's `callback_url`; at least once, retries with exponential backoff (no sooner than a receiver's `Retry-After` on 429/503, §7.2); receivers return 2xx and dedupe on (`provider_id`, `event_id`); order by `occurred_at`. A sender keeps `event_id` unique among its own events and repeats it on every retry. Plain `http://` callbacks are allowed only for loopback addresses (development and the conformance kit).

### 9.1 CloudEvents rendering (optional)
A subscriber that routes events through CloudEvents tooling (event buses, integration platforms) asks for `cloudevents: true` in `POST /subscriptions`. The provider answers `cloudevents: true` if it will add the headers, else `false`; the subscription is created either way. Every delivery to such a subscription carries CloudEvents 1.0 **binary content mode** headers (CloudEvents HTTP protocol binding). The body stays the TDS event byte for byte and `Content-Type` stays `application/json`, so receivers that ignore the headers lose nothing. A provider MAY add the headers to any subscription.

| CloudEvents attribute | Header | Value |
|---|---|---|
| `specversion` | `ce-specversion` | `1.0` |
| `id` | `ce-id` | `event_id` |
| `source` | `ce-source` | `urn:tds:provider:` + `provider_id` (percent-encoded as a URI component) |
| `type` | `ce-type` | `org.tds-openapi.` + `event_type` in lower case, e.g. `org.tds-openapi.milestone`; upper-casing the suffix gives the event type back |
| `time` | `ce-time` | `occurred_at` |
| `subject` | `ce-subject` | `trips/<trip_id>` or `trips/<trip_id>/legs/<leg_id>` (ids percent-encoded); absent when the event has no `trip_id` |
| `dataschema` | `ce-dataschema` | the event type's schema `$id` with `schema_version` inserted after `/schemas/`, e.g. `https://tds-openapi.org/schemas/0.6.0/core/event-trip-status-changed.yaml` |
| `datacontenttype` | `Content-Type` | `application/json` (binary mode never sends `ce-datacontenttype`) |
| extension `providerid` | `ce-providerid` | `provider_id` |
| extension `schemaversion` | `ce-schemaversion` | `schema_version` |

- Either every header above is sent (`ce-subject` only with a `trip_id`) or none. Values are percent-encoded as the HTTP binding requires (space, `"`, `%` and anything outside printable ASCII).
- Extension names follow CloudEvents' rule (lower-case letters and digits only), hence `providerid`, not `provider_id`.
- Deduplication is the same either way: CloudEvents (`source`, `id`) is (`provider_id`, `event_id`).
- Signing does not change (§12): CloudEvents defines no payload signature, and the signature covers the body, which is the same in both renderings.
- The `urn:tds:` namespace and the `org.tds-openapi` type prefix follow the placeholder schema domain and change with it (§16).
- Structured mode (`application/cloudevents+json`, the envelope inside a CloudEvents object) is not defined; it would change the body.

### 9.2 Batch files (CSV profile, `profiles/csv`)
For providers without an API. Many already exchange trip files with brokers over SFTP, each broker with its own layout; this profile gives them one layout that maps row for row to the API objects, so a broker can run both and a provider can move to the API later without changing meaning. It is a rendering of core (plus transfer milestones), not a separate model.

- **Files.** `tds_requests_<YYYYMMDDTHHMMSSZ>[_<tag>].csv` (requester → provider, `request-row.yaml`), `tds_answers_…csv` (provider → requester, `answer-row.yaml`), `tds_statuses_…csv` (provider → requester, `status-row.yaml`). The time is when the file was written, in UTC. The optional tag (1–40 letters, digits or `-`, e.g. a sequence number or a random suffix) tells apart files written in the same second; a name, once used, is never reused for other content. The parties are in every row, so one transport folder can serve several.
- **Format.** RFC 4180: one header row; comma separator; double quotes around any field containing a comma, quote or line break; CRLF line ends (readers also accept LF). UTF-8; writers write no byte-order mark, readers strip one. Columns are matched by header name in any order; readers ignore unknown columns. An empty cell is an absent value. Numbers use `.` as decimal point and no thousands separator; booleans are `true`/`false`; lists are `|`-separated; times are RFC 3339 with offset; coordinates are WGS84 decimal `lat`/`lon`; enums are the API's `UPPER_SNAKE_CASE` codes.
- **Request rows.** One action on one leg: `REQUEST` (`POST /trips`), `CONFIRM` (`POST /trips/{trip_id}/confirm`), `MODIFY` (`PATCH /trips/{trip_id}`; the three party columns together replace the party) or `CANCEL` (`POST /trips/{trip_id}/cancel`). The idempotency key is (`requester_id`, `provider_id`, `trip_id`, `leg_id`, `action`, `revision`); `revision` (default 1) tells apart repeated actions of one kind, such as a second `MODIFY`. Rows apply in file order. A resent row with the same key is applied once; the same key with different content is an error. A bridge that replays rows as API calls uses `trip_id/leg_id/action/revision` (each part percent-encoded) as the `Idempotency-Key`.
- **Answer rows.** One per request row, repeating its key, with `result` `ACCEPTED` or `REJECTED`, the trip `status` after the answer, and `answered_at`. `ACCEPTED` carries what the API's Trip would (`provider_trip_id`, required for an accepted `REQUEST`; `scheduled_pickup`, `scheduled_dropoff`, `offer_expires_at`); `REJECTED` carries `reason`, a refusal reason or `INVALID_STATE`/`NOT_FOUND`, as the API's Problem Details `code`. A request row with no answer yet is **pending**: neither side treats a missing answer, or a trip missing from a later file, as a refusal or a cancellation. Files are deltas, never snapshots.
- **Status rows.** One event per row (§9: `TRIP_STATUS_CHANGED`, `TRIP_SCHEDULED`, `TRIP_CANCELLED`, and transfer `MILESTONE`), deduplicated on (`provider_id`, `event_id`) like webhook deliveries, with the same completeness rule: a row for every change, including ones already given in an answer file. Milestone rows follow §10.3 and record actual pickup and dropoff times.
- **Mapping.** Each column's `x-maps-to` gives its JSON Pointer in the API object, per action (request rows) or result (answer rows). `tds_conform.csvfile` converts both ways (`row_to_json`, `json_to_row`) and checks files (`file_problems`, `tds-conform check-csv`): name, encoding, header, cell types, row schemas, the API objects the rows stand for, reused keys, and milestone order.
- **Not carried** (use the API): eligibility attestations, the fare amount, rider e-mail, tenders, holds and custody records.
- **Privacy.** Files can hold rider contact details and addresses: move them over an encrypted, authenticated channel (SFTP, HTTPS), not as e-mail attachments, and delete them when processed. Readers treat every cell as data; a spreadsheet must not evaluate a cell starting with `=`, `+`, `-` or `@` as a formula.
- Conventions borrowed: RFC 4180, the column-mapped trip files NEMT brokers and scheduling systems already exchange, and GTFS's practice of CSV with documented columns and UTF-8.

### 9.3 Managing subscriptions (optional)
A subscriber changes what it receives without a second subscription. Subscribing again would give it a second `subscription_id` and signing secret, and every event both subscriptions cover would arrive twice; replaying `POST /subscriptions` with its old `Idempotency-Key` only returns the old answer. Optional for providers; one that implements it declares `subscription_management: true` on `/health`. All four operations need scope `events:subscribe` and act only on the authenticated requester's own subscriptions (any other is `NOT_FOUND`). The signing secret is returned only when a subscription is created (and by secret rotation, §12.1), never by these operations.

- **Read.** `GET /subscriptions/{subscription_id}` returns the subscription (`subscription.yaml`) without `signing_secret`. `GET /subscriptions` returns the requester's current subscriptions, oldest first (`subscription-list.yaml`), e.g. to find duplicates made before this was available.
- **Change.** `PATCH /subscriptions/{subscription_id}` (`Idempotency-Key` required; body `subscription-update.yaml`, at least one of `event_types`, `callback_url`, `cloudevents`) replaces the fields it carries and keeps the others. `event_types` is replaced as a whole: to add a type, send the current list plus the new one. The same rules as creation apply (loopback-only `http://`; a provider that cannot add CloudEvents headers answers `cloudevents: false`). It answers 200 with the subscription, `updated_at` set to the time of the change and no `signing_secret`. The `subscription_id`, signing secrets and `signing_secret_id` do not change, so deliveries keep verifying with the secret the subscriber holds; a new secret comes only from rotation (§12.1). Events raised after the answer follow the new settings; a delivery of an earlier event MAY still be attempted under the old ones, retries included. A replay with the same key returns the same answer.
- **End.** `DELETE /subscriptions/{subscription_id}` (`Idempotency-Key` required) answers 200 with the subscription as it was, without `signing_secret`. No delivery attempt to it starts after the answer, retries of earlier events included; its secrets are no longer used, and reading, changing or ending it again (or its secret operations, §12.1) is `NOT_FOUND`. A replay with the same key returns the same answer.
- A provider without the capability answers these routes with 404 `NOT_FOUND`; its subscribers change event types out of band, or subscribe again and accept duplicates until the old subscription is removed.
- Changing a subscription raises no event. Webhook platforms commonly let an endpoint's event filter and URL be edited in place; the binding adds the rule that the secret survives the change.

### 9.4 Relay mailbox for CSV files (optional, `spec/relay-mailbox.yaml`)
§9.2 says what goes in the files, not how they move. Many provider schedulers cannot accept connections: they sit behind an agency firewall, or run inside a platform such as Google Apps Script that cannot serve requests or run an SFTP client. For them the **requester** keeps a mailbox per provider, and a small agent beside the scheduler polls it over HTTPS, calling out only. Defining the mailbox here lets one agent work with any requester. It is a separate OpenAPI document, `spec/relay-mailbox.yaml`, because the roles are reversed: the requester serves it and the provider's agent is the client. Schemas are in `profiles/csv/` (`mailbox-listing.yaml`, `mailbox-file.yaml`, `mailbox-ack.yaml`, `mailbox-receipt.yaml`); example `examples/relay-mailbox/`.

- **Base URL and credential.** The requester gives the provider's staff the mailbox base URL (HTTPS) and a token, out of band; the agent sends `Authorization: Bearer <token>` on every call, and MAY send `X-Relay-Agent: <name>/<version>`. The token names one provider's mailbox and reaches nothing else; the provider is taken from the token, never from the request. Missing, invalid or non-mailbox credentials get 401 `UNAUTHORIZED` (a requester MAY answer 403 with the same code for a valid credential that is not a mailbox token). Errors are Problem Details (§6.7), as in the API.
- **`GET /outbox`** lists the request files not yet acknowledged, oldest first, each with `name`, `size`, lowercase-hex `sha256` and `created_at`, plus the mailbox's `provider_id`, `poll_interval_s` and, optionally, `max_file_bytes` (at least 1 000 000). Every authorised call counts as a poll.
- **`GET /outbox/{file_name}`** returns a request file's bytes unchanged (`text/csv`) with `X-Relay-SHA256`, the SHA-256 of the body. Reading changes nothing; a file stays readable after its acknowledgement until the requester deletes it. Unknown names are 404 `NOT_FOUND`.
- **`POST /ack/{file_name}`** marks the file taken; it leaves the listing. Idempotent: a repeat answers 200 with the first `acked_at`. Unknown names are 404 `NOT_FOUND`.
- **`PUT /inbox/{file_name}`** stores an answers or statuses file; the body is the raw bytes (`Content-Type: text/csv`). Write-once by name: a new name is stored (201, `stored: true`), the same bytes under a stored name are a retry (200, `stored: false`, applied once), other bytes under a stored name are refused with 409 `FILE_NAME_TAKEN` and never overwrite. The receipt carries the stored bytes' `sha256`. Refusals: 400 `INVALID_FILE_NAME` (a name that uses characters other than letters, digits, `_`, `-` and `.`, or is not an answers or statuses name per §9.2, which includes any requests file), 400 `EMPTY_FILE`, 413 `FILE_TOO_LARGE` (over `max_file_bytes`). A mailbox MUST refuse names that are not CSV profile answers or statuses names and SHOULD refuse every name not exactly per §9.2.
- **No `Idempotency-Key`.** Every call is safe to repeat as it stands: reads change nothing, acknowledgements are idempotent and the file name is the upload's key.
- **Agent behaviour.** Download, check the bytes against `X-Relay-SHA256` and the listed `sha256`, save the file where the scheduler reads it, and only then acknowledge it. A crash before the acknowledgement means the file is listed again on the next poll; an agent that already has a file with that name and SHA-256 acknowledges it without saving it twice, and one that has other bytes under the name does not acknowledge it and reports a problem. A checksum mismatch is not saved or acknowledged and is fetched again next poll. Export new content under a new name, never a reused one. 401/403 needs a person; 400, 404, 409 and 413 will not change on retry (set the file aside); no answer, 408, 429 and 5xx are retried, after `Retry-After` / `retry_after_s` when given (§7.2).
- **Poll interval.** Agents SHOULD poll about every `poll_interval_s`, and no more often apart from retries. A requester MAY report an agent as silent after three intervals without a call.
- **Content.** The mailbox stores bytes; it does not answer for their content. The requester reads each stored file per §9.2 (rows keyed and applied once); a file whose rows it cannot use is handled as for any other §9.2 transport, out of band.
- **Privacy.** Request files carry the rider data §9.2 allows, so: HTTPS only; neither side logs file contents or the token; the requester SHOULD keep files encrypted at rest and delete them a short time after they are acknowledged (outbox) or read (inbox), and delete files nobody collects after a longer period; the agent SHOULD delete its local copies once the scheduler has imported them.
- Conventions borrowed: the store-and-forward mailbox that EDI value-added networks and AS2 gateways run for trading partners that cannot receive connections, with poll, download and acknowledge; SHA-256 checksums as in RFC 9530 digests, sent as a plain hex header because some agent platforms cannot read structured fields.

## 10. Transfer profile (`profiles/transfer`)
Enables protected connections between legs run by different providers. Patterns borrowed from freight visibility are documented in `mappings/logistics-patterns.md`.

### 10.1 Interchange (`interchange.yaml`)
Links an inbound leg to an outbound leg.
`interchange_id`, `from` (`provider_id`, `trip_id`, `leg_id`), `to` (same), `transfer_location` (Location), `min_connection_s`, `max_hold_s`, `auto_hold_s`, `accessibility_buffer_s`, `protection` enum (`PROTECTED`, `BEST_EFFORT`), `supervision_required` (bool). Semantics borrowed from NeTEx interchange.
- `max_hold_s`: the longest the outbound provider can be *asked* to wait (§10.5).
- `auto_hold_s` (≤ `max_hold_s`, default 0): how long the outbound vehicle waits for a late feeder *without being asked* (rail-style standard waiting time). It is a provider commitment, so it binds only once the outbound provider echoes it in its tender `ACCEPT` (§10.2).
- `accessibility_buffer_s` is added to `min_connection_s` only when either leg has `service_requirements`; otherwise it is ignored.
- If either leg has `HAND_TO_HAND` and `protection: PROTECTED`, `supervision_required` MUST be true. A provider refuses a tender that breaks this (400 `INVALID_REQUEST`), as it does one with `auto_hold_s` > `max_hold_s`.

### 10.2 Tender (`tender-request.yaml`, `tender.yaml`, `accepted-terms.yaml`, `fallback-policy.yaml`)
Broker offers a leg to a provider with a deadline (EDI 204/990 pattern).
- `POST /tenders` (scope `tenders:write`): `tender_id`, `trip` (a TripRequest), `interchange` (the full interchange, so the provider sees the terms and which end it serves), `respond_by`.
- States: `OFFERED → ACCEPTED | REJECTED | EXPIRED`. The provider answers in the response (201 `ACCEPTED`/`REJECTED`) or later (202 `OFFERED`, then a `TENDER_RESPONSE` event); `GET /tenders/{tender_id}` shows the current state. Unanswered past `respond_by`, the tender is `EXPIRED`.
- `ACCEPTED` creates the trip (status `CONFIRMED`) under `trip.trip_id` and carries `accepted_terms` (`auto_hold_s`, `max_hold_s`): what the provider commits to, possibly less than asked. The broker plans with these. `REJECTED` carries a `reason` (RefusalReason).
- Fallback (broker-side policy, published for transparency): ordered provider list, max attempts, per-attempt deadline, `approval` (`REQUIRED`: a human approves each fallback tender or `REPROTECT`; `PRE_AGREED`: the broker may send them automatically under a signed agreement), escalate to coordinator on exhaustion.

### 10.3 Milestones (`milestone.yaml`, `event-milestone.yaml`)
Fixed event vocabulary instead of raw GPS (EDI 214 pattern). Event `MILESTONE` with `milestone` enum:
`EN_ROUTE_TO_PICKUP`, `ARRIVED_PICKUP`, `RIDER_ONBOARD`, `EN_ROUTE_TO_DROPOFF`, `ARRIVED_DROPOFF`, `RIDER_HANDED_OFF`, `LEG_COMPLETED`, `RIDER_NO_SHOW`, `LEG_FAILED`.

Milestones always refer to the leg's **own** pickup and dropoff. There are no transfer-specific milestones: the interchange already says which end of a leg is a hub, so a feeder provider need not know its trip is part of a transfer. Brokers and driver UIs may still *label* a hub arrival "arrived at transfer". One sequence covers single, feeder and onward legs:

```
EN_ROUTE_TO_PICKUP → ARRIVED_PICKUP → RIDER_ONBOARD → EN_ROUTE_TO_DROPOFF → ARRIVED_DROPOFF → [RIDER_HANDED_OFF] → LEG_COMPLETED
         └──────────────┴→ RIDER_NO_SHOW (from ARRIVED_PICKUP)        any non-terminal → LEG_FAILED
```
`EN_ROUTE_TO_PICKUP` may be omitted (vehicle already at pickup). `RIDER_HANDED_OFF` is required when custody applies (§10.7) and otherwise optional. `LEG_FAILED` may carry `delay_cause` (§10.4).
Valid sequence enforced by conformance (`milestone_sequence`). Live vehicle position is an **optional** separate event, off by default.

### 10.4 ETA band (`eta-band.yaml`, `delay-cause.yaml`, `event-eta-update.yaml`)
Event `ETA_UPDATE`: `target` (`PICKUP`/`DROPOFF`, the leg's own ends as in §10.3), `earliest`, `likely`, `latest`, `confidence` (0–1, probability arrival ≤ `latest`), `computed_at`, and optionally:
- `source`: `VEHICLE_POSITION`, `PROVIDER_FORECAST`, `DISPATCHER`, `DRIVER`, `SCHEDULE` (promised time only). Brokers may poll sooner when the source is weak.
- `delay_cause`: `TRAFFIC`, `BOARDING`, `VEHICLE`, `WEATHER`, `PRIOR_TRIP`, `UNKNOWN`. Codes only, no free text, and no rider-attributed cause (avoids health explanations). Whether a cause shifts cost is for inter-provider agreements, not this spec.
Heartbeat: the provider of an interchange's **from** (feeder) leg sends `ETA_UPDATE` at least every `heartbeat_s` (an interchange field, default 60) while that leg is `IN_PROGRESS` and before `ARRIVED_DROPOFF`, if the interchange is `PROTECTED`; stale after `2 × heartbeat_s`. A feeder learns its role from the tender's interchange. ETA bands never predict an arrival earlier than now.

### 10.5 Hold request (`hold-request.yaml`, `hold.yaml`)
Broker → outbound provider when inbound ETA threatens the connection.
`POST /trips/{trip_id}/hold`: `hold_id`, `interchange_id`, `action` (`HOLD`: wait until `requested_until`; `RETIME`: move the pickup to `requested_until`), `requested_until`, `respond_by`, `inbound_eta` (ETA band).
Answered in the response (201 `GRANTED`, `PARTIAL` or `DENIED` + reason) or later (202 `PENDING`, then a `HOLD_RESPONSE` event); `GET /trips/{trip_id}/holds/{hold_id}` shows the state. `GRANTED` and `PARTIAL` carry `scheduled_pickup` and optionally `held_until`, update the trip and emit `TRIP_SCHEDULED`. A provider counter-offer ("can't wait that long, can pick up at 14:35") is `PARTIAL` with the new `scheduled_pickup`. No response by `respond_by` = `EXPIRED`; the broker raises `HOLD_UNANSWERED`. Escalation steps (who is contacted, when) are broker-local.
Only the outbound leg of the named interchange can be held, and only before `RIDER_ONBOARD`.
**Re-protecting** with another provider is not a hold action (the outbound provider cannot act on it): the broker cancels the outbound trip with `MISSED_CONNECTION` and tenders the leg elsewhere, subject to the fallback `approval` mode (§10.2).

### 10.6 Exceptions (`exception.yaml`)
Broker-generated alerts for coordinators (manage by exception). Not required of providers; defined so broker UIs and integrations share vocabulary.
`exception_id`, `severity` (`INFO`/`AT_RISK`/`CRITICAL`), `type` (`LOW_CONNECTION_PROBABILITY`, `TENDER_EXPIRED`, `TENDER_EXHAUSTED`, `STALE_HEARTBEAT`, `MISSED_MILESTONE`, `HOLD_DENIED`, `HOLD_UNANSWERED`, `CUSTODY_GAP`, `ESCALATED`), `interchange_id`, `detected_at`, `suggested_actions`, `resolved_at`, and for `ESCALATED` `escalates_exception_id` and `escalation_level`.
`ESCALATED` (v0.5.1) says a condition was not handled in time and the broker has moved it up its own escalation ladder: a person is now acting, so subscribers should not start a second response. It names the condition exception it escalates (never another `ESCALATED` one), has severity `AT_RISK` or higher, and carries the ladder step (`escalation_level` ≥ 1, broker-defined) but never who was contacted. Escalating further raises a new `ESCALATED` for the same condition with a higher level. Resolving the condition resolves its escalations (`EXCEPTION_RESOLVED` for each). Only brokers raise exceptions; a provider that sends `EXCEPTION_*` events fails `webhook_delivery`.
Default trigger: connection probability < 0.8 → `AT_RISK`; < 0.5 → `CRITICAL`.
A broker that offers webhooks to its own subscribers sends `EXCEPTION_RAISED` when it raises one and `EXCEPTION_RESOLVED` (same `exception_id`, `resolved_at` set) when it closes; example `examples/exceptions/`.

### 10.7 Custody (`custody-handoff.yaml`, `custody-party.yaml`)
Required when `supervision_required: true`.
Event `CUSTODY_HANDOFF`: `handoff_from` (party + role: `DRIVER`, `ATTENDANT`, `CAREGIVER`, `FACILITY_STAFF`), `handoff_to`, `timestamp`, `location`, `method` (`DRIVER_CONFIRM`, `ATTENDANT_CONFIRM`, `RIDER_SELF_CONFIRM`, `CODE_EXCHANGE`), `caregiver_notified` (bool).
A leg is supervised when its `service_requirements` include `HAND_TO_HAND` or its interchange has `supervision_required`. A supervised leg cannot reach `LEG_COMPLETED` without a custody record at pickup (by `RIDER_ONBOARD`) and at dropoff (with `RIDER_HANDED_OFF`) (conformance: `custody_required`). At a hub, both drivers' providers record the same physical handoff; that corroboration is intended. Parties are identified by role (and optionally provider and an opaque `party_ref`), never by name.

### 10.8 Settlement split (`settlement-split.yaml`)
After-trip reconciliation of a linked journey, produced by the broker once every leg is terminal and shared with the providers and funders involved. Data only: no payment is processed.
`split_id`, `requester_id`, `service_date`, `interchange_ids`, `total` (Money), `created_at`, and one line per tendered leg (cancelled ones included): `leg` (`provider_id`, `trip_id`, `leg_id`), `trip_status` (terminal), `cost` (Money; `"0.00"` when nothing is owed), `funding_source`, optional `sponsor_ref`, `authorization_ref`, `cancellation_reason`, `delay_cause`.
- `funding_source` (`core/funding-source.yaml`, shared with `fare_funding`): `RIDER`, `SECTION_5310`, `SECTION_5311`, `MEDICAID_NEMT`, `AGENCY`, `SPONSOR`, `OTHER`. Legs of one journey may have different funders.
- One currency throughout; `total` is the sum of the lines; each leg once (checked by the example validator and `tds_conform.settlement`).
- `delay_cause` is recorded as a fact. Who bears the cost of a late or failed leg is for inter-provider agreements, not this spec (§10.4).
- No rider data: lines identify riders only through the trips they reference. `authorization_ref` is a reference, never clinical content.
- Exchange is not yet an API operation (§7); the schema fixes the format so brokers and funders can exchange files now.

## 11. Other profiles (drafts)
Both profiles extend `POST /trips`: the body is a core TripRequest plus one profile object (`paratransit` or `nemt`). A requester sends the object only to providers whose `/health` lists the profile; a provider that lists it validates the object and refuses (400 `INVALID_REQUEST`) a request that breaks the rules below. The core rules of §12 apply unchanged: needs are leg `service_requirements` (expressing what the vehicle and driver must do, never why), riders are pairwise references, eligibility and authorization are attestations or references, never evidence. Conformance levels **Core + Paratransit** and **Core + NEMT** (§13): `paratransit_booking` / `nemt_booking` (the profile is declared on `/health`, a valid request is accepted and the profile object is kept exactly as sent, nothing added) and `paratransit_rules` / `nemt_rules` (requests breaking the rules below, or for NEMT carrying extra fields or free text in references, are refused with 400). `tds-mock` implements both; `tds_conform.profiles.profile_problems()` holds the cross-field rules.

### 11.1 Paratransit (`profiles/paratransit/`)
`paratransit`: `eligibility_category` (`UNCONDITIONAL`, `CONDITIONAL`, `TEMPORARY`; ADA paratransit categories), `mobility_devices` (`mobility-device.yaml`: `type`, `width_cm`, `length_cm`, `occupied_weight_kg`, `rider_transfers_to_seat`), `pca_authorized`.
- `rider_ref.eligibility` must include program `ADA_PARATRANSIT` (the attestation; core §6.5).
- A wheeled device the rider stays in needs `WHEELCHAIR_LIFT` or `RAMP` on the leg.
- `party.attendants` > 0 needs `pca_authorized: true`.
- Conditional eligibility carries no conditions; a provider that must know whether a trip qualifies asks the attesting agency. TODO(crosswalk): compare with TDS 2020 eligibility elements.

### 11.2 NEMT (`profiles/nemt/`)
`nemt` (closed object, no free text): `authorization` (`authorization_ref`, `payer_ref`, `valid_from`, `valid_until`; closed, references constrained to `[A-Za-z0-9._:-]`), `level_of_service` (`AMBULATORY`, `WHEELCHAIR`, `STRETCHER`: the vehicle class the payer authorized), optional `mobility_devices`, `will_call`.
- Minimum necessary: no diagnosis, condition, procedure, visit type, medication or member id anywhere in the request. The payer resolves what it needs from `authorization_ref`. Free-text core fields (`notes_to_driver`, `cancel-request.detail`) must not carry health information (§6, §7).
- `level_of_service` and `service_requirements` must agree: `WHEELCHAIR` needs `WHEELCHAIR_LIFT` or `RAMP`; `STRETCHER` needs the core requirement `STRETCHER`, and only `STRETCHER` may have it. Size and weight go in `mobility_devices`, not in a level.
- The leg's service date must fall within the authorization's validity; `fare_funding.authorization_ref`, if set, equals `nemt.authorization.authorization_ref` (use `funding_source: MEDICAID_NEMT` when Medicaid pays).
- `will_call`: the pickup time is not fixed (typical for returns); the pickup window is the earliest expected one, not a commitment.
- Values are a draft; payer vocabularies differ.

## 12. Security and privacy
- OAuth 2.0 client credentials; mTLS optional. Scopes: `trips:write`, `trips:read`, `events:subscribe`, `tenders:write` (tenders are sent by brokers); holds use `trips:write`.
- Webhook signing follows **Standard Webhooks** (standardwebhooks.com), with the earlier `X-TDS-*` headers kept alongside:
  - `webhook-id` = `event_id` (the same on every retry), `webhook-timestamp` = Unix seconds when this attempt was sent, `webhook-signature` = space-separated entries `v1,<base64 HMAC-SHA256 of "<webhook-id>.<webhook-timestamp>.<raw body>">`.
  - The subscription's `signing_secret` SHOULD be `whsec_` + standard base64 of 24 to 64 random bytes; `v1` signatures are keyed with the decoded bytes, so Standard Webhooks libraries verify them unchanged. A sender SHOULD send the three headers whenever the secret has that form.
  - Secret rotation: while more than one secret is active, the sender adds one `v1` entry per secret, newest first, so receivers switch without downtime. New secrets are issued over the API (§12.1).
  - `X-TDS-Timestamp` (Unix seconds) and `X-TDS-Signature` (`sha256=` + lowercase hex HMAC-SHA256, keyed with the UTF-8 bytes of the whole secret, of `<timestamp>.<raw body>`, newest secret) are **deprecated since 0.6.0**. Senders still send them on every delivery; removing them needs a major version and an RFC.
  - Receivers MUST accept a delivery when `webhook-signature` holds a valid `v1` entry for one of their secrets or `X-TDS-Signature` is valid, with the matching timestamp no more than 300 s from their clock, and MUST reject it otherwise. Compare signatures in constant time; ignore entries with versions they do not know (such as `v1a`, Ed25519, which this binding does not define). A receiver that checks only one set SHOULD check `webhook-signature` when present, since it also binds the event id.
  - CloudEvents (§9.1) defines no payload signing, so these headers apply to both renderings.
- Data minimization: share only what the receiving provider needs for that leg. Rider contact shared only with `contact_consent: true`.
- Retention guidance in docs (not enforced by spec).

### 12.1 Rotating a subscription's signing secret
A subscriber gets a new secret without a second, out-of-band hand-over. Optional for providers; one that implements it declares `secret_rotation: true` on `/health` and returns `signing_secret_id` with each new subscription. All three operations need scope `events:subscribe` and act only on the authenticated requester's own subscriptions (any other is `NOT_FOUND`).

- **Rotate.** `POST /subscriptions/{subscription_id}/secrets` (`Idempotency-Key` required; body optional, `secret-rotation-request.yaml`) mints a new secret and answers 201 with the active secrets, newest first (`subscription-secret-list.yaml`). The first entry is the new secret and the only place its value (`signing_secret`) is ever returned; a replay with the same key returns the same value, so a lost response never strands a secret.
- **Overlap.** Every older active secret gets `expires_at` = now + `overlap_s`, unless it already expires sooner. `overlap_s` absent: the provider's default, which SHOULD be 86400 (24 h); `0` retires older secrets at once (a leaked secret). A provider MAY shorten `overlap_s` to its own maximum (which SHOULD be at least 86400) and MAY refuse a rotation with 409 `INVALID_STATE` while it holds its limit of active secrets (it SHOULD allow at least 3).
- **Signing.** Each delivery attempt is signed with the secrets active when it is sent: one `webhook-signature` `v1` entry per active `whsec_` secret, newest first, and `X-TDS-Signature` with the newest. From an older secret's `expires_at` on, no signature uses it, retries included.
- **List.** `GET /subscriptions/{subscription_id}/secrets` returns the active secrets with `secret_id`, `created_at` and `expires_at`, never values.
- **Retire early.** `DELETE /subscriptions/{subscription_id}/secrets/{secret_id}` (`Idempotency-Key` required) sets an older secret's `expires_at` to now and returns it (200, no value). The newest secret cannot be retired (409 `INVALID_STATE`; rotate first). Retiring an expired secret changes nothing.
- **Receiver procedure.** Rotate; add the new secret to the secrets you verify with (`verify_any` takes several); deploy; then retire the old one, or let it expire. During the overlap a delivery verifies with either secret.
- Patterned on Standard Webhooks and webhook platforms that keep the previous secret valid for a period after rotation; the binding adds the overlap and early retirement to the API so either side can see when the old secret stops working.

## 13. Versioning and conformance
- SemVer on the spec. `schema_version` in every event; `/health` lists supported versions and profiles.
- Conformance levels: **Core**, **Core + Transfer**, **Core + Paratransit**, **Core + NEMT** (`tds-conform run --profile core|transfer|paratransit|nemt`). A run produces an HTML/JSON report a vendor can attach to an RFP response. A level is met only if every scenario for it passes; INCONCLUSIVE (the provider refused the test trip) does not count as a pass.
- The kit ships with a stateful reference provider (`tds-mock`) that passes every scenario; it is the executable reading of this spec where prose is ambiguous.
- Conformance scenarios (initial): `book_confirm_cancel`, `modify_window`, `idempotency`, `eligibility_attestation`, `booking_window`, `webhook_delivery`, `secret_rotation`, `subscription_management`, `tender_deadline`, `milestone_sequence`, `eta_heartbeat_staleness`, `hold_request`, `custody_required`; profiles: `paratransit_booking`, `paratransit_rules`, `nemt_booking`, `nemt_rules`; relay mailbox (§9.4, requester side): `relay_mailbox_upload`, `relay_mailbox_download`.
- Synchronous (201) and asynchronous (202 → later `OFFERED`/`REJECTED`) decisions are equally conformant. Timing checks use the provider's declared `typical_response_s`, not a fixed number of seconds; a broker-proxied provider is tested the same way (the kit may stand in for its staff).
- `secret_rotation` checks only providers that declare `secret_rotation: true` (others pass with a note): the new value is returned once and replayed under the same key, the old secret expires within `overlap_s`, listings carry no values, the newest secret cannot be retired, and, with a webhook receiver, deliveries during the overlap carry `v1` entries for both secrets and none for a retired one.
- `subscription_management` checks only providers that declare `subscription_management: true` (others pass with a note): reading and listing carry no secret; `PATCH` replaces `event_types` as a whole, keeps the id and `signing_secret_id`, sets `updated_at`, replays under the same key and refuses an empty change; `DELETE` replays, then reading, changing and ending again are `NOT_FOUND` and the subscription is no longer listed. With a webhook receiver: after the change only the new event types arrive and they verify with the secret from subscribing; after ending, nothing arrives (a second subscription shows when the trip's events have gone out).
- Scenarios that need a vehicle to move (`milestone_sequence`, `custody_required`, `eta_heartbeat_staleness`) cannot be triggered on a live provider; they are checked on a captured event log (`tds-conform check-events`). `webhook_delivery`, `tender_deadline` and `hold_request` run live against any endpoint.
- Back-pressure (§7.2) has no scenario of its own, since a live provider cannot be made to throttle on demand. Whenever any scenario gets an unexpected 429 or 503, the kit checks the hint as a SHOULD (`Retry-After` present and parseable, `retry_after_s` present and in agreement, the matching `code`) and records a failure as a **warning**, which does not affect the verdict; it then waits and repeats the request with the same `Idempotency-Key` (up to 3 times). A wait longer than 30 s, or a provider still busy after the retries, makes the scenario INCONCLUSIVE. The example validator requires every operation to document 429 and 503 with `Retry-After`.
- CSV batch files (§9.2) are checked with `tds-conform check-csv`; it is a file check, not a conformance level.
- A requester's relay mailbox (§9.4) is checked with `tds-conform mailbox --url <mailbox base URL> --token <mailbox token>`, which runs `relay_mailbox_upload` (credential, listing, write-once uploads, every refusal, unknown files) and `relay_mailbox_download` (download, checksums, a valid §9.2 requests file, idempotent acknowledgement). Both must pass for the **Relay mailbox** level, which a requester claims, not a provider. The upload scenario leaves one header-only answers file (no rows) under a `conform-` name. The download scenario needs a request file waiting, and acknowledges it only with `--take-files` (use a test mailbox: an acknowledged file leaves the listing); without either it is INCONCLUSIVE. Reports never copy file contents. `tds-mock` serves a mailbox per provider at `/mailbox` (token = provider id), with `/_sim/mailbox/<provider_id>` to queue request files and read uploads, so agents can be developed against it.
- Fuzzing (`tds-conform fuzz`, schemathesis) complements the scenarios: generated valid and invalid requests to every operation must get documented status codes, media types and schema-valid bodies, never a 5xx. It is a diagnostic, not part of a level.
- `tender_deadline` tests the provider side: respond by `respond_by`, or the tender becomes `EXPIRED`. Conformance never measures how fast a broker or a human approves a fallback tender; it checks only that a fallback tender, when sent, follows the published policy.

## 14. Mappings
- `tds-2020-crosswalk.md`: every core field ↔ TDS element (name, section, notes, status: `MATCH` / `RENAMED` / `EXTENSION` / `TODO`).
- `gtfs-flex.md`, `gtfs-realtime.md`: how locations/services are referenced.
- `netex-interchange.md`: interchange semantics.
- `logistics-patterns.md`: EDI 204/990 → tender; EDI 214 → milestones; GS1 EPCIS / proof of delivery → custody; visibility platforms (project44/FourKites) → broker model; what was deliberately left out.

## 15. Milestones
| Version | Scope |
|---|---|
| **v0.1** | Crosswalk template; core schemas: trip, leg, location, time-window, error; Spectral config; example validator script; README/licenses/CONTRIBUTING. |
| **v0.2** | Core paths (§7); 2 examples (`single-trip`, `cancellation`); Prism mock server; CI lint + validate. |
| **v0.3** | `tds-conform` CLI running `book_confirm_cancel` + `idempotency` against mock; report output. |
| **v0.4** | Transfer profile (§10) draft; examples `two-provider-transfer`, `late-first-leg-hold`, `tender-expired-retender`, `supervised-rider-custody-handoff` (v0.4.1 adds `missed-connection-reprotect`, v0.5 `refusals-and-failures`); mock simulating two providers; ThroughRide adapters pointed at mock. **SE Michigan pilot demo target.** |
| **v0.5** | Settlement split (§10.8); paratransit and NEMT drafts with conformance levels (§11); AsyncAPI completeness incl. broker exception events (§9); `tds-conform fuzz`; `docs/for-agencies.md` RFP language. v0.5.1 adds exception type `ESCALATED`; v0.5.2 eligibility verification methods and fact codes (§6.5.1); v0.5.3 rider-category fact codes (§6.5.2) and booking windows (§7.1). |
| **v0.6** | CloudEvents binary-mode rendering of deliveries (§9.1); Standard Webhooks signature headers, `X-TDS-*` deprecated (§12); CSV profile for batch providers (§9.2) with `tds-conform check-csv`. |
| **v0.7** | Subscription secret rotation over the API (§12.1) with conformance scenario `secret_rotation`. v0.7.1 adds back-pressure: 429/503 with `Retry-After` and `retry_after_s` (§7.2). |
| **v0.8** | Reading, changing and ending subscriptions (§9.3) with conformance scenario `subscription_management`. |
| **v0.9** | Relay mailbox for CSV profile files (§9.4, `spec/relay-mailbox.yaml`), a `tds-mock` mailbox, and the Relay mailbox level (`tds-conform mailbox`). |
| v0.10+ | Settlement exchange (API operation or events, undecided), profile vocabularies checked against TDS 2020 and payers, outreach to MobilityData/OMF. |

## 16. Open questions
1. Do DRCOG or NEORide already have TDS API schemas? (Ask before v0.2 to avoid divergence.)
2. Who currently governs TDS? Is USDOT's reservation/scheduling/dispatch standards effort a competitor or a home?
3. TCRP Report 210 reuse terms for field definitions.
4. Should core allow multi-leg trips natively, or keep multi-leg strictly in the transfer profile? (Current: transfer profile.)
5. ~~Synchronous vs asynchronous booking decisions as the default.~~ Resolved: both conformant; providers declare `typical_response_s` (§7, §13).
6. Whether ETA `confidence` should be standardized to a specific percentile definition.
7. Liability semantics for `PROTECTED` connections (what the outbound provider commits to).
8. The CloudEvents `source` namespace (`urn:tds:`, not a registered URN namespace) and `type` prefix (`org.tds-openapi.`) follow the placeholder `$id` domain; settle them when the domain is chosen.
9. Relay mailbox (§9.4): should the requester report back on an uploaded file (rows applied, rows refused and why), e.g. `GET /inbox/{file_name}`, so an agent can tell its staff without a phone call? Today content problems go out of band.
