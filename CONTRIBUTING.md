# Contributing

Thanks for helping. Issues and pull requests are welcome from agencies, vendors and anyone implementing TDS.

## Sign your commits (DCO)

This project uses the [Developer Certificate of Origin](https://developercertificate.org/) instead of a CLA. Every commit must carry a sign-off certifying you have the right to submit it under the project's licences:

```
Signed-off-by: Your Name <you@example.org>
```

`git commit -s` adds it for you. Pull requests with unsigned commits can't be merged.

## Before you open a pull request

1. `npm run lint` passes.
2. `uv run scripts/validate_examples.py` passes: every schema and example validates.
3. Any new or changed `profiles/core` field is in `mappings/tds-2020-crosswalk.md`, or marked `x-tds-extension: true` with a reason.
4. `CHANGELOG.md` has an entry under *Unreleased*.
5. If behaviour changed, an example in `examples/` shows it (and, once the conformance kit exists, a scenario covers it).

## Ground rules

- **Vendor-neutral core.** No product- or company-specific fields in `profiles/core`.
- **Don't copy TCRP report text.** Describe the concept and cite it ("corresponds to TDS 2020 element X"). Reuse terms for the report are not yet confirmed.
- **Don't guess TDS names.** If you aren't sure what TDS calls something, write `TODO(crosswalk)` and say so in the PR.
- **Privacy by default.** Rider data is the minimum needed for one leg. No diagnoses or health data in core; express needs as service requirements (e.g. `WHEELCHAIR_LIFT`).
- **Borrow vocabulary** from GTFS, GTFS-Flex, NeTEx or EDI before inventing terms, and record the borrowing in `mappings/`.
- **Additive changes only** within a minor version. Breaking changes need an RFC in `docs/rfcs/`.

## Conventions

YAML for schemas, JSON for examples. `snake_case` properties, `UPPER_SNAKE_CASE` enum values, RFC 3339 times with offset, durations as integer seconds with an `_s` suffix, WGS84 `lat`/`lon`. One schema per file with `$id`, `title`, `description` and `examples`; every property has a `description`. The validator enforces most of this.
