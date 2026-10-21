"""Command-line entry points: `tds-conform` (run the kit) and `tds-mock` (run the reference provider)."""

import argparse
import contextlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx

import tds_conform.scenarios  # noqa: F401  (registers scenarios)
from tds_conform import SPEC_VERSION, __version__, report
from tds_conform.receiver import Receiver
from tds_conform.runner import SCENARIOS, Settings, default_trip_template, run_scenario

# Each level is Core plus its profile's scenarios.
PROFILE_SCENARIOS = {
    "core": ("core",),
    "transfer": ("core", "transfer"),
    "paratransit": ("core", "paratransit"),
    "nemt": ("core", "nemt"),
}


def conform_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tds-conform", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list scenarios")
    run = sub.add_parser("run", help="run scenarios against a provider endpoint")
    run.add_argument("--url", required=True, help="base URL of the provider API")
    run.add_argument("--profile", default="core", choices=list(PROFILE_SCENARIOS), help="conformance level to test")
    run.add_argument("--scenario", action="append", help="run only this scenario (repeatable)")
    run.add_argument("--token", help="bearer token (default: the requester id, which is what tds-mock expects)")
    run.add_argument("--requester-id", help="requester_id to send (default: from the trip template)")
    run.add_argument("--provider-id", help="provider_id to send (default: from the trip template)")
    run.add_argument("--trip-template", type=Path, help="TripRequest JSON to base test trips on")
    run.add_argument("--max-wait", type=float, help="seconds to wait for an async decision (default: 3x typical)")
    run.add_argument(
        "--listen",
        metavar="HOST:PORT",
        help="run the webhook receiver here (default: 127.0.0.1:<free port> for localhost targets)",
    )
    run.add_argument("--callback-url", help="public URL that reaches --listen (for remote providers)")
    run.add_argument("--report-dir", type=Path, default=Path("tds-conform-report"), help="where to write reports")
    check = sub.add_parser("check-events", help="check a captured event log (milestones, custody, heartbeats)")
    check.add_argument("file", type=Path, help="JSON array of events, as delivered to your webhook")
    check.add_argument(
        "--interchange",
        type=Path,
        action="append",
        default=[],
        help="interchange or tender-request JSON: marks supervised legs and heartbeat feeders",
    )
    check.add_argument(
        "--supervised",
        action="append",
        default=[],
        metavar="PROVIDER/TRIP/LEG",
        help="a leg that requires custody records (repeatable)",
    )
    fz = sub.add_parser("fuzz", help="fuzz every operation against the OpenAPI document (needs tds-conform[fuzz])")
    fz.add_argument("--url", required=True, help="base URL of the provider API")
    fz.add_argument("--spec", type=Path, help="bundled OpenAPI document (default: dist/openapi.yaml in the checkout)")
    fz.add_argument("--token", help="bearer token (default: the requester id, which is what tds-mock expects)")
    fz.add_argument("--requester-id", default="throughride-demo", help="requester_id put into generated bodies")
    fz.add_argument("--provider-id", default="aaata-a-ride", help="provider_id put into generated bodies")
    fz.add_argument("--max-examples", type=int, default=25, help="generated requests per operation and mode")
    fz.add_argument("--mode", choices=["positive", "negative"], action="append", help="default: both")
    fz.add_argument("--operation", action="append", help='only this operation, e.g. "POST /trips" (repeatable)')
    fz.add_argument("--seed", type=int, default=0, help="Hypothesis seed (default 0: reproducible runs)")
    fz.add_argument("--report-dir", type=Path, default=Path("tds-conform-report"), help="where to write fuzz.json")
    args = parser.parse_args(argv)

    if args.command == "fuzz":
        return _fuzz(args)
    if args.command == "list":
        for sid, (profile, title, _) in SCENARIOS.items():
            print(f"{sid:24} {profile:12} {title}")
        return 0
    if args.command == "check-events":
        return _check_events(args)

    template = json.loads(args.trip_template.read_text()) if args.trip_template else default_trip_template()
    requester_id = args.requester_id or template["requester_id"]
    settings = Settings(
        url=args.url.rstrip("/"),
        token=args.token or requester_id,
        requester_id=requester_id,
        provider_id=args.provider_id or template["provider_id"],
        trip_template=template,
        max_wait_s=args.max_wait,
    )
    profiles = PROFILE_SCENARIOS[args.profile]
    wanted = args.scenario or [sid for sid, (profile, _, _) in SCENARIOS.items() if profile in profiles]
    unknown = set(wanted) - SCENARIOS.keys()
    if unknown:
        parser.error(f"unknown scenario(s): {', '.join(sorted(unknown))}")

    listen = args.listen or ("127.0.0.1:0" if urlsplit(settings.url).hostname in ("localhost", "127.0.0.1") else None)
    receiver = Receiver(*_host_port(listen), public_url=args.callback_url) if listen else contextlib.nullcontext()
    started = datetime.now(UTC).isoformat(timespec="seconds")
    with receiver as active, httpx.Client(base_url=settings.url, timeout=30) as client:
        settings.receiver = active
        try:
            health = client.get("/health").json()
        except (httpx.HTTPError, ValueError) as exc:
            print(f"cannot reach {settings.url}/health: {exc}", file=sys.stderr)
            return 2
        typical = health.get("typical_response_s", 0) if isinstance(health, dict) else 0
        results = []
        for sid in wanted:
            result = run_scenario(sid, settings, client, typical)
            print(f"{result.outcome:13} {sid}{'  (' + result.note + ')' if result.note else ''}")
            results.append(result)

    meta = {
        "kit_version": __version__,
        "spec_version": SPEC_VERSION,
        "profile": args.profile,
        "target": settings.url,
        "started_at": started,
        "health": health,
    }
    built = report.build(meta, results)
    json_path, html_path = report.write(built, args.report_dir)
    print(f"\n{'CONFORMANT' if built['conformant'] else 'NOT CONFORMANT'} ({args.profile}) · {json_path} · {html_path}")
    return 0 if built["conformant"] else 1


def _fuzz(args) -> int:
    from tds_conform.fuzz import fuzz, write_report

    try:
        results = fuzz(
            args.url.rstrip("/"),
            args.token or args.requester_id,
            args.requester_id,
            args.provider_id,
            spec=args.spec,
            max_examples=args.max_examples,
            modes=tuple(args.mode or ("positive", "negative")),
            include=set(args.operation) if args.operation else None,
            seed=args.seed,
        )
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 2
    for r in results:
        print(f"{r.status:8} {r.mode:9} {r.operation}")
        if r.status in ("FAIL", "ERROR"):
            print("         " + r.detail.replace("\n", "\n         "))
    path = write_report(results, args.report_dir)
    bad = [r for r in results if r.status in ("FAIL", "ERROR")]
    print(f"\n{len(bad)} operation/mode pair(s) failed · {path}" if bad else f"\nno failures · {path}")
    return 1 if bad else 0


def _host_port(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    return host or "127.0.0.1", int(port or 0)


def _check_events(args) -> int:
    from tds_conform.events import (
        broker_event_problems,
        custody_problems,
        dedupe,
        escalation_problems,
        event_errors,
        heartbeat_problems,
        milestone_problems,
    )

    events = json.loads(args.file.read_text())
    supervised = {tuple(s.split("/")) for s in args.supervised}
    feeders = {}
    for path in args.interchange:
        doc = json.loads(path.read_text())
        ix = doc.get("interchange", doc)
        legs = [(ix[r]["provider_id"], ix[r]["trip_id"], ix[r]["leg_id"]) for r in ("from", "to")]
        if ix.get("supervision_required"):
            supervised.update(legs)
        if ix.get("protection") == "PROTECTED":
            feeders[legs[0]] = ix.get("heartbeat_s", 60)
    checks = {
        "events match their schemas": [f"{e.get('event_id')}: {m}" for e in dedupe(events) for m in event_errors(e)],
        "milestone_sequence": milestone_problems(events),
        "custody_required": custody_problems(events, supervised),
        "eta_heartbeat_staleness": heartbeat_problems(events, feeders),
        "exception events only from brokers": broker_event_problems(events),
        "escalations": escalation_problems(events),
    }
    for name, problems in checks.items():
        print(f"{'PASS' if not problems else 'FAIL':5} {name}")
        for problem in problems:
            print(f"      {problem}")
    return 0 if not any(checks.values()) else 1


def mock_main(argv: list[str] | None = None) -> int:
    from tds_conform.mock.network import Dispatcher, Network
    from tds_conform.mock.provider import ProviderConfig
    from tds_conform.mock.server import make_server

    parser = argparse.ArgumentParser(
        prog="tds-mock",
        description="Stateful reference provider(s) for the core and transfer APIs. "
        "Each --provider gets base URL /p/<id>; un-prefixed URLs go to the first.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4010)
    parser.add_argument(
        "--provider",
        action="append",
        metavar="PROVIDER_ID",
        help="run a provider with this id (repeatable; default: one that accepts any provider_id)",
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="the demo providers (aaata-a-ride, oakland-go, fallback county-connector), 10 s heartbeats",
    )
    parser.add_argument(
        "--decision",
        choices=["sync", "async"],
        default="sync",
        help="sync: decide in the response; async: 202, decided after --decision-delay",
    )
    parser.add_argument("--decision-delay", type=float, default=2.0, help="seconds (async mode)")
    parser.add_argument("--schedule-delay", type=float, default=1.0, help="seconds from CONFIRMED to SCHEDULED")
    parser.add_argument("--operated-via", choices=["DIRECT", "BROKER_PROXY"], default="DIRECT")
    parser.add_argument(
        "--unsupported",
        nargs="*",
        default=[],
        metavar="REQUIREMENT",
        help="service_requirements to refuse with UNSUPPORTED_REQUIREMENT",
    )
    parser.add_argument("--heartbeat", type=int, help="seconds between ETA heartbeats (default: the interchange's)")
    parser.add_argument("--retry-base", type=float, default=1.0, help="first webhook retry delay in seconds")
    parser.add_argument("--quiet", action="store_true", help="no request log")
    args = parser.parse_args(argv)

    ids = args.provider or (["aaata-a-ride", "oakland-go", "county-connector"] if args.demo else [None])
    heartbeat = args.heartbeat or (10 if args.demo else None)
    configs = [
        ProviderConfig(
            provider_id=pid,
            decision=args.decision,
            decision_delay_s=args.decision_delay,
            schedule_delay_s=args.schedule_delay,
            operated_via=args.operated_via,
            unsupported_requirements=frozenset(args.unsupported),
            heartbeat_s=heartbeat,
        )
        for pid in ids
    ]
    network = Network(configs, dispatcher=Dispatcher(retry_base_s=args.retry_base))
    server = make_server(network, args.host, args.port, quiet=args.quiet)
    network.start()
    base = f"http://{args.host}:{args.port}"
    print(f"tds-mock ({args.decision}) on {base}  ·  Authorization: Bearer <requester_id>")
    for pid in network.providers:
        print(f"  provider {pid:16} {base}/p/{pid}" if pid != "mock-provider" else f"  any provider_id    {base}")
    print(f"  simulation        {base}/_sim/<provider_id>/...  (see tds_conform/mock/server.py)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        network.stop()
    return 0
