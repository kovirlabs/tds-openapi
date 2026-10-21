"""Broker-side walkthroughs of the transfer profile against tds-mock (SPEC.md §10, v0.4 demo).

The broker here plays ThroughRide's role: it tenders both legs of a linked journey,
watches milestones and ETA bands, asks the onward vehicle to hold when the feeder runs
late, re-tenders when a provider lets a tender expire, and collects custody records for
a supervised rider. Milestones are driven through tds-mock's /_sim controls.

Two modes:
  tds-demo run [--url URL] [--scenario NAME]   narrate a scenario live against a running
                                                 `tds-mock --demo` (1 demo minute = 1 s)
  tds-demo generate-examples [--out examples]   run every scenario in-process on a fake
                                                 clock and write examples/<scenario>/
"""

import argparse
import itertools
import json
import shutil
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx

from tds_conform.data import data_root, schema_id
from tds_conform.runner import REQUEST_SCHEMAS, RESPONSE_SCHEMAS

A, B, C = "aaata-a-ride", "oakland-go", "county-connector"
REQUESTER = "throughride-demo"
EASTERN = timezone(timedelta(hours=-5), "EST")
HOME = {"lat": 42.2459, "lon": -83.6157, "name": "Rider's home (Ypsilanti)", "access_notes": "Side door, ramp"}
HUB = {"lat": 42.4802, "lon": -83.4755, "name": "County-line transfer hub", "access_notes": "Covered bay 2"}
CLINIC = {"lat": 42.6064, "lon": -83.1498, "name": "Corewell Health Troy", "access_notes": "Main entrance"}
TRANSFER_OPS = {
    "POST /tenders",
    "GET /tenders/{tender_id}",
    "POST /trips/{trip_id}/hold",
    "GET /trips/{trip_id}/holds/{hold_id}",
}


class DemoError(RuntimeError):
    pass


@dataclass
class Recorded:
    label: str
    operation: str
    status: int | None  # None for a request body
    body: dict
    wire: bool  # reference it from spec/openapi.yaml


@dataclass
class Demo:
    """A broker session: API calls to providers, /_sim controls, and a step log."""

    base_url: str
    wait_minutes: Callable[[float], None]
    now: Callable[[], datetime]
    align: Callable[[str], None] = lambda hhmm: None  # fake clock: jump to HH:MM on the trip day
    verbose: bool = True
    callback_url: str | None = None
    seconds_per_minute: float = 60.0  # deadlines (respond_by) compress with the demo's pacing
    steps: list[Recorded] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    trips: set[tuple[str, str]] = field(default_factory=set)
    tag: str = ""  # appended to every id so live runs can repeat against the same mock

    def code(self, base: str) -> str:
        return f"{base}{self.tag}"

    def __post_init__(self):
        self.client = httpx.Client(base_url=self.base_url, timeout=10)

    def after(self, minutes: float) -> datetime:
        return self.now() + timedelta(seconds=minutes * self.seconds_per_minute)

    def say(self, text: str) -> None:
        self.notes.append(text)
        if self.verbose:
            print(f"  · {text}")

    def api(
        self,
        label: str,
        provider: str,
        method: str,
        route: str,
        *,
        expect: tuple[int, ...],
        body=None,
        wire: bool = False,
        **params,
    ) -> dict:
        headers = {"Authorization": f"Bearer {REQUESTER}"}
        if method in ("POST", "PATCH"):
            headers["Idempotency-Key"] = f"demo-{uuid.uuid4()}"
        resp = self.client.request(method, f"/p/{provider}{route.format(**params)}", headers=headers, json=body)
        data = resp.json()
        if resp.status_code not in expect:
            raise DemoError(f"{label}: expected {expect}, got {resp.status_code}: {data}")
        operation = f"{method} {route}"
        if body is not None:
            self.steps.append(Recorded(label, operation, None, body, wire))
        self.steps.append(Recorded(label, operation, resp.status_code, data, wire))
        return data

    def sim(self, provider: str, action: str, trip_id: str | None = None, body=None) -> dict:
        path = f"/_sim/{provider}/trips/{trip_id}/{action}" if trip_id else f"/_sim/{provider}/{action}"
        resp = self.client.post(path, json=body)
        if resp.status_code != 200:
            raise DemoError(f"sim {path}: {resp.status_code} {resp.text}")
        data = resp.json()
        if self.verbose:
            for event in data.get("emitted", []):
                print(f"      ← {provider}: {_describe(event)}")
        return data

    def advance(self, provider: str, trip_id: str, until: str | None = None) -> None:
        self.sim(provider, "advance", trip_id, {"until": until} if until else None)

    def play(self, timeline: list[tuple[str, object]]) -> None:
        """Run (HH:MM, action) steps; an action is (provider, trip_id) for "next milestone" or a callable.

        Waits between steps follow the timestamps, so milestones and heartbeats land at
        realistic times on the fake clock (and at demo pace live).
        """
        previous = None
        for hhmm, action in timeline:
            if previous is None:
                self.align(hhmm)
            else:
                self.wait_minutes(_minutes(previous, hhmm))
            previous = hhmm
            if callable(action):
                action()
            else:
                self.advance(*action)

    def events(self) -> list[dict]:
        events = []
        for provider in sorted({p for p, _ in self.trips}):
            resp = self.client.get(f"/_sim/{provider}/events")
            ours = {t for p, t in self.trips if p == provider}
            events += [e for e in resp.json()["events"] if e.get("trip_id") in ours]
        return sorted(events, key=lambda e: (datetime.fromisoformat(e["occurred_at"]), e["provider_id"], e["event_id"]))

    # --- booking helpers ----------------------------------------------------------

    def subscribe(self, provider: str, wire: bool = False) -> None:
        if self.callback_url is None:  # live run without a receiver: nothing to deliver to
            self.say(f"(no --callback-url: not subscribing at {provider}; events are in /_sim/{provider}/events)")
            return
        url = self.callback_url
        types = [
            "TRIP_STATUS_CHANGED",
            "TRIP_SCHEDULED",
            "TRIP_CANCELLED",
            "TENDER_RESPONSE",
            "HOLD_RESPONSE",
            "MILESTONE",
            "ETA_UPDATE",
            "CUSTODY_HANDOFF",
        ]
        self.api(
            f"subscribe-{provider}",
            provider,
            "POST",
            "/subscriptions",
            expect=(201,),
            body={"callback_url": url, "event_types": types},
            wire=wire,
        )

    def tender(self, label: str, trip: dict, interchange: dict, respond_in_min: float, expect=(201,), wire=False):
        self.trips.add((trip["provider_id"], trip["trip_id"]))
        body = {
            "tender_id": f"tn-{trip['trip_id'].removeprefix('tr-')}",
            "trip": trip,
            "interchange": interchange,
            "respond_by": _iso(self.after(respond_in_min)),
        }
        tender = self.api(label, trip["provider_id"], "POST", "/tenders", body=body, expect=expect, wire=wire)
        self.say(f"Tender to {trip['provider_id']} for {trip['trip_id']}: {tender['status']}")
        return tender


def _trip(code: str, leg: str, provider: str, pickup: dict, dropoff: dict, start: datetime, reqs=()) -> dict:
    window = {"earliest": _iso(start), "latest": _iso(start + timedelta(minutes=15))}
    trip_leg = {"leg_id": "leg-1", "pickup": pickup, "dropoff": dropoff, "pickup_window": window}
    if reqs:
        trip_leg["service_requirements"] = list(reqs)
    return {
        "trip_id": f"tr-{code}-{leg}",
        "requester_id": REQUESTER,
        "provider_id": provider,
        # Pairwise rider pseudonym: each provider sees a different, stable id (SPEC.md §6.5).
        "rider_ref": {"rider_ref_id": f"r-{provider[:4]}-55e1", "display_name": "Dana K.", "language": "en"},
        "legs": [trip_leg],
    }


def _interchange(code: str, feeder: dict, onward: dict, supervised: bool, heartbeat_s: int) -> dict:
    return {
        "interchange_id": f"ix-{code}",
        "from": {"provider_id": feeder["provider_id"], "trip_id": feeder["trip_id"], "leg_id": "leg-1"},
        "to": {"provider_id": onward["provider_id"], "trip_id": onward["trip_id"], "leg_id": "leg-1"},
        "transfer_location": HUB,
        "min_connection_s": 300,
        "max_hold_s": 900,
        "auto_hold_s": 300,
        "accessibility_buffer_s": 300,
        "protection": "PROTECTED",
        "supervision_required": supervised,
        "heartbeat_s": heartbeat_s,
    }


def _book(d: Demo, code: str, day: date, supervised: bool = False, wire: bool = False):
    reqs = ("WHEELCHAIR_LIFT", "HAND_TO_HAND") if supervised else ("WHEELCHAIR_LIFT",)
    feeder = _trip(code, "a", A, HOME, HUB, datetime(day.year, day.month, day.day, 8, 50, tzinfo=EASTERN), reqs)
    onward = _trip(code, "b", B, HUB, CLINIC, datetime(day.year, day.month, day.day, 9, 45, tzinfo=EASTERN), reqs)
    ix = _interchange(code, feeder, onward, supervised, heartbeat_s=300)
    d.say(
        f"Interchange {ix['interchange_id']}: {A} → {B} at {HUB['name']}, protected"
        + (", supervised rider" if supervised else "")
    )
    d.tender("tender-feeder", feeder, ix, respond_in_min=15, wire=wire)
    d.tender("tender-onward", onward, ix, respond_in_min=15, wire=wire)
    d.wait_minutes(1)
    return feeder["trip_id"], onward["trip_id"], ix


# --- scenarios ------------------------------------------------------------------------


def _ride(feeder: str, onward: str, supervised: bool) -> list[tuple[str, object]]:
    """A connection that runs to plan: feeder 08:40–09:38, onward waits at the hub from 09:33."""
    a, b = (A, feeder), (B, onward)
    timeline = [
        ("08:40", a),
        ("08:50", a),
        ("08:53", a),
        ("08:54", a),  # en route, arrived, onboard, to hub
        ("09:20", b),
        ("09:33", b),
        ("09:35", a),
    ]  # onward heads to hub and waits; feeder arrives
    if supervised:
        timeline += [("09:37", a)]  # RIDER_HANDED_OFF, with the custody record
    timeline += [("09:38", a), ("09:38", b), ("09:39", b), ("10:12", b)]
    if supervised:
        timeline += [("10:14", b)]
    return timeline + [("10:15", b)]


def two_provider_transfer(d: Demo, day: date) -> None:
    """Two providers, one protected connection at a hub: both legs tendered and accepted,
    the feeder sends ETA heartbeats while inbound, the onward vehicle waits at the hub,
    and both legs complete."""
    d.subscribe(A, wire=True)
    d.subscribe(B)
    a, b, _ = _book(d, d.code("tpt"), day, wire=True)
    d.play(_ride(a, b, supervised=False))
    d.say("Rider delivered; both legs COMPLETED")


def late_first_leg_hold(d: Demo, day: date) -> None:
    """The feeder runs 15 minutes late in traffic. The broker sees the ETA band slip past
    the connection and asks the onward provider to hold; the hold is granted and the
    onward pickup moves."""
    a, b, ix = _book(d, d.code("hold"), day)
    hold_id = f"hd-{d.code('hold')}-1"
    eta: dict = {}

    def delay() -> None:
        eta.update(d.sim(A, "delay", a, {"seconds": 900, "cause": "TRAFFIC"})["emitted"][0]["payload"]["eta"])

    def hold() -> None:
        connection = datetime.fromisoformat(eta["likely"]) + timedelta(seconds=ix["min_connection_s"])
        d.say(f"Feeder now likely at hub {eta['likely'][11:16]}; connection needs pickup ≥ {connection:%H:%M}")
        body = {
            "hold_id": hold_id,
            "interchange_id": ix["interchange_id"],
            "action": "HOLD",
            "requested_until": _iso(connection),
            "respond_by": _iso(d.after(5)),
            "inbound_eta": dict(eta),
        }
        result = d.api(
            "hold-onward", B, "POST", "/trips/{trip_id}/hold", trip_id=b, body=body, expect=(201, 202), wire=True
        )
        d.say(f"Hold {result['status']}: onward pickup now {result.get('scheduled_pickup', '—')[11:16]}")
        d.api(
            "read-hold",
            B,
            "GET",
            "/trips/{trip_id}/holds/{hold_id}",
            trip_id=b,
            hold_id=hold_id,
            expect=(200,),
            wire=True,
        )

    d.play(
        [
            ("08:40", (A, a)),
            ("08:50", (A, a)),
            ("08:53", (A, a)),
            ("08:54", (A, a)),
            ("09:05", delay),
            ("09:06", hold),
            ("09:30", (B, b)),
            ("09:46", (B, b)),  # onward heads to hub, waits for the late feeder
            ("09:50", (A, a)),
            ("09:52", (A, a)),  # feeder arrives 15 min late, completes
            ("09:53", (B, b)),
            ("09:54", (B, b)),
            ("10:27", (B, b)),
            ("10:29", (B, b)),
        ]
    )
    d.say("Connection made despite the late feeder")


def tender_expired_retender(d: Demo, day: date) -> None:
    """The onward provider never answers its tender. At respond_by it expires; a coordinator
    approves the published fallback (approval: REQUIRED) and the broker re-tenders the leg
    to the next provider, which accepts."""
    d.sim(B, "policy", body={"tender_decision": "IGNORE"})
    try:
        reqs = ("WHEELCHAIR_LIFT",)
        start = datetime(day.year, day.month, day.day, 8, 50, tzinfo=EASTERN)
        code = d.code("rt")
        feeder = _trip(code, "a", A, HOME, HUB, start, reqs)
        onward = _trip(code, "b", B, HUB, CLINIC, start + timedelta(minutes=55), reqs)
        ix = _interchange(code, feeder, onward, supervised=False, heartbeat_s=300)
        d.tender("tender-feeder", feeder, ix, respond_in_min=15)
        d.tender("tender-onward", onward, ix, respond_in_min=5, expect=(202,), wire=True)
        d.wait_minutes(6)
        tender = d.api(
            "read-expired-tender", B, "GET", "/tenders/{tender_id}", tender_id=f"tn-{code}-b", expect=(200,), wire=True
        )
        d.say(f"Tender to {B} is {tender['status']}; coordinator approves fallback to {C}")
        fallback = _trip(code, "b2", C, HUB, CLINIC, start + timedelta(minutes=55), reqs)
        ix2 = _interchange(f"{code}-2", feeder, fallback, supervised=False, heartbeat_s=300)
        d.tender("retender-onward", fallback, ix2, respond_in_min=15, wire=True)
    finally:
        d.sim(B, "policy", body={"tender_decision": "ACCEPT"})


def missed_connection_reprotect(d: Demo, day: date) -> None:
    """The feeder breaks down and runs 40 minutes late. The onward provider can't wait that
    long and counter-offers a later pickup (PARTIAL); the broker asks it to retime further and
    gets no answer by respond_by (EXPIRED, so the broker raises HOLD_UNANSWERED). A coordinator
    approves re-protecting (fallback approval: REQUIRED): the broker cancels the onward trip with
    MISSED_CONNECTION and tenders the leg to another provider, which carries the rider."""
    a, b, ix = _book(d, d.code("rp"), day)
    code = d.code("rp")
    eta: dict = {}

    def connection() -> datetime:
        return datetime.fromisoformat(eta["likely"]) + timedelta(seconds=ix["min_connection_s"])

    def delay() -> None:
        eta.update(d.sim(A, "delay", a, {"seconds": 2400, "cause": "VEHICLE"})["emitted"][0]["payload"]["eta"])

    def hold() -> None:
        d.say(f"Feeder now likely at hub {eta['likely'][11:16]}; connection needs pickup ≥ {connection():%H:%M}")
        d.sim(B, "policy", body={"hold_decision": "PARTIAL"})
        body = {
            "hold_id": f"hd-{code}-1",
            "interchange_id": ix["interchange_id"],
            "action": "HOLD",
            "requested_until": _iso(connection()),
            "respond_by": _iso(d.after(5)),
            "inbound_eta": dict(eta),
        }
        result = d.api(
            "hold-onward", B, "POST", "/trips/{trip_id}/hold", trip_id=b, body=body, expect=(201,), wire=True
        )
        d.say(
            f"Hold {result['status']} ({result.get('reason')}): {B} offers pickup {result['scheduled_pickup'][11:16]}"
        )

    def retime() -> None:
        d.sim(B, "policy", body={"hold_decision": "IGNORE"})
        body = {
            "hold_id": f"hd-{code}-2",
            "interchange_id": ix["interchange_id"],
            "action": "RETIME",
            "requested_until": _iso(connection()),
            "respond_by": _iso(d.after(5)),
        }
        result = d.api(
            "retime-onward", B, "POST", "/trips/{trip_id}/hold", trip_id=b, body=body, expect=(202,), wire=True
        )
        d.say(f"Asked {B} to retime to {connection():%H:%M}: {result['status']}")

    def reprotect() -> None:
        hold = d.api(
            "read-unanswered-hold",
            B,
            "GET",
            "/trips/{trip_id}/holds/{hold_id}",
            trip_id=b,
            hold_id=f"hd-{code}-2",
            expect=(200,),
            wire=True,
        )
        d.say(f"Retime is {hold['status']}: broker raises HOLD_UNANSWERED; coordinator approves re-protect to {C}")
        d.api(
            "cancel-onward",
            B,
            "POST",
            "/trips/{trip_id}/cancel",
            trip_id=b,
            body={"reason": "MISSED_CONNECTION"},
            expect=(200,),
            wire=True,
        )
        start = datetime.fromisoformat(eta["likely"]).replace(second=0) + timedelta(minutes=5)
        fallback = _trip(code, "b2", C, HUB, CLINIC, start, ("WHEELCHAIR_LIFT",))
        ix2 = _interchange(f"{code}-2", {"provider_id": A, "trip_id": a}, fallback, supervised=False, heartbeat_s=300)
        d.tender("reprotect-onward", fallback, ix2, respond_in_min=10)
        c.append(fallback["trip_id"])

    c: list[str] = []
    try:
        d.play(
            [
                ("08:40", (A, a)),
                ("08:50", (A, a)),
                ("08:53", (A, a)),
                ("08:54", (A, a)),
                ("09:00", delay),
                ("09:01", hold),
                ("09:03", retime),
                ("09:10", reprotect),
                ("09:55", lambda: d.advance(C, c[0])),  # en route to the hub
                ("10:13", lambda: d.advance(A, a)),  # feeder arrives 40 min late
                ("10:14", lambda: d.advance(A, a)),
                ("10:15", lambda: d.advance(C, c[0])),
                ("10:17", lambda: d.advance(C, c[0])),
                ("10:18", lambda: d.advance(C, c[0])),
                ("10:50", lambda: d.advance(C, c[0])),
                ("10:51", lambda: d.advance(C, c[0])),
            ]
        )
    finally:
        d.sim(B, "policy", body={"hold_decision": "AUTO"})
    d.say(f"Rider reached the clinic on {C}; {B}'s trip CANCELLED with MISSED_CONNECTION")


def supervised_rider_custody_handoff(d: Demo, day: date) -> None:
    """A rider who must not be left alone (HAND_TO_HAND, supervision_required). Custody passes
    caregiver → feeder driver → onward driver → clinic staff, with a record at each handoff."""
    a, b, _ = _book(d, d.code("sup"), day, supervised=True)
    d.play(_ride(a, b, supervised=True))
    d.say("Custody recorded at home, at the hub (by both drivers) and at the clinic; both legs COMPLETED")


SCENARIOS = {
    "two-provider-transfer": two_provider_transfer,
    "late-first-leg-hold": late_first_leg_hold,
    "tender-expired-retender": tender_expired_retender,
    "missed-connection-reprotect": missed_connection_reprotect,
    "supervised-rider-custody-handoff": supervised_rider_custody_handoff,
}


# --- example generation -------------------------------------------------------------------


def generate_examples(out: Path) -> list[Path]:
    from tds_conform.mock.network import Network
    from tds_conform.mock.provider import Provider, ProviderConfig
    from tds_conform.mock.server import make_server

    written = []
    day = date(2026, 11, 10)
    for name, scenario in SCENARIOS.items():
        clock = [datetime(day.year, day.month, day.day, 7, 55, tzinfo=EASTERN)]
        counter = itertools.count(1)

        def new_id(prefix: str, counter=counter) -> str:
            return f"{prefix}-{next(counter):04d}"

        providers = [
            Provider(
                ProviderConfig(provider_id=pid),
                clock=lambda c=clock: c[0],
                new_id=new_id,
                new_secret=lambda: "whsec_example_not_a_real_secret_0000",
            )
            for pid in (A, B, C)
        ]
        network = Network.of(*providers)  # not started: no ticker thread, no webhook delivery
        server = make_server(network, port=0, quiet=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def wait(minutes: float, clock=clock, providers=providers) -> None:
            end = clock[0] + timedelta(minutes=minutes)
            while clock[0] < end:  # minute steps, like the live ticker, so heartbeats land on time
                clock[0] = min(end, clock[0] + timedelta(minutes=1))
                for p in providers:
                    p.tick()

        def align(hhmm: str, clock=clock, wait=wait) -> None:
            hour, minute = map(int, hhmm.split(":"))
            target = clock[0].replace(hour=hour, minute=minute, second=0)
            wait(max(0.0, (target - clock[0]).total_seconds() / 60))

        demo = Demo(
            f"http://127.0.0.1:{server.server_address[1]}",
            wait,
            lambda c=clock: c[0],
            align=align,
            verbose=False,
            callback_url="https://broker.example.org/tds/events",
        )
        scenario(demo, day)
        written.append(_write_scenario(out / name, scenario, demo))
        server.shutdown()
    return written


def _write_scenario(folder: Path, scenario, demo: Demo) -> Path:
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    files = {}
    for n, step in enumerate(demo.steps, 1):
        kind = "request" if step.status is None else "response"
        filename = f"{n:02d}-{step.label}.{kind}.json"
        (folder / filename).write_text(json.dumps(step.body, indent=2, ensure_ascii=False) + "\n")
        ref = (
            REQUEST_SCHEMAS[step.operation]
            if step.status is None
            else (RESPONSE_SCHEMAS.get((step.operation, step.status)) or "core/error")
        )
        entry = {"schema": schema_id(*reversed(ref.split("/")))}
        if step.wire:
            entry["operation"] = step.operation
            if step.status is not None:
                entry["status"] = step.status
        files[filename] = entry
    (folder / "events.json").write_text(json.dumps(demo.events(), indent=2, ensure_ascii=False) + "\n")
    files["events.json"] = {"kind": "event-stream"}
    manifest = {
        "description": " ".join(scenario.__doc__.split()),
        "generated_by": "tds-demo generate-examples (do not edit by hand; change tds_conform/demo.py and regenerate)",
        "narrative": demo.notes,
        "files": files,
    }
    (folder / "manifest.yaml").write_text(_yaml(manifest))
    return folder


def _yaml(data: dict) -> str:
    import yaml

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=100)


def _minutes(start: str, end: str) -> float:
    (h1, m1), (h2, m2) = (map(int, t.split(":")) for t in (start, end))
    return (h2 * 60 + m2) - (h1 * 60 + m1)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _describe(event: dict) -> str:
    p = event["payload"]
    detail = {
        "MILESTONE": lambda: p["milestone"],
        "ETA_UPDATE": lambda: (
            f"{p['eta']['target']} likely {p['eta']['likely'][11:16]} "
            f"(latest {p['eta']['latest'][11:16]}, {p['eta']['source']})"
        ),
        "CUSTODY_HANDOFF": lambda: f"{p['handoff']['handoff_from']['role']} → {p['handoff']['handoff_to']['role']}",
        "TRIP_STATUS_CHANGED": lambda: f"{p.get('previous_status')} → {p['status']}",
        "TRIP_SCHEDULED": lambda: f"pickup {p['scheduled_pickup'][11:16]}",
        "HOLD_RESPONSE": lambda: p["hold"]["status"],
        "TENDER_RESPONSE": lambda: p["tender"]["status"],
    }.get(event["event_type"], lambda: "")()
    return f"{event['event_type']} {event.get('trip_id', '')} {detail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tds-demo", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="narrate scenarios against a running `tds-mock --demo`")
    run.add_argument("--url", default="http://localhost:4010")
    run.add_argument("--scenario", choices=list(SCENARIOS), action="append")
    run.add_argument("--seconds-per-minute", type=float, default=1.0, help="live pacing (default 1)")
    run.add_argument("--callback-url", help="subscribe this webhook receiver (e.g. ThroughRide's)")
    gen = sub.add_parser("generate-examples", help="write examples/<scenario>/ from in-process runs")
    gen.add_argument("--out", type=Path, default=data_root() / "examples")
    args = parser.parse_args(argv)

    if args.command == "generate-examples":
        for folder in generate_examples(args.out):
            print(f"wrote {folder}")
        return 0

    tomorrow = (datetime.now(EASTERN) + timedelta(days=1)).date()
    for name in args.scenario or list(SCENARIOS):
        print(f"\n== {name} ==\n{' '.join(SCENARIOS[name].__doc__.split())}\n")
        demo = Demo(
            args.url,
            lambda m: time.sleep(m * args.seconds_per_minute),
            lambda: datetime.now(EASTERN),
            callback_url=args.callback_url,
            seconds_per_minute=args.seconds_per_minute,
            tag=f"-{uuid.uuid4().hex[:4]}",
        )
        try:
            SCENARIOS[name](demo, tomorrow)
        except (DemoError, httpx.HTTPError) as exc:
            print(f"  ✗ {exc}")
            return 1
    return 0
