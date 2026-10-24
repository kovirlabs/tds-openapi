"""Conformance reports: JSON for machines, a self-contained HTML page for people."""

import json
from dataclasses import asdict
from html import escape
from pathlib import Path

from tds_conform.runner import ScenarioResult

OUTCOME_ORDER = ("PASS", "FAIL", "ERROR", "INCONCLUSIVE")


def build(meta: dict, results: list[ScenarioResult]) -> dict:
    summary = {o: sum(r.outcome == o for r in results) for o in OUTCOME_ORDER}
    # Failed SHOULD-level (advisory) checks: shown as warnings, never affect the verdict.
    summary["warnings"] = sum(not c.passed and c.advisory for r in results for s in r.steps for c in s.checks)
    level = meta["profile"].upper() if summary["PASS"] == len(results) and results else None
    return {**meta, "conformant": level is not None, "summary": summary, "scenarios": [asdict(r) for r in results]}


def write(report: dict, out_dir: Path) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, html_path = out_dir / "report.json", out_dir / "report.html"
    json_path.write_text(json.dumps(report, indent=2) + "\n")
    html_path.write_text(render_html(report))
    return json_path, html_path


def render_html(report: dict) -> str:
    verdict = f"Conformant: {escape(_level(report['profile']))}" if report["conformant"] else "Not conformant"
    rows = "".join(
        f"<tr><td><a href='#{escape(s['id'])}'>{escape(s['id'])}</a></td><td>{escape(s['title'])}</td>"
        f"<td class='{s['outcome'].lower()}'>{s['outcome']}</td></tr>"
        for s in report["scenarios"]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TDS conformance report</title>
<style>
:root {{ --bg:#fff; --fg:#1b1f24; --muted:#59636e; --line:#d1d9e0; --pass:#1a7f37; --fail:#cf222e; --warn:#9a6700; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#0d1117; --fg:#e6edf3; --muted:#9198a1; --line:#3d444d;
  --pass:#3fb950; --fail:#f85149; --warn:#d29922; }} }}
body {{ background:var(--bg); color:var(--fg); font:15px/1.5 system-ui,sans-serif; margin:0 auto; max-width:960px;
  padding:16px; }}
h1 {{ font-size:1.5rem; margin-bottom:.25rem; }} .muted {{ color:var(--muted); }}
table {{ border-collapse:collapse; width:100%; margin:1rem 0; }} td,th {{ border-bottom:1px solid var(--line);
  padding:.35rem .5rem; text-align:left; vertical-align:top; }}
.pass {{ color:var(--pass); font-weight:600; }} .fail,.error {{ color:var(--fail); font-weight:600; }}
.inconclusive {{ color:var(--warn); font-weight:600; }}
details {{ border:1px solid var(--line); border-radius:6px; margin:.5rem 0; padding:.25rem .75rem; }}
pre {{ overflow-x:auto; font-size:12px; background:color-mix(in srgb, var(--line) 25%, transparent); padding:.5rem; }}
.verdict {{ font-size:1.2rem; font-weight:600; }}
</style></head><body>
<h1>TDS conformance report</h1>
<p class="muted">Target <code>{escape(report["target"])}</code> · profile {escape(report["profile"])} ·
spec {escape(report["spec_version"])} · kit {escape(report["kit_version"])} · {escape(report["started_at"])}</p>
<p class="verdict {"pass" if report["conformant"] else "fail"}">{verdict}</p>
<table><thead><tr><th>Scenario</th><th>What it checks</th><th>Outcome</th></tr></thead><tbody>{rows}</tbody></table>
{"".join(_scenario_html(s) for s in report["scenarios"])}
</body></html>
"""


def _level(profile: str) -> str:
    return {"core": "Core", "transfer": "Core + Transfer"}.get(profile, profile)


def _scenario_html(s: dict) -> str:
    steps = "".join(_step_html(step) for step in s["steps"])
    note = f"<p class='muted'>{escape(s['note'])}</p>" if s["note"] else ""
    badge = f"<span class='{s['outcome'].lower()}'>{s['outcome']}</span>"
    return f"<h2 id='{escape(s['id'])}'>{escape(s['id'])} {badge}</h2>{note}{steps}"


def _step_html(step: dict) -> str:
    ok = all(c["passed"] or c.get("advisory") for c in step["checks"])
    checks = "".join(
        f"<li class='{_check_class(c)}'>{_check_mark(c)} {escape(c['name'])}"
        f"{' — ' + escape(c['detail']) if c['detail'] else ''}</li>"
        for c in step["checks"]
    )
    req, resp = step["request"], step["response"] or {}
    mark = f"<span class='{'pass' if ok else 'fail'}'>{'✓' if ok else '✗'}</span>"
    call = f"<code>{escape(req['method'])} {escape(req['path'])}</code> → {resp.get('status', '—')}"
    return (
        f"<details{'' if ok else ' open'}><summary>{mark} {escape(step['label'])} {call}"
        f"</summary><ul>{checks}</ul><p>Request</p><pre>{escape(json.dumps(req, indent=2))}</pre>"
        f"<p>Response</p><pre>{escape(json.dumps(resp, indent=2))}</pre></details>"
    )


def _check_class(check: dict) -> str:
    return "pass" if check["passed"] else "inconclusive" if check.get("advisory") else "fail"


def _check_mark(check: dict) -> str:
    return "✓" if check["passed"] else "!" if check.get("advisory") else "✗"
