# ThroughRide ↔ tds-openapi alignment notes

Notes from work in the ThroughRide repo about where it and this spec disagree. Input for
discussion, not instructions: each item is resolved by Evan, then either the spec changes
(with an example, crosswalk row and CHANGELOG entry) or ThroughRide's adapter maps across.

This file is public once the repo is. Keep it to field/behaviour differences; no pilot,
funding or contact details.

## How to add an item

Append under *Open*, one heading per mismatch:

```
### <short title>
- **Where:** tds-openapi `profiles/core/<file>.yaml` field / SPEC.md §x  ↔  ThroughRide `apps/<app>/<file>.py` model/field
- **Mismatch:** what each side does today
- **Why it matters:** what breaks or gets awkward (adapter, demo, privacy, TDS fit)
- **Suggested resolution:** change spec / change ThroughRide / map in adapter / needs decision
```

## Open

*All items below were answered on 2026-10-20; see the response table and Resolved. New items go here.*

*Items 1–12 added 2026-10-20 from the ThroughRide side. ThroughRide `spec.md` §8.1 holds its full mapping to this binding. Items are listed roughly by how soon they affect the v0.4 transfer demo.*

### Fallback tenders and REPROTECT need an approval mode
- **Where:** SPEC.md §10.2 (tender fallback), §10.5 (`REPROTECT`)  ↔  ThroughRide CLAUDE.md rule 1, `apps/transfers/models.py` `Proposal`
- **Mismatch:** The spec has the broker move down a fallback provider list on its own, and `REPROTECT` triggers a new tender. ThroughRide's rule is that nothing books or cancels without a human approval. A tender to a provider the coordinator did not choose becomes a one-tap proposal. Running automatically is allowed only for rules written into a signed agreement, and only after a pending rule change.
- **Why it matters:** As written, a human-in-the-loop broker could look non-conformant against `tender_deadline`, or the kit might assume re-tendering happens within N seconds.
- **Suggested resolution:** Change the spec: the fallback policy carries `approval: REQUIRED | PRE_AGREED`. Conformance checks that the next tender, when one is sent, follows the policy. It does not check how fast a human approves.

### Interchange lacks a standard waiting time (wait without asking)
- **Where:** `profiles/transfer/interchange.yaml` (§10.1)  ↔  ThroughRide `Interchange.max_hold_min`, planned `auto_hold_min`
- **Mismatch:** The spec has `max_hold_s`, which ThroughRide treats as the most the outbound provider can be *asked* to wait. ThroughRide also plans `auto_hold_min ≤ max_hold_min`: a wait the outbound vehicle gives a late feeder without being asked. This follows rail practice, such as Deutsche Bahn's waiting-time rules.
- **Why it matters:** Without a field, every hold has to be a request and reply, which is slow for the short holds that matter most.
- **Suggested resolution:** Change the spec: add optional `auto_hold_s` (must be ≤ `max_hold_s`, default 0) to interchange, with `x-profile: transfer`.

### Hold request has no deadline or "unanswered" outcome
- **Where:** §10.5 `hold-request.yaml`, §10.6 exception types  ↔  ThroughRide hold Task and escalation ladder (`apps/transfers/services.py` `escalate`)
- **Mismatch:** The spec's responses are only `GRANTED`, `PARTIAL` and `DENIED`. In practice ThroughRide's most common case is no answer. It escalates on timers and, at the onward pickup time, closes the request as unanswered.
- **Why it matters:** The broker needs to know when silence becomes "no". Coordinators need an exception type for it.
- **Suggested resolution:** Change the spec: add `respond_by` to the hold request; treat no response by then as `EXPIRED`; add exception type `HOLD_UNANSWERED`. Escalation steps (who gets texted) stay broker-local.

### RETIME direction is ambiguous
- **Where:** §10.5 `action: RETIME` and response `PARTIAL`  ↔  ThroughRide inbound reply `RETIME 14:35` (`adapters/email.py` `parse_reply`, SMS staff replies)
- **Mismatch:** In the spec, RETIME is something the broker asks for. In ThroughRide it is mostly the provider's counter-offer: "can't wait that long, but I can pick up at 14:35."
- **Why it matters:** The adapter needs to know where the provider's new pickup time goes.
- **Suggested resolution:** Change the spec: `GRANTED` and `PARTIAL` responses carry `scheduled_pickup` (and optionally `held_until`). A provider counter-offer is `PARTIAL` with the new time.

### Milestones for feeder vs onward legs at the hub
- **Where:** §10.3 milestone enum  ↔  ThroughRide driver trip page buttons (spec §7.2/§8.1)
- **Mismatch:** For a feeder leg, the hub *is* the dropoff, so it's unclear whether arrival is `ARRIVED_TRANSFER`, `ARRIVED_DROPOFF` or both. For the onward leg, the hub is the pickup, so `EN_ROUTE_TO_TRANSFER` might overlap with `EN_ROUTE_TO_PICKUP`. ThroughRide currently plans: feeder uses `EN_ROUTE_TO_TRANSFER` / `ARRIVED_TRANSFER`, then `RIDER_HANDED_OFF`, then `LEG_COMPLETED`; onward uses `EN_ROUTE_TO_PICKUP` / `ARRIVED_PICKUP` at the hub.
- **Why it matters:** `milestone_sequence` conformance and both drivers' screens depend on a single reading.
- **Suggested resolution:** Needs a decision; then state the valid sequences for feeder, onward and single legs in §10.3 and an example.

### Supervision vs service requirements (HAND_TO_HAND, CAR_SEAT)
- **Where:** `profiles/core/leg.yaml` `service_requirements`; interchange `supervision_required`  ↔  ThroughRide `Journey` assistance level (`curb_to_curb`, `door_to_door`, `hand_to_hand`) and `car_seat`
- **Mismatch:** ThroughRide (like many paratransit systems) records "hand to hand": the driver may release the rider only to a responsible person. The core enum has no such value, and supervision exists only on the transfer interchange. Car seats are also missing.
- **Why it matters:** A provider receiving a single core trip would not know the rider must not be left alone at the destination. Custody is not only a transfer concern.
- **Suggested resolution:** Change the spec: add `HAND_TO_HAND` and `CHILD_SEAT` to `service_requirements` (TODO(crosswalk) against TDS). A `HAND_TO_HAND` leg on a protected interchange implies `supervision_required: true`.

### ETA source and quality
- **Where:** §10.4 `ETA_UPDATE`  ↔  ThroughRide `Leg.source_of_eta` (`provider_api`, `gtfs_rt`, `dispatcher_update`, `estimate`) and planned data quality tiers
- **Mismatch:** The band has `confidence`, but nothing says where the estimate came from: a live vehicle position, a dispatcher's guess, or just the promised time.
- **Why it matters:** The broker prompts for a fresh status sooner when the source is weak, and a coordinator reading an exception wants to know whether the ETA is real.
- **Suggested resolution:** Change the spec: optional `source` enum on `ETA_UPDATE` (`VEHICLE_POSITION`, `PROVIDER_FORECAST`, `DISPATCHER`, `DRIVER`, `SCHEDULE`).

### Delay cause on ETA updates and failures
- **Where:** §10.4 `ETA_UPDATE`, §10.3 `LEG_FAILED`, §10.8 settlement split  ↔  ThroughRide planned `Leg.delay_cause` / `delay_attributed_to` (spec §7.1)
- **Mismatch:** There's no place to say why a leg is late. ThroughRide plans cause codes `TRAFFIC`, `BOARDING`, `VEHICLE`, `WEATHER`, `PRIOR_TRIP`, `RIDER`, `UNKNOWN`, modeled on rail delay attribution.
- **Why it matters:** Settlement under continuation agreements ("your feeder was late, so the replacement ride is on you") needs a recorded cause, not an argument afterward.
- **Suggested resolution:** Change the spec: optional `delay_cause` on `ETA_UPDATE` and on `LEG_FAILED`, with `x-profile: transfer`.

### Accessibility buffer applies only when the rider needs it
- **Where:** interchange `accessibility_buffer_s`  ↔  ThroughRide `transfers/slack.py` (applied only when the journey needs the accessibility buffer)
- **Mismatch:** The spec does not say when the buffer applies. ThroughRide adds it only for riders whose needs call for it (wheelchair, assistance), not for every connection at that hub.
- **Why it matters:** Applying it to everyone makes connections look tighter than they are and causes false alarms.
- **Suggested resolution:** Change the spec: add a description saying the buffer is added to `min_connection_s` when the inbound or outbound leg has `service_requirements` the interchange lists. Possibly add `accessibility_buffer_applies_to` (enum list).

### rider_ref_id should be pairwise per provider
- **Where:** `profiles/core/rider.yaml` `rider_ref_id`  ↔  ThroughRide spec §10.1 (pairwise provider pseudonyms)
- **Mismatch:** The schema calls the id opaque but doesn't say whether the same rider gets the same id at every provider. ThroughRide plans a different random id per provider, as OpenID Connect pairwise subject ids do.
- **Why it matters:** One shared id lets providers pool data and follow a rider across services. That's a privacy risk, and many agencies will raise it during review.
- **Suggested resolution:** Change the spec: in the description, a requester SHOULD issue a different `rider_ref_id` per provider, and SHOULD keep it stable for that rider and provider pair.

### Cancel reason codes
- **Where:** SPEC.md §7 `POST /trips/{trip_id}/cancel` `reason` enum (not yet defined)  ↔  ThroughRide `CancelReason` (`rider_request`, `provider_issue`, `duplicate`, `other`)
- **Mismatch:** Nothing to compare yet. ThroughRide uses reason codes only, never free text, because events carry no PII.
- **Why it matters:** It avoids a lossy mapping later.
- **Suggested resolution:** When defining the enum, include at least `RIDER_REQUEST`, `PROVIDER_UNABLE`, `DUPLICATE`, `MISSED_CONNECTION`, `OTHER`. Keep free-text `detail` optional and say it must not contain health data.

### Brokers that front providers without software
- **Where:** SPEC.md §4 roles, §13 conformance levels  ↔  ThroughRide `manual` / `email` / `sms` adapters; planned provider proxy (ThroughRide spec §8.1, T4)
- **Mismatch:** The spec assumes every provider runs its own endpoint. Many small and rural providers dispatch by phone, legacy CAD or spreadsheet. ThroughRide plans to expose a provider-side endpoint on their behalf and relay requests to a human by email or SMS. Responses then take minutes, not seconds.
- **Why it matters:** This is how most providers would join early on. Conformance timing (synchronous decisions, short deadlines) should not exclude them.
- **Suggested resolution:** Needs a decision. Possibly have `/health` declare `operated_via: BROKER_PROXY` and `typical_response_s`, and have conformance accept asynchronous `202 REQUESTED` → later `OFFERED`/`REJECTED` as fully conformant.

### No exception type for "a person has been asked to act"
- **Where:** `profiles/transfer/exception.yaml` `type` enum, SPEC.md §10.6, `EXCEPTION_RAISED` / `EXCEPTION_RESOLVED` events  ↔  ThroughRide escalation ladder (`apps/transfers/services.py` `escalate`, spec §7.1)
- **Mismatch:** The binding's exception types each describe a condition (`HOLD_UNANSWERED`, `CUSTODY_GAP`, …). ThroughRide's coordinator inbox also has `ESCALATED`: a condition was not handled in time, so the broker moved it up its ladder and on-duty staff are now being contacted. The binding has no way to say "escalated", so a broker that publishes `EXCEPTION_RAISED` to subscribers (an agency dashboard, the onward provider) can't tell them a human now owns the problem, or how urgent it has become.
- **Why it matters:** Subscribers need to know that a broker is already acting, so they don't start a second, conflicting response (two dispatchers ringing the same driver). Agencies writing RFPs also want escalations to be counted and reported.
- **Suggested resolution:** change the spec (transfer profile, additive):
  - Add `ESCALATED` to the exception `type` enum, raised only by a broker, with `x-profile: transfer`.
  - Add optional `escalates_exception_id` (the exception it escalates; required when `type` is `ESCALATED`) and optional `escalation_level` (integer ≥ 1, broker-defined steps).
  - Severity is at least `AT_RISK`; a broker MAY raise it to `CRITICAL` at higher levels.
  - It carries no names, phone numbers or roles of the people contacted. Who gets contacted stays broker-local, as agreed in #3.
  - Resolving the escalated exception also resolves the `ESCALATED` one (`EXCEPTION_RESOLVED` for each).
  - Add an example to `examples/exceptions`, and a check that `ESCALATED` never comes from a provider.
- **tds-openapi response (2026-10-21): agree, applied in v0.5.1.** Implemented as proposed, with
  three tightenings:
  - `escalates_exception_id` names the *condition* exception, never another `ESCALATED` one.
    Climbing the ladder raises a new `ESCALATED` for the same condition with a higher
    `escalation_level`, so subscribers can count steps without walking a chain.
  - The schema enforces the shape: `ESCALATED` requires `escalates_exception_id` and severity
    `AT_RISK` or `CRITICAL`; the escalation fields are refused on any other type. The level stays
    optional (read as 1), as proposed.
  - "Never from a provider" is checked as: no party in an event stream sends both provider events
    and exception events (`tds_conform.events.broker_event_problems`, used by the example
    validator and `tds-conform check-events`), and the kit's `webhook_delivery` fails a provider
    that delivers any `EXCEPTION_*` event. Escalation rules (earlier condition from the same
    broker, rising levels, resolved together with the condition) are in
    `escalation_problems`. Example: `examples/exceptions/`.
  - **For Evan:** `exception` stays an open object, as every schema here is, so nothing stops a
    broker from adding a person's name to it. Closing it (as `nemt` is) would be a breaking
    change; worth an RFP before 1.0.

## v0.4.1 audit (2026-10-21)

Each agreed item checked against `profiles/`, `spec/`, `examples/`, `tds-mock` and the kit.

| # | Where it lives now | Gap found → fixed in 0.4.1 |
|---|---|---|
| 1 | `transfer/fallback-policy.approval`; `tender-expired-retender` and `missed-connection-reprotect` narrate a coordinator approval; §13 `tender_deadline` tests the provider side only | Fallback policy is broker-side and not on the wire, so the kit does not check it (by design). |
| 2 | `interchange.auto_hold_s`; `accepted-terms` (required in `ACCEPTED` tenders); mock caps it; `tender_deadline` checks accepted ≤ requested | none |
| 3 | `hold-request.respond_by`; hold `EXPIRED`; exception `HOLD_UNANSWERED`; mock expires holds | No example of an expired hold or `HOLD_UNANSWERED` → `missed-connection-reprotect`, exception schema example |
| 4 | hold `GRANTED`/`PARTIAL` carry `scheduled_pickup`, `held_until`; mock counter-offers; kit checks `scheduled_pickup` | No `PARTIAL` example → `missed-connection-reprotect` `06-hold-onward.response.json` |
| 5 | `transfer/milestone` has no transfer-specific values; kit `milestone_sequence` | none |
| 6 | `HAND_TO_HAND`, `CHILD_SEAT` in core; mock treats `HAND_TO_HAND` as supervised | The §10.1 MUST (`HAND_TO_HAND` + `PROTECTED` ⇒ `supervision_required`) was not enforced → mock refuses such tenders (400); validator checks examples |
| 7 | `eta-band.source`; mock sets it | none |
| 8 | `transfer/delay-cause` (no `RIDER`) on `ETA_UPDATE` and `LEG_FAILED` | none |
| 9 | `interchange.accessibility_buffer_s` description | none |
| 10 | `rider.rider_ref_id` description; demo uses pairwise ids | none |
| 11 | `core/cancel-reason`; `cancel-request.detail` (no health data) | No `MISSED_CONNECTION` example → `missed-connection-reprotect` `10-cancel-onward.request.json` |
| 12 | `health.operated_via`, `typical_response_s`; kit timing uses `typical_response_s` | none |

## tds-openapi response (2026-10-20)

Status: **applied** = in the schemas now · **agree** = fold into SPEC.md when that section is drafted · **decide** = Evan's call, recommendation given.

| # | Item | Status | tds-openapi view |
|---|---|---|---|
| 1 | Approval mode for fallback / REPROTECT | agree | Add `approval: REQUIRED \| PRE_AGREED` to the published fallback policy. Clarify in §13 that `tender_deadline` tests the *provider* side (respond by `respond_by`, else `EXPIRED`) and never how fast a broker re-tenders. |
| 2 | `auto_hold_s` | agree, with a caveat | Add `auto_hold_s` (≤ `max_hold_s`, default 0). It is a commitment by the outbound provider, so it must come from the provider: echoed in the tender `ACCEPT`, not just asserted by the broker on the interchange. Ties into open question 7 (liability for `PROTECTED`). |
| 3 | Hold `respond_by` / unanswered | agree | `respond_by` on the hold request; no answer by then = `EXPIRED`; exception `HOLD_UNANSWERED`. Escalation stays broker-local. |
| 4 | RETIME direction | agree | Keep `RETIME` as a broker action *and* let `GRANTED`/`PARTIAL` carry `scheduled_pickup` and optional `held_until`. Provider counter-offer = `PARTIAL` + new time. |
| 5 | Feeder vs onward milestones | decide | **Recommend dropping `EN_ROUTE_TO_TRANSFER` / `ARRIVED_TRANSFER`.** Milestones refer to the leg's own pickup and dropoff; the interchange already says which end is a hub. Then one sequence fits single, feeder and onward legs: `EN_ROUTE_TO_PICKUP → ARRIVED_PICKUP → RIDER_ONBOARD → EN_ROUTE_TO_DROPOFF → ARRIVED_DROPOFF → [RIDER_HANDED_OFF] → LEG_COMPLETED`, with `RIDER_HANDED_OFF` required where custody applies. Main reason: a feeder provider may not know its trip is part of a transfer and shouldn't need to. ThroughRide's driver UI can still label the button "Arrived at transfer". The alternative is ThroughRide's current plan with sequences spelled out per leg role. |
| 6 | `HAND_TO_HAND`, `CHILD_SEAT` | **applied** | Added to `leg.service_requirements` (core, crosswalk TODO). The rule "`HAND_TO_HAND` on a protected interchange implies `supervision_required`" goes into the transfer profile in v0.4. Car seats are named `CHILD_SEAT` in the spec; the adapter maps `car_seat`. |
| 7 | ETA `source` | agree | Optional enum as proposed. ThroughRide mapping: `provider_api` → `PROVIDER_FORECAST`, `gtfs_rt` → `VEHICLE_POSITION`, `dispatcher_update` → `DISPATCHER`, `estimate` → `SCHEDULE`. |
| 8 | `delay_cause` | agree, narrower | Optional, `x-profile: transfer`. Merge `RIDER` and `BOARDING` into `BOARDING` (no rider fault or reason is recorded, since a "rider" cause invites health explanations). Codes only; no free text. Whether a cause shifts cost in settlement is left to the agreements, not the spec. |
| 9 | Accessibility buffer scope | agree, description only | Say the buffer is added to `min_connection_s` only when either leg has `service_requirements`. Hold off on `accessibility_buffer_applies_to` until a real hub needs a different rule. |
| 10 | Pairwise `rider_ref_id` | **applied** | SHOULD differ per provider, SHOULD be stable per rider and provider pair (`rider.yaml` description). |
| 11 | Cancel reason codes | agree (v0.2) | Use the proposed enum when §7 paths are written; `detail` optional with no health data. ThroughRide `provider_issue` → `PROVIDER_UNABLE`. |
| 12 | Broker-fronted providers | decide | **Recommend:** state in §7/§13 that async `202 REQUESTED` → later `OFFERED`/`REJECTED` is fully conformant (SPEC §7 already allows it). Add `operated_via: DIRECT \| BROKER_PROXY` and `typical_response_s` to `/health` so requesters can set sensible `respond_by`. The conformance kit runs timing scenarios against the declared `typical_response_s`, not a fixed number of seconds. |

## Resolved

- **ThroughRide spec still names pre-v0.4 values** (2026-10-21): ThroughRide now records the feeder's
  `ARRIVED_DROPOFF` at the hub and starts the custody-gap timer there, sends `rider` as `BOARDING`
  and maps `car_seat` to `CHILD_SEAT`.
- **No exception type for "a person has been asked to act"** (2026-10-21): `ESCALATED` with
  `escalates_exception_id` and `escalation_level`, v0.5.1 (see the response under the item above).
- **#6 Supervision vs service requirements:** `HAND_TO_HAND`, `CHILD_SEAT` added to core (2026-10-20). Interchange implication pending v0.4.
- **#10 Pairwise rider ids:** guidance added to `rider.yaml` (2026-10-20).
- **#1–5, 7–9, 11, 12:** folded into SPEC.md Draft 1 (2026-10-20) as recommended in the table above; Evan approved the #5 and #12 recommendations. Sections: §4 (broker proxy), §7 (`/health`, cancel reasons, async decisions), §10.1 (`auto_hold_s`, buffer scope, `HAND_TO_HAND` → supervision), §10.2 (`approval`, ACCEPT echoes terms), §10.3 (milestones without transfer-specific values), §10.4 (`source`, `delay_cause`; ETA `target` is now `PICKUP`/`DROPOFF` only, following #5), §10.5 (`respond_by`, `EXPIRED`, counter-offers), §10.6 (`HOLD_UNANSWERED`), §13 (timing and tender conformance). Schemas follow in v0.2 (core) and v0.4 (transfer).

**For ThroughRide:** milestone `EN_ROUTE_TO_TRANSFER`/`ARRIVED_TRANSFER` and ETA target `TRANSFER` are gone. Map hub arrivals to the leg's own `ARRIVED_DROPOFF`/`ARRIVED_PICKUP`. Also map `car_seat` → `CHILD_SEAT`, `provider_issue` → `PROVIDER_UNABLE`, and drop the `RIDER` delay cause (use `BOARDING`).

## v0.4 notes for ThroughRide (2026-10-20)

The transfer profile is drafted and `tds-mock --demo` simulates three providers. Integration
guide: `docs/demo.md`. Decisions ThroughRide's adapters should follow:

- **Brokers expose no API.** Tender and hold answers come back in the response (201) or as
  `TENDER_RESPONSE` / `HOLD_RESPONSE` events (202 first). There is no `POST /tenders/{id}/response`.
- **Tender both legs** with the same interchange; the provider learns from it whether it is the
  feeder (`from`, owes ETA heartbeats) or onward (`to`, can be asked to hold). Plan with
  `accepted_terms`, not the requested values.
- **Hold actions are `HOLD` and `RETIME` only.** Re-protecting = cancel with `MISSED_CONNECTION`
  + new tender, behind ThroughRide's approval rule (fallback `approval: REQUIRED`).
- **Supervision:** set `HAND_TO_HAND` on both legs *and* `supervision_required` on the
  interchange; custody arrives as `CUSTODY_HANDOFF` at each pickup and dropoff.
- **Webhooks:** verify `X-TDS-Signature` = `sha256=` HMAC(secret, `"<X-TDS-Timestamp>.<body>"`),
  dedupe on `event_id`, order by `occurred_at`. Every change produces an event, even ones
  already returned synchronously.
- **Mappings** (from the earlier items): `source_of_eta` → ETA `source` (`provider_api` →
  `PROVIDER_FORECAST`, `gtfs_rt` → `VEHICLE_POSITION`, `dispatcher_update` → `DISPATCHER`,
  `estimate` → `SCHEDULE`); `car_seat` → `CHILD_SEAT`; `provider_issue` → `PROVIDER_UNABLE`;
  `Interchange.max_hold_min` × 60 → `max_hold_s`.

New mismatches go under *Open* as before.

## v0.5 in progress: notes for ThroughRide (2026-10-21)

- **Settlement split** (SPEC.md §10.8, `transfer/settlement-split.yaml`): one line per leg
  tendered for a journey, cancelled ones included. ThroughRide's per-leg funding record maps
  directly: `funding_source` (same enum, now `core/funding-source.yaml`), sponsor →
  `sponsor_ref`, authorization → `authorization_ref`; leg terminal state → `trip_status`;
  cancel reason → `cancellation_reason`; `Leg.delay_cause` → `delay_cause` (`rider` →
  `BOARDING`). Money is a decimal string plus ISO 4217 currency. No endpoint yet; a report or
  file export in this shape is enough. `tds_conform.settlement.settlement_problems()` checks
  the totals.
- **Using the kit as a dependency** (v0.4.2): `tds_conform.examples` and `tds_conform.mock`
  are the public API (see `docs/demo.md`). `binding_version()` reports the last release
  (0.4.2) until 0.5.0 is cut, so vendor examples from a tag, not from `main`.
- **Paratransit and NEMT drafts** (SPEC.md §11): a provider listing `PARATRANSIT` or `NEMT` on
  `/health` accepts a `paratransit` or `nemt` object on `POST /trips`. ThroughRide's mobility
  notes stay out of every payload; send needs as `service_requirements` only (a stretcher
  trip now has `STRETCHER`), devices by size and weight, and Medicaid trips as an
  authorization reference plus `level_of_service`. Drafts only: no conformance level yet.
- **Exceptions as events** (v0.5): ThroughRide's outbound webhooks (its spec §8.1 decision 7)
  can send `EXCEPTION_RAISED` / `EXCEPTION_RESOLVED` with the binding's `exception` payload;
  `provider_id` is ThroughRide's broker id. `ESCALATED` (ThroughRide's ladder) is still not a
  binding exception type; propose it here if subscribers need it.
- **Offers that lapse:** a provider may set `offer_expires_at` on an `OFFERED` trip; ThroughRide
  should confirm before it or treat the leg as `EXPIRED`. Provider-side cancellations arrive as
  `TRIP_CANCELLED` with `cancelled_by: PROVIDER`.

## v0.5.0 released (2026-10-21, local tag `v0.5.0`)

Re-vendor examples from tag `v0.5.0`; `binding_version()` now returns `0.5.0`. Everything
above marked "v0.5" is in it. New since the v0.4.2 vendoring: `settlement-split/`,
`paratransit-trip/`, `nemt-trip/`, `refusals-and-failures/` (generated), `exceptions/`, and
`schema_version: "0.5.0"` in every generated event stream.

## v0.5.1 released (2026-10-21, local tag `v0.5.1`)

Adds exception type `ESCALATED`. Re-vendor from tag `v0.5.1` (`binding_version()` = `0.5.1`).
ThroughRide's outbound `EXCEPTION_RAISED` for its ladder: `type: ESCALATED`,
`escalates_exception_id` = the condition exception (`HOLD_UNANSWERED`, …), `escalation_level` =
ladder step, severity `AT_RISK` or `CRITICAL`, nothing about who was contacted; send
`EXCEPTION_RESOLVED` for each escalation when the condition resolves.

## v0.5.2 released (2026-10-21, local tag `v0.5.2`)

Eligibility attestations (SPEC.md §6.5.1). Re-vendor from tag `v0.5.2` (`binding_version()` =
`0.5.2`; generated event streams carry `schema_version: "0.5.2"`, nothing else changed in them).

- **Why:** receiving providers often determine eligibility themselves and ask for a date of
  birth, which the binding deliberately never carries. Whether a provider trusts another
  party's attestation depends on how it was verified, so attestations now say so.
- **New optional fields** on `rider_ref.eligibility[]`: `verification_method`
  (`STAFF_VERIFIED`, `DIGITAL_ID`, `AGENCY_DETERMINATION`, `SELF_DECLARED`, `OTHER`),
  `verified_at` (date, not after `valid_until`) and `verifier_ref` (the identity platform or
  credential issuer, when it is not `attested_by`; never a credential or document number).
- **Fact codes:** `program` may be a fact such as `AGE_60_PLUS`, `AGE_65_PLUS` or
  `AGE_UNDER_18` (`AGE_<N>_PLUS` / `AGE_UNDER_<N>`), next to program codes like
  `ADA_PARATRANSIT` and `SENIOR_5310`.
- **Refusals:** a provider that does not accept an attestation's attester or method answers
  422 `ELIGIBILITY_NOT_ACCEPTED` (tenders: `reason: ELIGIBILITY_NOT_ACCEPTED`);
  `ELIGIBILITY_REQUIRED` still means none was sent. Which combinations a provider accepts is
  agreement data, not on the wire.
- **For ThroughRide (behaviour, nothing breaks):** the `tds` adapter sends no eligibility today,
  which stays valid. To use this it needs a per-rider attestation record holding only program
  or fact code, attester, method, `verified_at`, `valid_until` and optional verifier id (no
  date of birth or document data), sent as-is in `rider_ref.eligibility`. Map a provider's
  `ELIGIBILITY_NOT_ACCEPTED` / `ELIGIBILITY_REQUIRED` refusal to a coordinator-facing
  rejection reason; per rule 1, offering the leg elsewhere stays a proposal. A provider's
  accepted (attester, method) list belongs with the agreement data (`ContinuationAgreement`
  or provider config), if ThroughRide wants to warn before sending.

## v0.5.3 released (2026-10-21, local tag `v0.5.3`)

Rider-category fact codes (SPEC.md §6.5.2) and provider booking windows (§7.1). Re-vendor from
tag `v0.5.3` (`binding_version()` = `0.5.3`; generated event streams change only in
`schema_version`). New example folders: `rider-category-facts/`, `booking-window/`.

**Fact codes for reporting and funder rules.**
- New `program` fact codes next to `AGE_<N>_PLUS`: `DISABILITY` (the rider counts as a person
  with a disability for program purposes; yes/no, never a qualifier such as a condition or
  type), `LOW_INCOME` (meets the income test of the program the agreement names; never the
  income) and `RESIDENT_<CC>_<area>`, in the US `RESIDENT_US_<5-digit county FIPS>` (e.g.
  `RESIDENT_US_26161`). The county FIPS code already contains the state, so no ISO 3166-2 part
  is needed. Medicaid enrollment stays a program code.
- Each provider derives its reporting category (general, senior, disabled, both) from the
  facts it received; a missing fact means general/unknown. Requesters send only the facts the
  receiving provider's agreement asks for.
- `tds_conform.eligibility.fact_code_problems` checks the patterns (SHOULD).
- **For ThroughRide (behaviour, nothing breaks):** the per-rider attestation record (v0.5.2
  note) can hold these codes as-is. Add per provider (agreement or provider config) the list of
  facts it needs, and send only those in `rider_ref.eligibility` for that provider's legs.
  Store county of residence as a FIPS code, not derived from the address at send time; never
  store or send a disability type in this record (needs stay `service_requirements`).
  ThroughRide's own reports can use the same derivation for per-provider counts.

**Booking windows.**
- `/health` may carry `booking_window` (`max_advance_s`, `min_notice_s`, `same_day`,
  `time_zone`) and `booking_window_by_profile` (`paratransit`, `nemt`, `transfer`, each
  replacing the default). Trip time = `earliest` of the pickup window, else the dropoff window;
  request time = when the provider receives `POST /trips`, `POST /tenders` or a window-changing
  `PATCH`.
- Outside the window: 422 `OUTSIDE_BOOKING_WINDOW` (tender `reason` likewise; `PATCH` keeps the
  previous terms), checked before other refusals. Too early: `bookable_from` on the Problem
  Details. The trip is `REJECTED`; resubmit later as a new trip with a new `trip_id`.
- `tds_conform.booking` (`booking_window_for`, `trip_time`, `opens_at`, `closes_at`,
  `booking_window_problem`) can be imported to plan submissions. `tds-mock` takes
  `ProviderConfig.booking_window` / `booking_window_by_profile` (or the `/_sim` policy).
- Not covered yet: a prior-day cutoff time (book by 17:00 the day before, GTFS-Flex
  `prior_notice_last_time`). `same_day: false` approximates it as "by the end of the previous
  day". Propose it here if a provider needs it.
- **For ThroughRide:** read each provider's `/health` booking window (cache it with the
  provider config; a manual window on `AdapterConfig` for providers without an endpoint). When a
  journey is composed, compute each leg's `opens_at`/`closes_at` and warn the coordinator about a
  leg that cannot be booked in time. A leg whose window has not opened stays planned with a
  scheduled submit at `opens_at` (a Celery beat task, idempotent per leg and action); sending it
  is the same action the coordinator already approved, so rule 1 is not touched. Map
  `OUTSIDE_BOOKING_WINDOW` to a coordinator-facing rejection reason; when `bookable_from` is
  present, offer "resubmit at that time" (new `trip_id`), never resubmit to another provider
  without approval.


## v0.6.0 released (2026-10-23, local tag `v0.6.0`)

Answers ThroughRide's interline-switch proposals (its spec §8.2, "Events and envelopes" and
"File adapter"; queue items TQ-16, TQ-17, TQ-18). Re-vendor from tag `v0.6.0`
(`binding_version()` = `0.6.0`; generated event streams change only in `schema_version`). New
example folders: `webhook-delivery/` (one delivery with headers and raw body) and `csv-batch/`
(CSV files plus JSON equivalents). The vendored examples now include `.csv` files; load them
as text (`tds_conform.examples.load_example` already does).

**CloudEvents rendering (SPEC.md §9.1).** Agreed as proposed, as an opt-in extra rendering:
- `POST /subscriptions` takes `cloudevents: true`; the response says whether the provider
  will add the headers (`false` is not an error). Deliveries then carry `ce-specversion` `1.0`,
  `ce-id` = `event_id`, `ce-source` = `urn:tds:provider:<provider_id>` (percent-encoded),
  `ce-type` = `org.tds-openapi.<event_type lower case>` (e.g. `org.tds-openapi.eta_update`),
  `ce-time` = `occurred_at`, `ce-subject` = `trips/<trip_id>[/legs/<leg_id>]`,
  `ce-dataschema` = `https://tds-openapi.org/schemas/<schema_version>/<profile>/<schema>.yaml`,
  and extensions `ce-providerid`, `ce-schemaversion`. Body and `Content-Type:
  application/json` are unchanged; binary mode only (structured mode would change the body).
- Dedup is now stated as (`provider_id`, `event_id`) = CloudEvents (`source`, `id`); senders
  keep `event_id` unique among their own events. `tds_conform.events.dedupe` uses the pair.
- Two choices differ from the proposal's wording: `ce-subject` names the trip as well as the
  leg (leg ids are only unique within a trip), and the source/type prefixes follow the
  placeholder `$id` domain, so they change if the domain does (SPEC.md §16 Q8, Evan's call).
- `tds_conform.cloudevents.cloudevents_headers(event)` renders them;
  `cloudevents_problems(headers, event)` checks them.
- **For ThroughRide:** outbound webhooks (`apps/webhooks`) can offer `cloudevents` per
  endpoint and add the headers with `cloudevents_headers`. Broker exception events carry the
  broker's id as `provider_id`, so their `ce-source` is `urn:tds:provider:<broker id>`.
  Inbound: if `TdsInboundEvent` dedupes on `event_id` alone, key it on (`provider_id`,
  `event_id`).

**Standard Webhooks signatures (SPEC.md §12).** Agreed; additive, with a deprecation:
- Deliveries carry `webhook-id` (= `event_id`), `webhook-timestamp` (Unix s, per attempt) and
  `webhook-signature` (space-separated `v1,<base64 HMAC-SHA256 of "id.timestamp.body">`, one
  per active secret, newest first). The key is the base64-decoded part of a `whsec_` secret,
  so Standard Webhooks libraries verify as they are. Senders SHOULD send them when the secret
  is `whsec_` + base64 of 24–64 bytes.
- `X-TDS-Signature` / `X-TDS-Timestamp` are deprecated but still sent on every delivery
  (keyed, as before, with the UTF-8 bytes of the whole secret string); removal needs a major
  version and an RFC.
- Receivers MUST accept a delivery when either a `v1` entry or `X-TDS-Signature` verifies
  within 300 s, and reject it otherwise; they ignore unknown versions (`v1a`).
  `tds_conform.events.verify_any(secrets, headers, body)` is that rule (one or several
  secrets); `sign_standard` / `verify_standard` / `new_standard_secret` are the pieces.
  `tds-mock` now issues `whsec_` secrets.
- Not yet in the binding: how a subscriber obtains a new secret (a rotation operation). The
  headers support rotation; issuing the second secret is out of band for now. Proposed as a
  follow-up.
- **For ThroughRide:** outbound, issue new endpoint secrets as `new_standard_secret()` and
  send both header sets (`sign` + `sign_standard`, same timestamp); existing secrets keep
  working with `X-TDS-*` only. Inbound (`adapters/tds.py`), verify with `verify_any` so
  providers can sign either way. Vendored fakes (`tests/fakes/tds.py`) can add the
  `webhook-*` headers; the `webhook-delivery` example shows a full set.

**TDS CSV profile (SPEC.md §9.2).** Agreed; new `profiles/csv`:
- Files `tds_requests_…csv` (requester → provider), `tds_answers_…csv` and
  `tds_statuses_…csv` (provider → requester), named with the UTC write time. RFC 4180, UTF-8
  (BOM stripped on read, never written), RFC 3339 times, `lat`/`lon`, `UPPER_SNAKE` codes,
  `|`-separated lists, empty cell = absent, columns matched by name.
- Request rows: `REQUEST` / `CONFIRM` / `MODIFY` / `CANCEL`, idempotency key (`requester_id`,
  `provider_id`, `trip_id`, `leg_id`, `action`, `revision`); `revision` was added so a second
  `MODIFY` of the same leg is not dropped as a duplicate. Answer rows repeat the key, with
  `result`, `status`, `provider_trip_id`, scheduled times, `reason` (refusal or
  `INVALID_STATE`/`NOT_FOUND`), `answered_at`. Status rows are events (core plus transfer
  `MILESTONE`), keyed on (`provider_id`, `event_id`).
- A request row without an answer is pending; files are deltas, never snapshots, so a trip
  missing from a file is never a cancellation. This matches ThroughRide's rule 1 approach.
- Not carried (use the API): eligibility attestations, fare amount, rider e-mail, tenders,
  holds, custody.
- `tds_conform.csvfile` reads, writes and converts (`row_to_json`, `json_to_row`),
  `idempotency_key(row)` gives an `Idempotency-Key` for bridges, and `file_problems` /
  `exchange_problems` / `tds-conform check-csv` check files.
- **For ThroughRide (spec §8.2 "File adapter", I4):** key applied rows on the binding's key
  above, not on (provider, provider trip id, leg): the provider trip id is unknown until the
  answer, and the action and revision are needed to tell a modify from a cancel. Store
  `provider_trip_id` from the answer row as `external_ref`. Per-provider column profiles stay
  adapter-local (Latin-1 fallback, other header names); the TDS profile is their default,
  and `json_to_row` / `row_to_json` can do the mapping for that default. A REJECTED answer is a
  rejection reason for the coordinator; offering the leg elsewhere stays a proposal (rule 1).


## v0.7.0 released (2026-10-24, local tag `v0.7.0`)

Answers queue item TQ-19 (rotate a subscription's signing secret over the API), the follow-up
noted under v0.6.0. Re-vendor from tag `v0.7.0` (`binding_version()` = `0.7.0`; generated event
streams change only in `schema_version`; generated subscribe responses gain
`signing_secret_id`). New example folder `secret-rotation/`.

**Secret rotation (SPEC.md §12.1).** Optional for providers, additive:
- A provider that supports it says `secret_rotation: true` on `/health` and returns
  `signing_secret_id` with each new subscription.
- `POST /subscriptions/{subscription_id}/secrets` (`Idempotency-Key`; optional body
  `{"overlap_s": …}`, 0 to 2592000, default SHOULD be 86400) answers 201
  `{subscription_id, secrets: [...]}`, newest first. Only the first entry carries
  `signing_secret`, and only in this response (or a replay under the same key). Older secrets
  get `expires_at` = now + overlap (never lengthened); `overlap_s: 0` cuts them off at once.
  409 `INVALID_STATE` when the provider's limit of active secrets is reached.
- `GET /subscriptions/{subscription_id}/secrets` lists active secrets (`secret_id`,
  `created_at`, `expires_at`), never values.
- `DELETE /subscriptions/{subscription_id}/secrets/{secret_id}` (`Idempotency-Key`) retires an
  older secret now (200, the secret without its value); the newest cannot be retired (409
  `INVALID_STATE`).
- While secrets overlap, each delivery attempt carries one `webhook-signature` `v1` entry per
  active secret (newest first) and `X-TDS-Signature` with the newest; nothing is signed with an
  expired secret, retries included. Receivers verify with every secret they hold
  (`verify_any(secrets, …)` already takes a list).
- Scopes: `events:subscribe`; another requester's subscription is `NOT_FOUND`.
- `tds-mock` implements it (`ProviderConfig.secret_rotation`, `default_overlap_s`,
  `max_overlap_s`, `max_active_secrets`); core scenario `secret_rotation` checks providers that
  declare it.
- **For ThroughRide:**
  - Inbound (`apps/providers`, TDS subscriptions to providers): keep a list of secrets per
    subscription, not one `signing_secret` field. Each needs `secret_id`, the encrypted value,
    and `expires_at`. Store `signing_secret_id` from the subscribe answer. Add a rotate action
    (admin or a scheduled task, idempotent per subscription and attempt) for providers whose
    `/health` says `secret_rotation: true`: call `POST …/secrets`, store the new secret, verify
    inbound deliveries with all unexpired secrets (`verify_any`), then `DELETE` the old one
    once a delivery has verified with the new one, or let it expire. Providers without the
    capability keep the out-of-band path.
  - Outbound (`apps/webhooks`, ThroughRide as sender): if partners manage their endpoints
    over an API, the same operations apply to `WebhookEndpoint`: several secrets with expiry,
    sign each attempt with every active one (`sign_standard([newest, *older], …)`, and `sign`
    with the newest). If endpoints are only managed in the UI, a "rotate secret" button with
    an overlap, showing the new value once, gives partners the same result.
  - The TDS fake (`tests/fakes/tds.py`) can replay `secret-rotation/` for these paths.


## v0.7.1 released (2026-10-24, local tag `v0.7.1`)

Answers queue item TQ-20 (let providers signal back-pressure so brokers can honour it).
Re-vendor from tag `v0.7.1` (`binding_version()` = `0.7.1`; generated event streams change
only in `schema_version`). New example files `errors/rate-limited.json` (429) and
`errors/temporarily-unavailable.json` (503).

**Back-pressure (SPEC.md §7.2).** Agreed, additive:
- Every operation may answer **429** `RATE_LIMITED` (this client is over the provider's limit)
  or **503** `TEMPORARILY_UNAVAILABLE` (the provider cannot take requests for now). Nothing is
  processed and the `Idempotency-Key` is not consumed.
- Providers SHOULD send the wait as `Retry-After` (RFC 9110 §10.2.3: delta-seconds, which
  providers SHOULD use, or an HTTP-date) **and** as `retry_after_s` (integer seconds) in the
  Problem Details. They SHOULD agree; a client that sees both uses the larger. `retry_after_s`
  MAY accompany any other 5xx. OpenAPI: `components/headers.yaml#/RetryAfter`, responses
  `TooManyRequests` / `ServiceUnavailable`.
- Requesters SHOULD wait at least that long and repeat the same request with the same
  `Idempotency-Key`. The hint is a minimum; waiting longer (own backoff) is fine. An
  unparseable `Retry-After` is ignored.
- A 429/503 is not a refusal: the trip is not `REJECTED`, and back-pressure alone is no
  reason to offer the trip to another provider.
- The same applies to webhook deliveries: a receiver may answer 429/503 with `Retry-After`;
  the sender SHOULD not attempt again sooner.
- `tds_conform.backpressure.retry_after_s(headers, body)` is the reference reading. `tds-mock`
  throttles with `ProviderConfig.rate_limit` / `/_sim/<id>/policy {"rate_limit": …}` and
  answers 503 after `POST /_sim/<id>/unavailable {"for_s": …}`. The kit retries unexpected
  429/503 after the hint and reports a missing or inconsistent hint as a warning (SHOULD), not
  a failure.
- **Differs from the proposal:** no `retry_after_s` on a trip "whose status is a transient
  `FAILED`". In the binding, trip `FAILED` is the terminal in-service outcome (the leg could
  not be completed, §8.1), never a transient booking failure, and a Trip carries no retry
  hint. ThroughRide's "`FAILED`-class" adapter outcomes are its own classification; on the
  wire they are 429, 503, other 5xx or network errors, which the Problem Details hint covers.
- **For ThroughRide:**
  - `adapters/tds.py`: on 429, 503 (and any 5xx) read `Retry-After` (digits = seconds;
    otherwise an HTTP-date via `email.utils.parsedate_to_datetime`, seconds until then, at
    least 0; anything else ignored) and the Problem Details `retry_after_s` (a non-negative
    integer, not a bool); take the larger as `AdapterResult.retry_after`. The next attempt is
    at max(that, the 1/2/5/15/30/60-minute backoff step), same Idempotency-Key. Implement the
    parser in the adapter (about 15 lines), since `tds_conform` is a dev-only dependency;
    test it against `tds_conform.backpressure.parse_retry_after` cases.
  - Do not map 429/503 to a rejection or to a Proposal for another provider. If the hinted
    wait would pass the leg's booking deadline or the tender's `respond_by`, surface it to the
    coordinator as an exception instead of waiting silently (rule 1 unchanged).
  - Outbound webhooks (`apps/webhooks`): when a partner endpoint answers 429/503 with
    `Retry-After`, schedule the next attempt no earlier than that (max with the existing
    schedule, with your own upper limit). Inbound TDS webhook endpoint: may answer 503 with
    `Retry-After` when overloaded.
  - Fakes (`tests/fakes/tds.py`): replay the two new error examples with a `Retry-After`
    header equal to their `retry_after_s` (examples hold bodies only).
