# For agencies: asking for TDS connectivity in procurements

This page is for transit agencies, mobility managers, human-service transportation
coordinators and state DOTs writing a request for proposals (RFP) or contract for
scheduling, dispatch, brokerage or microtransit software. It explains what to ask for so
that the system you buy can book trips with other providers' systems, and gives clause
language you can adapt.

Nothing here is legal advice, and the clauses are a starting point: your procurement
office will fit them to your templates and rules. The binding is a draft (see
[SPEC.md](../SPEC.md)); cite a specific version, and expect field names to change before 1.0.

## Why ask for it

Riders whose trips cross service areas often need two or more providers. Without a shared
interface, staff book each leg by phone or email and nobody watches the connection. The
Transactional Data Specification (TDS, TCRP Report 210, 2020) describes what a trip-booking
exchange should contain. This project turns it into an interface a vendor can build and a
test kit that shows whether they did:

- **Core**: request, confirm, modify and cancel a trip; status updates by signed webhook.
- **Core + Transfer**: everything in Core, plus protected connections between providers:
  tenders with deadlines, hold requests, trip milestones, arrival-time ranges ("ETA bands"),
  and custody records for riders who must not be left alone.

Asking for conformance at a named level is more precise than "supports TDS" or "has an
open API", and it can be checked before contract award.

## What to ask for

| Need | Ask for | How you check it |
|---|---|---|
| Other systems can book trips on yours | Core conformance, as a provider | Conformance report, Core level |
| Your system can book trips on other providers | Core, as a requester | Demonstration against the reference mock (`tds-mock`) |
| Protected transfers between providers | Core + Transfer, as a provider; as a broker if the vendor coordinates transfers | Conformance report, Core + Transfer; captured event log checked with `tds-conform check-events` |
| Small providers without software can take part | Acceptance of providers answered through a broker proxy | `/health` reports `operated_via: BROKER_PROXY`; asynchronous answers are conformant |
| Cost allocation across funders | Settlement split records per leg (SPEC.md §10.8) | Sample export validated against `settlement-split.yaml` |
| Escalations visible to partners | Broker exception events, including `ESCALATED` (who is already acting on a problem) | Sample `EXCEPTION_RAISED` / `EXCEPTION_RESOLVED` events; counts in the vendor's reports |
| Eligibility without re-collecting personal data | Acceptance of eligibility attestations, including fact codes such as `AGE_60_PLUS`, with a stated policy of which attesting parties and `verification_method` values the provider accepts (SPEC.md §6.5.1); refusals as `ELIGIBILITY_NOT_ACCEPTED`, not a request for date of birth | The policy in the agreement; Core report includes `eligibility_attestation` |
| Reporting by rider category and funder rules | Trips counted by rider category (senior, disabled, both, general) from attested fact codes `AGE_<N>_PLUS`, `DISABILITY`, `LOW_INCOME`, `RESIDENT_US_<county FIPS>` (SPEC.md §6.5.2); the agreement lists which facts the provider needs, and no other rider data is sent | The fact list in the agreement; sample payloads |
| Privacy | Data minimization as specified, no health data on the wire | Review of sample payloads and the vendor's data-handling statement |

A conformance report comes from running the open-source kit against the vendor's endpoint:

```sh
uv run tds-conform run --url https://vendor.example/tds --token "$TOKEN" --profile transfer \
  --trip-template trip-in-our-service-area.json
```

It writes a self-contained `report.html` and `report.json`. A level is met only when every
scenario at that level passes; INCONCLUSIVE (the provider refused the test trip) is not a
pass, so give vendors a trip template their service area can serve.

## Sample clauses

Replace the bracketed parts. "Binding" means the TDS OpenAPI binding published at
[repository URL], version [0.5.0 or later].

**Interface.**
> The Contractor shall provide an application programming interface that conforms to the
> TDS OpenAPI binding, version [x.y.z], at the [Core | Core + Transfer] conformance level,
> acting as a [provider | requester | broker]. The Contractor shall keep the interface
> conformant with later minor versions of the binding within [six] months of their
> publication, at no additional cost.

**Evidence of conformance.**
> Proposers shall submit a conformance report produced by the binding's conformance kit
> (`tds-conform`), version [x.y.z], run against a test endpoint of the proposed system, with
> all scenarios at the required level passing. The Agency may rerun the kit against the
> Contractor's test endpoint before award and during acceptance testing.

**Test environment.**
> The Contractor shall maintain a test endpoint, separate from production and holding no
> real rider data, that the Agency and its partner providers may use for integration
> testing for the term of the contract.

**Asynchronous answers and broker-proxied providers.**
> The Contractor's system, when acting as a requester or broker, shall accept asynchronous
> decisions (HTTP 202 followed by an event) as conformant, shall set response deadlines no
> shorter than a provider's declared `typical_response_s`, and shall interoperate with
> providers whose endpoint is operated by a broker on their behalf
> (`operated_via: BROKER_PROXY`).

**Human approval.**
> The Contractor's system shall not book a trip with a provider the coordinator did not
> choose, or cancel a booked trip, without a person's approval, except under rules the
> Agency has approved in writing and that the system publishes as a fallback policy with
> `approval: PRE_AGREED`.

**Events and security.**
> The Contractor shall deliver and accept events as signed webhooks as specified by the
> binding (HMAC-SHA256 signature over timestamp and body, at-least-once delivery,
> deduplication by `event_id`), and shall authenticate API clients with OAuth 2.0 client
> credentials. All traffic shall use HTTPS.

**Data minimization and privacy.**
> The Contractor shall share with each provider only the data that provider needs to serve
> its leg, as defined by the binding. Rider identifiers sent to providers shall be
> pseudonymous and differ between providers. Accessibility needs shall be sent as service
> requirements, never as diagnoses or health conditions. Rider contact details shall be
> sent only where the rider has consented (`contact_consent`). Free-text fields shall not
> carry health information.

**Supervised riders.**
> For riders who must not be left unattended, the Contractor's system shall record a
> custody hand-off at each pickup and drop-off as specified by the binding, and shall not
> mark such a leg completed without one.

**Settlement data.**
> The Contractor shall make available, for each completed or cancelled multi-provider
> journey, a settlement split record conforming to the binding's `settlement-split`
> schema, including the funding source of each leg.

**Openness.**
> Interface definitions, field mappings and documentation the Contractor produces to meet
> these requirements may be shared by the Agency with partner providers and published.
> The Contractor shall not charge partner providers or the Agency per-connection or
> per-transaction fees for use of the interface.

## Evaluating proposals

- Ask for the conformance report and the kit version used; check the level and that
  nothing is INCONCLUSIVE.
- Ask which roles the system plays (provider, requester, broker) and which profiles
  `/health` lists.
- For transfers, ask for a recorded run of one of the scenarios in `examples/` (for
  instance `late-first-leg-hold`) against the vendor's system, and its event log.
- Ask how the vendor handles providers without software (broker proxy) and how long
  asynchronous answers usually take.
- Treat "TDS compatible" without a report as unverified.

## What this does not cover

Trip planning and discovery (use GTFS-Flex), scheduling algorithms, payment processing
and rider identity are out of scope (SPEC.md §3). Settlement records are data for
reconciliation; who pays for what stays in your agreements. The paratransit and NEMT
profiles are drafts (SPEC.md §11). They have conformance levels (Core + Paratransit,
Core + NEMT), but their fields may still change, so ask for them as "preferred" rather than
pass/fail for now. In both, eligibility
and authorization are references only, and no diagnosis or health data is exchanged.
