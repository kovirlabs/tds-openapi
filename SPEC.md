# SPEC.md — tds-openapi

Status: **Draft 3 (v0.5.0 in progress; last release v0.4.2)** · Owner: Evan / Kovir Labs · Date: 2026-10-20

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
`rider_ref_id` (opaque, required; the requester SHOULD issue a different, stable value per provider so providers cannot link a rider across services); `display_name` (first name / initial, optional); `contact` (phone for SMS, optional, requester-controlled consent flag `contact_consent`); `language` (BCP 47); `eligibility` (array of `{program, attested_by, valid_until}` — e.g. `ADA_PARATRANSIT`, `SENIOR_5310`). **No diagnoses, DOB, or health data in core.**

### 6.6 FareFunding
`fare` (`amount`, `currency`); `funding_source` enum (`RIDER`, `SECTION_5310`, `SECTION_5311`, `MEDICAID_NEMT`, `AGENCY`, `SPONSOR`, `OTHER`); `sponsor_ref`; `authorization_ref`. TODO(crosswalk).

### 6.7 Error
RFC 9457 Problem Details (`type`, `title`, `status`, `detail`, `instance`) + `code` enum: protocol errors (`INVALID_REQUEST`, `UNAUTHORIZED`, `NOT_FOUND`, `INVALID_STATE`, `IDEMPOTENCY_KEY_REUSED`, `TRIP_ID_IN_USE`) and booking refusals (e.g. `NO_CAPACITY`, `OUTSIDE_SERVICE_AREA`, `OUTSIDE_SERVICE_HOURS`, `ELIGIBILITY_REQUIRED`, `INVALID_WINDOW`, `UNSUPPORTED_REQUIREMENT`).

## 7. Core operations (OpenAPI)
| Method | Path | Purpose |
|---|---|---|
| POST | `/trips` | Request a trip. Returns 202 with `status: REQUESTED` (decision follows by event) or 201 with the decision if synchronous. Both are fully conformant. |
| GET | `/trips/{trip_id}` | Current state. |
| POST | `/trips/{trip_id}/confirm` | Requester confirms a provider's offer (if provider returned `OFFERED`). |
| PATCH | `/trips/{trip_id}` | Modify windows/party. Provider may re-offer. |
| POST | `/trips/{trip_id}/cancel` | Cancel with `reason` enum (`RIDER_REQUEST`, `PROVIDER_UNABLE`, `DUPLICATE`, `MISSED_CONNECTION`, `OTHER`), `cancelled_by`, optional `detail` (free text, no health data). |
| POST | `/subscriptions` | Register webhook URL + event types. |
| GET | `/health` | Liveness, supported profiles and spec versions, `operated_via` (`DIRECT` / `BROKER_PROXY`), `typical_response_s` (how long an asynchronous decision usually takes). |

All mutating calls require `Idempotency-Key` header. Settlement endpoints are deferred; v0.5 defines the settlement split format only (§10.8).

Requesters should set deadlines (`respond_by`) no shorter than the provider's declared `typical_response_s`.

## 8. State machines

### 8.1 Trip status
```
REQUESTED → OFFERED → CONFIRMED → SCHEDULED → IN_PROGRESS → COMPLETED
     │          │          │            │             └→ FAILED
     └→ REJECTED└→ EXPIRED └────────────┴→ CANCELLED
```
`NO_SHOW` is terminal from `SCHEDULED`. Providers that confirm synchronously may skip `OFFERED`.

## 9. Events (AsyncAPI)
Defined in `spec/asyncapi.yaml`; payload schemas in `profiles/*/event-*.yaml`.
Envelope: `event_id`, `event_type`, `occurred_at`, `trip_id`, `leg_id`, `provider_id`, `payload`, `schema_version`.
Core event types: `TRIP_STATUS_CHANGED` (every status change), `TRIP_SCHEDULED` (committed times or vehicle set or changed), `TRIP_CANCELLED` (sent with the status change, carries who cancelled and why).
Transfer event types: `TENDER_RESPONSE`, `HOLD_RESPONSE`, `MILESTONE`, `ETA_UPDATE`, `CUSTODY_HANDOFF`.
- Providers emit an event for **every** change, including changes the requester already saw in a synchronous response, so a receiver's event log is complete.
- Delivery: one event per HTTPS POST to the subscription's `callback_url`; at least once, retries with exponential backoff; receivers return 2xx and dedupe on `event_id`; order by `occurred_at`. Plain `http://` callbacks are allowed only for loopback addresses (development and the conformance kit).

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
`exception_id`, `severity` (`INFO`/`AT_RISK`/`CRITICAL`), `type` (`LOW_CONNECTION_PROBABILITY`, `TENDER_EXPIRED`, `TENDER_EXHAUSTED`, `STALE_HEARTBEAT`, `MISSED_MILESTONE`, `HOLD_DENIED`, `HOLD_UNANSWERED`, `CUSTODY_GAP`), `interchange_id`, `detected_at`, `suggested_actions`, `resolved_at`.
Default trigger: connection probability < 0.8 → `AT_RISK`; < 0.5 → `CRITICAL`.

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
Both profiles extend `POST /trips`: the body is a core TripRequest plus one profile object (`paratransit` or `nemt`). A requester sends the object only to providers whose `/health` lists the profile; a provider that lists it validates the object and refuses (400 `INVALID_REQUEST`) a request that breaks the rules below. The core rules of §12 apply unchanged: needs are leg `service_requirements` (expressing what the vehicle and driver must do, never why), riders are pairwise references, eligibility and authorization are attestations or references, never evidence. No conformance scenarios yet; `tds-mock` validates both objects and `tds_conform.profiles.profile_problems()` holds the cross-field rules.

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
- Webhook signing: `X-TDS-Timestamp` is Unix seconds; `X-TDS-Signature` is `sha256=` + lowercase hex HMAC-SHA256, keyed with the subscription's `signing_secret`, of `<timestamp>.<raw body>`. Receivers reject a bad signature or more than 5 min skew.
- Data minimization: share only what the receiving provider needs for that leg. Rider contact shared only with `contact_consent: true`.
- Retention guidance in docs (not enforced by spec).

## 13. Versioning and conformance
- SemVer on the spec. `schema_version` in every event; `/health` lists supported versions and profiles.
- Conformance levels: **Core**, **Core + Transfer**, etc. A run produces an HTML/JSON report a vendor can attach to an RFP response. A level is met only if every scenario for it passes; INCONCLUSIVE (the provider refused the test trip) does not count as a pass.
- The kit ships with a stateful reference provider (`tds-mock`) that passes every scenario; it is the executable reading of this spec where prose is ambiguous.
- Conformance scenarios (initial): `book_confirm_cancel`, `modify_window`, `idempotency`, `webhook_delivery`, `tender_deadline`, `milestone_sequence`, `eta_heartbeat_staleness`, `hold_request`, `custody_required`.
- Synchronous (201) and asynchronous (202 → later `OFFERED`/`REJECTED`) decisions are equally conformant. Timing checks use the provider's declared `typical_response_s`, not a fixed number of seconds; a broker-proxied provider is tested the same way (the kit may stand in for its staff).
- Scenarios that need a vehicle to move (`milestone_sequence`, `custody_required`, `eta_heartbeat_staleness`) cannot be triggered on a live provider; they are checked on a captured event log (`tds-conform check-events`). `webhook_delivery`, `tender_deadline` and `hold_request` run live against any endpoint.
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
| **v0.4** | Transfer profile (§10) draft; examples `two-provider-transfer`, `late-first-leg-hold`, `tender-expired-retender`, `supervised-rider-custody-handoff` (v0.4.1 adds `missed-connection-reprotect`); mock simulating two providers; ThroughRide adapters pointed at mock. **SE Michigan pilot demo target.** |
| v0.5+ | AsyncAPI completeness, settlement, paratransit/NEMT profiles, `for-agencies.md` RFP language, outreach to MobilityData/OMF. |

## 16. Open questions
1. Do DRCOG or NEORide already have TDS API schemas? (Ask before v0.2 to avoid divergence.)
2. Who currently governs TDS? Is USDOT's reservation/scheduling/dispatch standards effort a competitor or a home?
3. TCRP Report 210 reuse terms for field definitions.
4. Should core allow multi-leg trips natively, or keep multi-leg strictly in the transfer profile? (Current: transfer profile.)
5. ~~Synchronous vs asynchronous booking decisions as the default.~~ Resolved: both conformant; providers declare `typical_response_s` (§7, §13).
6. Whether ETA `confidence` should be standardized to a specific percentile definition.
7. Liability semantics for `PROTECTED` connections (what the outbound provider commits to).
