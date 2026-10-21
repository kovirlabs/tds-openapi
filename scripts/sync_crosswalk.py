"""Add a TODO row to mappings/tds-2020-crosswalk.md for every profiles/core field that lacks one.

Existing rows (and anything filled in by hand) are kept; rows are regrouped by schema
in file order. Run after adding core fields, then fill in the TDS columns.

Usage: uv run scripts/sync_crosswalk.py
"""

import re

import validate_examples as ve

PROTOCOL = "Protocol field; likely no TDS equivalent"
NOTES = {
    "trip-request.": "Same as `trip.` counterpart",
    "trip-modification.": "Same as `leg.`/`trip.` counterpart; check TDS change-request message",
    "health.": PROTOCOL,
    "subscription": PROTOCOL,
    "event": "Event envelope/payload; check TDS status-message elements",
}


def default_note(key: str) -> str:
    return next((note for prefix, note in NOTES.items() if key.startswith(prefix)), "")


def main() -> None:
    text = ve.CROSSWALK.read_text()
    head, _, _ = text.partition("## Fields\n")
    rows = {m.group(1): m.group(0) for m in re.finditer(r"^\| `([^`]+)` \|.*$", text, re.M)}
    out, added = [head + "## Fields\n"], 0
    for path, schema in ve.load_schemas().items():
        if path.parent.name != "core":
            continue
        out.append(f"\n### {schema['title']} (`profiles/core/{path.name}`)\n")
        out.append("| Field | TDS 2020 element | TDS section | Status | Notes |\n|---|---|---|---|---|")
        for prop, _ in ve.iter_properties(schema):
            key = f"{path.stem}.{prop}"
            if key not in rows:
                added += 1
            out.append(rows.get(key) or f"| `{key}` | TODO(crosswalk) | | TODO | {default_note(key)} |")
    ve.CROSSWALK.write_text("\n".join(out) + "\n")
    print(f"{added} row(s) added")


if __name__ == "__main__":
    main()
