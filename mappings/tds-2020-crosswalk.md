# TDS 2020 crosswalk

Maps every field in `profiles/core` to its counterpart in the Transactional Data Specification
(TCRP G-16 / Report 210, 2020). `scripts/validate_examples.py` fails if a core field is missing
from this file and is not marked `x-tds-extension: true` in its schema.

**Everything below is unverified.** Fill in the TDS columns from the report itself. Describe and
cite; don't paste definitions (reuse terms not yet confirmed, see SPEC.md §16 Q3).

## Columns

| Column | Meaning |
|---|---|
| Field | `<schema-file>.<property>`; nested inline properties use further dots. Keep the backticks: the validator parses them. |
| TDS 2020 element | Element name as it appears in the report. `TODO(crosswalk)` until checked. |
| TDS section | Section or table reference in Report 210. |
| Status | `MATCH` (same meaning and name), `RENAMED` (same meaning, different name), `EXTENSION` (no TDS equivalent; also mark `x-tds-extension: true` in the schema), `TODO` (not yet checked). |
| Notes | Differences in type, cardinality or semantics; borrowed vocabulary. |

## Fields


### Address (`profiles/core/address.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `address.street` | TODO(crosswalk) | | TODO |  |
| `address.city` | TODO(crosswalk) | | TODO |  |
| `address.region` | TODO(crosswalk) | | TODO |  |
| `address.postal_code` | TODO(crosswalk) | | TODO |  |
| `address.country` | TODO(crosswalk) | | TODO |  |

### BookingWindow (`profiles/core/booking-window.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `booking-window.max_advance_s` | TODO(crosswalk) | | TODO | Provider booking policy (advance reservation limit). Check whether TDS 2020 describes provider service parameters such as advance booking; GTFS-Flex `booking_rules.txt` expresses it as `prior_notice_duration_max` (minutes, same-day booking) or `prior_notice_start_day` (prior-day booking). |
| `booking-window.min_notice_s` | TODO(crosswalk) | | TODO | GTFS-Flex `booking_rules.prior_notice_duration_min` (minutes; here seconds). |
| `booking-window.same_day` | TODO(crosswalk) | | TODO | GTFS-Flex `booking_type` 1 (same day) vs 2 (up to the prior day). The prior-day cutoff time (`prior_notice_last_time`) has no field here yet. |
| `booking-window.time_zone` | TODO(crosswalk) | | TODO | IANA zone, as GTFS `agency_timezone`. Protocol field. |

### CancelReason (`profiles/core/cancel-reason.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|

### CancelRequest (`profiles/core/cancel-request.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `cancel-request.reason` | TODO(crosswalk) | | TODO | Values in cancel-reason.yaml; check TDS cancellation vocabulary |
| `cancel-request.detail` | TODO(crosswalk) | | TODO |  |

### Cancellation (`profiles/core/cancellation.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `cancellation.reason` | TODO(crosswalk) | | TODO | Values in cancel-reason.yaml |
| `cancellation.cancelled_by` | TODO(crosswalk) | | TODO |  |
| `cancellation.detail` | TODO(crosswalk) | | TODO |  |
| `cancellation.cancelled_at` | TODO(crosswalk) | | TODO |  |

### Contact (`profiles/core/contact.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `contact.phone` | TODO(crosswalk) | | TODO |  |

### Eligibility (`profiles/core/eligibility.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `eligibility.program` | TODO(crosswalk) | | TODO | Also carries fact codes (`AGE_<N>_PLUS`, `AGE_UNDER_<N>`), modelled on the ISO/IEC 18013-5 mDL `age_over_NN` element: the threshold is shared, never the birth date. `DISABILITY`, `LOW_INCOME` and `RESIDENT_<CC>_<area>` (US: 5-digit county FIPS / census GEOID) follow SPEC.md §6.5.2. Check whether TDS 2020 has an age, disability, income or residency eligibility-category element, or a rider-category element for reporting. |
| `eligibility.attested_by` | TODO(crosswalk) | | TODO |  |
| `eligibility.valid_until` | TODO(crosswalk) | | TODO |  |
| `eligibility.verification_method` | TODO(crosswalk) | | TODO | Coarse version of OpenID Connect for Identity Assurance `verification` (trust framework, evidence type), without the evidence itself. Probably an extension. |
| `eligibility.verified_at` | TODO(crosswalk) | | TODO | Date only, like OIDC4IDA `verification.time` reduced to a day. |
| `eligibility.verifier_ref` | TODO(crosswalk) | | TODO | Names the issuer (mDL issuing authority, OpenID provider, verifiable-credential issuer), never the credential. Probably an extension. |

### Error (`profiles/core/error.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `error.type` | TODO(crosswalk) | | TODO | RFC 9457 field |
| `error.title` | TODO(crosswalk) | | TODO | RFC 9457 field |
| `error.status` | TODO(crosswalk) | | TODO | RFC 9457 field |
| `error.detail` | TODO(crosswalk) | | TODO | RFC 9457 field |
| `error.instance` | TODO(crosswalk) | | TODO | RFC 9457 field |
| `error.code` | TODO(crosswalk) | | TODO |  |
| `error.bookable_from` | TODO(crosswalk) | | TODO | Protocol field (retry hint on OUTSIDE_BOOKING_WINDOW); likely no TDS equivalent |

### TripCancelledEvent (`profiles/core/event-trip-cancelled.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `event-trip-cancelled.event_type` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event-trip-cancelled.payload` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event-trip-cancelled.payload.cancellation` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |

### TripScheduledEvent (`profiles/core/event-trip-scheduled.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `event-trip-scheduled.event_type` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event-trip-scheduled.payload` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event-trip-scheduled.payload.scheduled_pickup` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event-trip-scheduled.payload.scheduled_dropoff` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event-trip-scheduled.payload.vehicle_ref` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |

### TripStatusChangedEvent (`profiles/core/event-trip-status-changed.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `event-trip-status-changed.event_type` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event-trip-status-changed.payload` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event-trip-status-changed.payload.status` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event-trip-status-changed.payload.previous_status` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |

### EventTypes (`profiles/core/event-types.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|

### Event (`profiles/core/event.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `event.event_id` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event.event_type` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event.occurred_at` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event.provider_id` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event.trip_id` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event.leg_id` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event.schema_version` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |
| `event.payload` | TODO(crosswalk) | | TODO | Event envelope/payload; check TDS status-message elements |

### FareFunding (`profiles/core/fare-funding.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `fare-funding.fare` | TODO(crosswalk) | | TODO |  |
| `fare-funding.funding_source` | TODO(crosswalk) | | TODO | Enum values (`funding-source.yaml`) unverified against TDS |
| `fare-funding.sponsor_ref` | TODO(crosswalk) | | TODO |  |
| `fare-funding.authorization_ref` | TODO(crosswalk) | | TODO |  |

### FundingSource (`profiles/core/funding-source.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|

### Health (`profiles/core/health.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `health.status` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `health.spec_versions` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `health.profiles` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `health.operated_via` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `health.typical_response_s` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `health.booking_window` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `health.booking_window_by_profile` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `health.booking_window_by_profile.paratransit` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `health.booking_window_by_profile.nemt` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `health.booking_window_by_profile.transfer` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |

### Leg (`profiles/core/leg.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `leg.leg_id` | TODO(crosswalk) | | TODO |  |
| `leg.pickup` | TODO(crosswalk) | | TODO |  |
| `leg.dropoff` | TODO(crosswalk) | | TODO |  |
| `leg.pickup_window` | TODO(crosswalk) | | TODO |  |
| `leg.dropoff_window` | TODO(crosswalk) | | TODO |  |
| `leg.scheduled_pickup` | TODO(crosswalk) | | TODO |  |
| `leg.scheduled_dropoff` | TODO(crosswalk) | | TODO |  |
| `leg.service_requirements` | TODO(crosswalk) | | TODO | Enum values unverified against TDS; check especially `HAND_TO_HAND`, `CHILD_SEAT`, `STRETCHER` |
| `leg.vehicle_ref` | TODO(crosswalk) | | TODO |  |

### Location (`profiles/core/location.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `location.lat` | TODO(crosswalk) | | TODO |  |
| `location.lon` | TODO(crosswalk) | | TODO |  |
| `location.name` | TODO(crosswalk) | | TODO |  |
| `location.address` | TODO(crosswalk) | | TODO |  |
| `location.gtfs_stop_id` | TODO(crosswalk) | | TODO | Borrowed from GTFS `stop_id` (see gtfs-flex.md, to be written) |
| `location.flex_location_id` | TODO(crosswalk) | | TODO | Borrowed from GTFS-Flex `locations.geojson` id / location group |
| `location.access_notes` | TODO(crosswalk) | | TODO |  |

### Money (`profiles/core/money.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `money.amount` | TODO(crosswalk) | | TODO | Decimal string; check TDS representation |
| `money.currency` | TODO(crosswalk) | | TODO |  |

### Party (`profiles/core/party.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `party.companions` | TODO(crosswalk) | | TODO |  |
| `party.attendants` | TODO(crosswalk) | | TODO |  |
| `party.service_animals` | TODO(crosswalk) | | TODO |  |

### RefusalReason (`profiles/core/refusal-reason.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|

### Rider (`profiles/core/rider.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `rider.rider_ref_id` | TODO(crosswalk) | | TODO |  |
| `rider.display_name` | TODO(crosswalk) | | TODO |  |
| `rider.contact` | TODO(crosswalk) | | TODO |  |
| `rider.contact_consent` | TODO(crosswalk) | | TODO | Privacy control; may have no TDS equivalent |
| `rider.language` | TODO(crosswalk) | | TODO |  |
| `rider.eligibility` | TODO(crosswalk) | | TODO |  |

### SubscriptionRequest (`profiles/core/subscription-request.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `subscription-request.callback_url` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `subscription-request.event_types` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `subscription-request.cloudevents` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent. Borrows the CloudEvents 1.0 HTTP binding (binary mode). |

### Subscription (`profiles/core/subscription.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `subscription.subscription_id` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `subscription.callback_url` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `subscription.event_types` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `subscription.signing_secret` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |
| `subscription.cloudevents` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent. Borrows the CloudEvents 1.0 HTTP binding (binary mode). |
| `subscription.created_at` | TODO(crosswalk) | | TODO | Protocol field; likely no TDS equivalent |

### TimeWindow (`profiles/core/time-window.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `time-window.earliest` | TODO(crosswalk) | | TODO |  |
| `time-window.latest` | TODO(crosswalk) | | TODO |  |
| `time-window.preferred` | TODO(crosswalk) | | TODO |  |

### TripModification (`profiles/core/trip-modification.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `trip-modification.pickup_window` | TODO(crosswalk) | | TODO | Same as `leg.`/`trip.` counterpart; check TDS change-request message |
| `trip-modification.dropoff_window` | TODO(crosswalk) | | TODO | Same as `leg.`/`trip.` counterpart; check TDS change-request message |
| `trip-modification.party` | TODO(crosswalk) | | TODO | Same as `leg.`/`trip.` counterpart; check TDS change-request message |
| `trip-modification.notes_to_driver` | TODO(crosswalk) | | TODO | Same as `leg.`/`trip.` counterpart; check TDS change-request message |

### TripRequest (`profiles/core/trip-request.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `trip-request.trip_id` | TODO(crosswalk) | | TODO | Same as `trip.` counterpart |
| `trip-request.requester_id` | TODO(crosswalk) | | TODO | Same as `trip.` counterpart |
| `trip-request.provider_id` | TODO(crosswalk) | | TODO | Same as `trip.` counterpart |
| `trip-request.rider_ref` | TODO(crosswalk) | | TODO | Same as `trip.` counterpart |
| `trip-request.party` | TODO(crosswalk) | | TODO | Same as `trip.` counterpart |
| `trip-request.legs` | TODO(crosswalk) | | TODO | Same as `trip.` counterpart |
| `trip-request.fare_funding` | TODO(crosswalk) | | TODO | Same as `trip.` counterpart |
| `trip-request.notes_to_driver` | TODO(crosswalk) | | TODO | Same as `trip.` counterpart |

### TripStatus (`profiles/core/trip-status.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|

### Trip (`profiles/core/trip.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `trip.trip_id` | TODO(crosswalk) | | TODO |  |
| `trip.provider_trip_id` | TODO(crosswalk) | | TODO |  |
| `trip.requester_id` | TODO(crosswalk) | | TODO |  |
| `trip.provider_id` | TODO(crosswalk) | | TODO |  |
| `trip.status` | TODO(crosswalk) | | TODO | Values in trip-status.yaml; check TDS status vocabulary |
| `trip.rider_ref` | TODO(crosswalk) | | TODO |  |
| `trip.party` | TODO(crosswalk) | | TODO |  |
| `trip.legs` | TODO(crosswalk) | | TODO | Core restricts to exactly one leg |
| `trip.fare_funding` | TODO(crosswalk) | | TODO |  |
| `trip.notes_to_driver` | TODO(crosswalk) | | TODO |  |
| `trip.offer_expires_at` | TODO(crosswalk) | | TODO |  |
| `trip.cancellation` | TODO(crosswalk) | | TODO |  |
| `trip.created_at` | TODO(crosswalk) | | TODO |  |
| `trip.updated_at` | TODO(crosswalk) | | TODO |  |

### VehicleRef (`profiles/core/vehicle-ref.yaml`)

| Field | TDS 2020 element | TDS section | Status | Notes |
|---|---|---|---|---|
| `vehicle-ref.vehicle_id` | TODO(crosswalk) | | TODO |  |
| `vehicle-ref.description` | TODO(crosswalk) | | TODO |  |
| `vehicle-ref.plate` | TODO(crosswalk) | | TODO |  |
