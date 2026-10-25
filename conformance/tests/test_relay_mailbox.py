"""The relay mailbox for CSV profile files (SPEC.md §9.4): tds-mock's mailbox, its HTTP routes, the kit."""

import json
import threading
from datetime import timedelta

import httpx
import pytest

import tds_conform.scenarios  # noqa: F401 - registers the scenarios
from tds_conform.cli import conform_main
from tds_conform.csvfile import file_problems
from tds_conform.data import data_root, example, schema_errors
from tds_conform.mailbox import inbox_name_problem, sha256_hex
from tds_conform.mock.mailbox import Mailbox, _utc
from tds_conform.mock.network import Dispatcher, Network
from tds_conform.mock.provider import Provider, ProviderConfig
from tds_conform.mock.server import make_server
from tds_conform.runner import Settings, run_scenario

PID = "county-dial-a-ride"
AUTH = {"Authorization": f"Bearer {PID}"}
ANSWERS = "tds_answers_20261104T204000Z.csv"


def answers_bytes() -> bytes:
    return (data_root() / "examples" / "relay-mailbox" / ANSWERS).read_bytes()


@pytest.fixture
def mailbox(clock):
    return Mailbox(PID, clock=clock)


@pytest.fixture
def serve_mailbox():
    """Serve one mailbox (or a faulty subclass); yields a function returning (base URL, client)."""
    servers = []

    def start(box: Mailbox):
        network = Network.of(Provider(ProviderConfig(provider_id=PID)), dispatcher=Dispatcher(), mailboxes=[box])
        server = make_server(network, port=0, quiet=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        url = f"http://127.0.0.1:{server.server_address[1]}"
        return url, httpx.Client(base_url=url, timeout=5)

    yield start
    for server in servers:
        server.shutdown()


def run(url: str, scenario: str, take_files: bool = True):
    settings = Settings(url=f"{url}/mailbox", token=PID, requester_id="", provider_id="", trip_template={})
    settings.take_files = take_files
    with httpx.Client(base_url=settings.url, timeout=5) as client:
        return run_scenario(scenario, settings, client, 0)


def failed_checks(result) -> set[str]:
    return {c.name for s in result.steps for c in s.checks if not c.passed and not c.advisory}


# --- the reference mailbox ------------------------------------------------------------------


def test_listing_shows_unacked_files_oldest_first(mailbox, clock):
    first = mailbox.queue()[1]["name"]
    clock.advance(1)
    second = mailbox.queue(b"requester_id\r\n")[1]["name"]
    status, listing = mailbox.listing()
    assert status == 200 and schema_errors(listing, "csv/mailbox-listing") == []
    assert [f["name"] for f in listing["files"]] == [first, second]
    assert listing["provider_id"] == PID and listing["poll_interval_s"] == 300
    mailbox.ack(first)
    assert [f["name"] for f in mailbox.listing()[1]["files"]] == [second]


def test_queue_names_files_per_csv_profile(mailbox):
    names = [mailbox.queue()[1]["name"] for _ in range(3)]
    stamp = names[0].removeprefix("tds_requests_").removesuffix(".csv")
    assert names == [f"tds_requests_{stamp}.csv", f"tds_requests_{stamp}_2.csv", f"tds_requests_{stamp}_3.csv"]
    assert mailbox.queue(name="notes.txt")[0] == 400
    assert mailbox.queue(name=names[0])[0] == 409


def test_default_queued_file_is_the_example_requests_file(mailbox):
    entry = mailbox.queue()[1]
    status, data = mailbox.download(entry["name"])
    assert status == 200 and file_problems(data, "requests") == []
    assert entry["sha256"] == sha256_hex(data) and entry["size"] == len(data)


def test_ack_is_idempotent_and_keeps_the_first_time(mailbox, clock):
    name = mailbox.queue()[1]["name"]
    status, first = mailbox.ack(name)
    assert status == 200 and schema_errors(first, "csv/mailbox-ack") == []
    clock.advance(60)
    assert mailbox.ack(name) == (200, first)
    assert mailbox.download(name)[0] == 200  # still downloadable after the ack
    assert mailbox.ack("tds_requests_19990101T000000Z.csv")[1]["code"] == "NOT_FOUND"


def test_put_is_write_once_by_name(mailbox, clock):
    data = answers_bytes()
    status, receipt = mailbox.put(ANSWERS, data)
    assert status == 201 and schema_errors(receipt, "csv/mailbox-receipt") == []
    assert receipt == example("relay-mailbox", "05-put.response.json") | {"received_at": receipt["received_at"]}
    clock.advance(60)
    status, again = mailbox.put(ANSWERS, data)
    assert status == 200 and again == receipt | {"stored": False}
    status, body = mailbox.put(ANSWERS, data + b"\r\n")
    assert (status, body["code"]) == (409, "FILE_NAME_TAKEN")
    assert mailbox.inbox[ANSWERS].content == data


@pytest.mark.parametrize(
    ("name", "data", "status", "code"),
    [
        ("notes.txt", b"x", 400, "INVALID_FILE_NAME"),
        ("tds_requests_20261104T204000Z.csv", b"x", 400, "INVALID_FILE_NAME"),
        ("tds_answers_batch42.csv", b"x", 400, "INVALID_FILE_NAME"),
        ("..", b"x", 400, "INVALID_FILE_NAME"),
        (ANSWERS, b"", 400, "EMPTY_FILE"),
    ],
)
def test_put_refusals(mailbox, name, data, status, code):
    got_status, body = mailbox.put(name, data)
    assert (got_status, body["code"]) == (status, code)
    assert schema_errors(body, "error") == []
    assert not mailbox.inbox


def test_put_refuses_a_file_over_the_limit(clock):
    box = Mailbox(PID, clock=clock, max_file_bytes=1_000_000)
    assert box.put(ANSWERS, b"a" * 1_000_001)[1]["code"] == "FILE_TOO_LARGE"
    assert box.put(ANSWERS, b"a" * 1_000_000)[0] == 201


def test_inbox_name_rules():
    assert inbox_name_problem(ANSWERS) is None
    assert inbox_name_problem("tds_statuses_20261105T150000Z_1a2b3c4d.csv") is None
    assert "letters, digits" in inbox_name_problem("../tds_answers_20261104T204000Z.csv")
    assert "requester to provider" in inbox_name_problem("tds_requests_20261104T204000Z.csv")


# --- over HTTP ------------------------------------------------------------------------------


def test_http_round_trip(serve_mailbox, clock):
    box = Mailbox(PID, clock=clock)
    url, client = serve_mailbox(box)
    name = client.post(f"/_sim/mailbox/{PID}/outbox", json={}).json()["name"]
    listing = client.get("/mailbox/outbox", headers=AUTH | {"X-Relay-Agent": "test-agent/1.0"}).json()
    resp = client.get(f"/mailbox/outbox/{name}", headers=AUTH)
    assert resp.status_code == 200 and resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["x-relay-sha256"] == sha256_hex(resp.content) == listing["files"][0]["sha256"]
    assert client.post(f"/mailbox/ack/{name}", headers=AUTH).status_code == 200
    put = client.put(f"/mailbox/inbox/{ANSWERS}", headers=AUTH | {"Content-Type": "text/csv"}, content=answers_bytes())
    assert put.status_code == 201
    state = client.get(f"/_sim/mailbox/{PID}").json()
    assert state["agent"] == "test-agent/1.0" and state["outbox"][0]["acked_at"]
    assert state["inbox"][0]["content"] == answers_bytes().decode()


def test_http_auth_routes_and_encoded_names(serve_mailbox, clock):
    url, client = serve_mailbox(Mailbox(PID, clock=clock))
    for headers in ({}, {"Authorization": "Bearer someone-else"}, {"Authorization": f"Token {PID}"}):
        resp = client.get("/mailbox/outbox", headers=headers)
        assert (resp.status_code, resp.json()["code"]) == (401, "UNAUTHORIZED")
        assert resp.headers["content-type"] == "application/problem+json"
    assert client.delete("/mailbox/outbox", headers=AUTH).status_code == 404
    assert client.get("/mailbox/inbox", headers=AUTH).status_code == 404
    resp = client.put("/mailbox/inbox/tds_answers_20261104T204000Z%2Ecsv", headers=AUTH, content=answers_bytes())
    assert resp.status_code == 201 and resp.json()["name"] == ANSWERS


def test_http_too_large_and_unavailable(serve_mailbox, clock):
    url, client = serve_mailbox(Mailbox(PID, clock=clock, max_file_bytes=1_000_000))
    resp = client.put(f"/mailbox/inbox/{ANSWERS}", headers=AUTH, content=b"a" * 1_000_001)
    assert (resp.status_code, resp.json()["code"]) == (413, "FILE_TOO_LARGE")
    client.post(f"/_sim/mailbox/{PID}/unavailable", json={"for_s": 30})
    resp = client.get("/mailbox/outbox", headers=AUTH)
    assert resp.status_code == 503 and resp.headers["retry-after"] == "30" and resp.json()["retry_after_s"] == 30
    clock.advance(31)
    assert client.get("/mailbox/outbox", headers=AUTH).status_code == 200


def test_every_provider_in_a_network_has_a_mailbox():
    network = Network([ProviderConfig(provider_id="a"), ProviderConfig(provider_id="b")])
    assert set(network.mailboxes) == {"a", "b"}


# --- the kit ----------------------------------------------------------------------------------


def test_reference_mailbox_passes_both_scenarios(serve_mailbox):
    box = Mailbox(PID)
    url, _ = serve_mailbox(box)
    box.queue()
    for scenario in ("relay_mailbox_upload", "relay_mailbox_download"):
        result = run(url, scenario)
        assert result.outcome == "PASS", (scenario, result.note, failed_checks(result))
    assert all(f.acked_at for f in box.outbox.values())
    assert len(box.inbox) == 1 and "_conform-" in next(iter(box.inbox))  # the header-only upload


def test_download_without_take_files_or_files_is_inconclusive(serve_mailbox):
    box = Mailbox(PID)
    url, _ = serve_mailbox(box)
    assert run(url, "relay_mailbox_download").outcome == "INCONCLUSIVE"
    box.queue()
    result = run(url, "relay_mailbox_download", take_files=False)
    assert result.outcome == "INCONCLUSIVE" and "--take-files" in result.note
    assert not any(f.acked_at for f in box.outbox.values())


def test_report_keeps_no_file_content(serve_mailbox):
    box = Mailbox(PID)
    url, _ = serve_mailbox(box)
    box.queue()
    result = run(url, "relay_mailbox_download")
    text = json.dumps([s.response for s in result.steps])
    assert "+15175550142" not in text and "requester_id,provider_id" not in text


class OverwritingMailbox(Mailbox):
    def put(self, name, data):
        self.inbox.pop(name, None)
        return super().put(name, data)


class LooseNamesMailbox(Mailbox):
    def put(self, name, data):
        if name == "notes.txt":
            return super().put("tds_answers_20261104T204000Z_notes.csv", data)
        return super().put(name, data)


class NoPollIntervalMailbox(Mailbox):
    def listing(self):
        status, body = super().listing()
        return status, {k: v for k, v in body.items() if k != "poll_interval_s"}


class WrongHashMailbox(Mailbox):
    def listing(self):
        status, body = super().listing()
        for f in body["files"]:
            f["sha256"] = "0" * 64
        return status, body


class AckOnDownloadMailbox(Mailbox):
    def download(self, name):
        self.ack(name)
        return super().download(name)


class RestampingAckMailbox(Mailbox):
    def ack(self, name):
        stored = self.outbox.get(name)
        if stored is not None and stored.acked_at is not None:
            stored.acked_at += timedelta(seconds=1)
            return 200, {"name": name, "acked_at": _utc(stored.acked_at)}
        return super().ack(name)


class ListsAckedMailbox(Mailbox):
    def listing(self):
        status, body = super().listing()
        body["files"] = [f.entry() for f in self.outbox.values()]
        return status, body


@pytest.mark.parametrize(
    ("faulty", "scenario", "check"),
    [
        (OverwritingMailbox, "relay_mailbox_upload", "status is 200"),
        (LooseNamesMailbox, "relay_mailbox_upload", "status is 400"),
        (NoPollIntervalMailbox, "relay_mailbox_upload", "body matches csv/mailbox-listing.yaml"),
        (WrongHashMailbox, "relay_mailbox_download", "the listed sha256 is the body's"),
        (AckOnDownloadMailbox, "relay_mailbox_download", "downloading does not acknowledge"),
        (RestampingAckMailbox, "relay_mailbox_download", "acked_at is the first acknowledgement's"),
        (ListsAckedMailbox, "relay_mailbox_download", "an acknowledged file is not listed"),
    ],
)
def test_faulty_mailboxes_fail(serve_mailbox, faulty, scenario, check):
    box = faulty(PID)
    url, _ = serve_mailbox(box)
    box.queue()
    result = run(url, scenario)
    assert result.outcome == "FAIL", result.note
    assert check in failed_checks(result)


def test_a_failed_check_beats_inconclusive(serve_mailbox):
    box = WrongHashMailbox(PID)
    url, _ = serve_mailbox(box)
    box.queue()
    assert run(url, "relay_mailbox_download", take_files=False).outcome == "FAIL"


def test_cli_mailbox_writes_a_report(serve_mailbox, tmp_path):
    box = Mailbox(PID)
    url, _ = serve_mailbox(box)
    box.queue()
    argv = ["mailbox", "--url", f"{url}/mailbox", "--token", PID, "--report-dir", str(tmp_path)]
    assert conform_main([*argv, "--take-files"]) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["conformant"] and report["profile"] == "relay-mailbox" and report["summary"]["PASS"] == 2
    assert "Conformant: Relay mailbox" in (tmp_path / "report.html").read_text()
    assert conform_main([*argv, "--token", "nobody"]) == 1
