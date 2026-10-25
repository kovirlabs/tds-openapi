"""Relay mailbox scenarios (SPEC.md §9.4), run against a requester's mailbox with `tds-conform mailbox`.

`relay_mailbox_upload` leaves one header-only answers file (no rows, so nothing to apply) in the
mailbox under a `conform-` name. `relay_mailbox_download` reads a waiting request file and, only
with `--take-files`, acknowledges it: use a test mailbox for that, since an acknowledged file
leaves the listing and the provider's real agent would not see it.
"""

import secrets
from datetime import UTC, datetime

from tds_conform.csvfile import file_name, file_problems, write
from tds_conform.mailbox import inbox_name_problem, is_outbox_name, sha256_hex
from tds_conform.runner import Abort, Context, Inconclusive, Step, scenario

CSV = {"Content-Type": "text/csv"}


def _code(step: Step, expected: str) -> bool:
    code = step.body.get("code") if isinstance(step.body, dict) else None
    return step.check_equal(code, expected, f"code is {expected}")


def _list(ctx: Context, label: str) -> Step:
    step = ctx.call(label, "GET", "/outbox", expect=(200,))
    step.require(step.status == 200 and isinstance(step.body, dict), "the mailbox token is accepted")
    return step


def _put(ctx: Context, label: str, name: str, data: bytes, expect: tuple[int, ...]) -> Step:
    return ctx.call(
        label, "PUT", "/inbox/{file_name}", expect=expect, key=None, content=data, headers=CSV, file_name=name
    )


def _stop_without_verdict(ctx: Context, reason: str) -> None:
    """INCONCLUSIVE, unless a check already failed: then the scenario fails."""
    if any(not c.passed and not c.advisory for s in ctx.result.steps for c in s.checks):
        raise Abort(reason)
    raise Inconclusive(reason)


@scenario("relay_mailbox_upload", "csv", "Relay mailbox: token, listing, write-once uploads and refusals")
def relay_mailbox_upload(ctx: Context) -> None:
    step = ctx.call("list without a token", "GET", "/outbox", expect=(401, 403), auth=False)
    if step.status in (401, 403):
        _code(step, "UNAUTHORIZED")

    listing = _list(ctx, "list the outbox")
    files = listing.body.get("files", [])
    listing.check(
        all(is_outbox_name(f.get("name", "")) for f in files),
        "listed files are requests files named per §9.2",
        str([f.get("name") for f in files]),
    )
    created = [f.get("created_at", "") for f in files]
    listing.check(created == sorted(created, key=_moment), "oldest first", str(created))

    run = f"conform-{secrets.token_hex(4)}"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    name = file_name("answers", stamp, run)
    data = write([], "answers")  # header only: a valid answers file with nothing to apply
    first = _put(ctx, "upload an answers file", name, data, (201,))
    if first.status == 201:
        first.check_equal(first.body.get("stored"), True, "stored is true")
        first.check_equal(first.body.get("name"), name, "receipt names the file")
        first.check_equal(first.body.get("sha256"), sha256_hex(data), "receipt sha256 matches the bytes")
        if "size" in first.body:
            first.check_equal(first.body["size"], len(data), "receipt size matches the bytes")

    again = _put(ctx, "upload the same bytes again (a retry)", name, data, (200,))
    if again.status == 200:
        again.check_equal(again.body.get("stored"), False, "stored is false: applied once")
        again.check_equal(again.body.get("sha256"), sha256_hex(data), "receipt sha256 matches the bytes")
        if first.status == 201 and "received_at" in first.body and "received_at" in again.body:
            again.check_equal(again.body["received_at"], first.body["received_at"], "received_at is the first upload's")

    other = data.replace(b"\r\n", b"\n")
    taken = _put(ctx, "other bytes under the same name", name, other, (409,))
    if taken.status == 409:
        _code(taken, "FILE_NAME_TAKEN")

    for label, bad in (
        ("a name that is not a CSV profile file", "notes.txt"),
        ("a requests file uploaded the wrong way", file_name("requests", stamp, run)),
    ):
        refused = _put(ctx, f"refuse {label}", bad, data, (400,))
        if refused.status == 400:
            _code(refused, "INVALID_FILE_NAME")
    empty = _put(ctx, "refuse an empty file", file_name("statuses", stamp, run), b"", (400,))
    if empty.status == 400:
        _code(empty, "EMPTY_FILE")

    loose = f"tds_answers_{run}.csv"  # no time: not a §9.2 name
    assert inbox_name_problem(loose)
    step = _put(ctx, "a name without the §9.2 time (SHOULD be refused)", loose, data, (400, 200, 201))
    step.advise(
        step.status == 400, "a name not per §9.2 is refused with INVALID_FILE_NAME (SHOULD)", f"got {step.status}"
    )

    unknown = file_name("requests", "19990101T000000Z", run)
    missing = ctx.call("download an unknown file", "GET", "/outbox/{file_name}", expect=(404,), file_name=unknown)
    if missing.status == 404:
        _code(missing, "NOT_FOUND")
    missing = ctx.call(
        "acknowledge an unknown file", "POST", "/ack/{file_name}", expect=(404,), key=None, file_name=unknown
    )
    if missing.status == 404:
        _code(missing, "NOT_FOUND")

    after = _list(ctx, "list again")
    after.check(
        name not in {f.get("name") for f in after.body.get("files", [])}, "uploads are not listed in the outbox"
    )


@scenario("relay_mailbox_download", "csv", "Relay mailbox: download, checksum and idempotent acknowledgement")
def relay_mailbox_download(ctx: Context) -> None:
    listing = _list(ctx, "list the outbox")
    files = listing.body.get("files") or []
    if not files:
        _stop_without_verdict(ctx, "no request file waiting in this mailbox; queue one for this provider and run again")
    entry = files[0]
    name = entry["name"]

    got = ctx.call("download the oldest file", "GET", "/outbox/{file_name}", expect=(200,), file_name=name)
    got.require(got.status == 200, "the listed file can be downloaded")
    raw = ctx.last.content
    got.check_equal(got.response["content_type"], "text/csv", "media type is text/csv")
    got.check_equal(got.response.get("x_relay_sha256"), sha256_hex(raw), "X-Relay-SHA256 is the body's SHA-256")
    got.check_equal(entry.get("sha256"), sha256_hex(raw), "the listed sha256 is the body's")
    got.check_equal(entry.get("size"), len(raw), "the listed size is the body's")
    problems = file_problems(raw, "requests", filename=name)
    got.check(not problems, "the file is a CSV profile requests file (§9.2)", "; ".join(problems[:5]))
    got.response["body"] = f"({len(raw)} bytes; rider data not copied into the report)"

    still = _list(ctx, "list again after downloading")
    still.check(name in {f.get("name") for f in still.body.get("files", [])}, "downloading does not acknowledge")

    if not ctx.settings.take_files:
        _stop_without_verdict(
            ctx, "download checked; acknowledgement not tested (run with --take-files on a test mailbox)"
        )

    ack = ctx.call("acknowledge it", "POST", "/ack/{file_name}", expect=(200,), key=None, file_name=name)
    ack.require(ack.status == 200, "the file can be acknowledged")
    ack.check_equal(ack.body.get("name"), name, "the answer names the file")
    again = ctx.call("acknowledge it again", "POST", "/ack/{file_name}", expect=(200,), key=None, file_name=name)
    if again.status == 200:
        again.check_equal(
            again.body.get("acked_at"), ack.body.get("acked_at"), "acked_at is the first acknowledgement's"
        )
    after = _list(ctx, "list after the acknowledgement")
    after.check(name not in {f.get("name") for f in after.body.get("files", [])}, "an acknowledged file is not listed")


def _moment(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
