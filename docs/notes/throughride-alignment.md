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

### ThroughRide spec still names pre-v0.4 values
- **Where:** SPEC.md §10.3, §10.4, `leg.service_requirements`  ↔  ThroughRide `spec.md` §8.1 decisions 3, 5 and 10 and the mapping table
- **Mismatch:** ThroughRide's milestone table still lists `EN_ROUTE_TO_TRANSFER` / `ARRIVED_TRANSFER`, the custody-gap timer starts at `ARRIVED_TRANSFER`, the delay causes include `rider`, and the mapping says car seats have no value. The binding dropped the transfer milestones (#5), merged `RIDER` into `BOARDING` (#8) and has `CHILD_SEAT` (#6).
- **Why it matters:** An adapter written from ThroughRide's table would emit milestones the kit rejects.
- **Suggested resolution:** change ThroughRide: hub arrival = the feeder leg's `ARRIVED_DROPOFF` (the driver button can still say "Arrived at transfer"); start the custody-gap timer there; map `rider` → `BOARDING`; map `car_seat` → `CHILD_SEAT`.

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
