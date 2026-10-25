"""A requester-side relay mailbox (SPEC.md §9.4) for one provider, as `tds-mock` serves it.

tds-mock plays the requester here: request files are queued with `/_sim/mailbox/<id>/outbox`
(or `queue()`), the agent under test lists, downloads, acknowledges and uploads, and what it
uploaded is read back with `GET /_sim/mailbox/<id>`. Methods return (status, body); a
successful download returns the file's bytes as the body.
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from tds_conform.data import data_root
from tds_conform.mailbox import inbox_name_problem, is_outbox_name, sha256_hex
from tds_conform.mock.common import _ts, backpressure, problem

DEFAULT_REQUESTS = "examples/relay-mailbox/tds_requests_20261104T150000Z.csv"


@dataclass
class StoredFile:
    name: str
    content: bytes
    created_at: datetime
    acked_at: datetime | None = None

    @property
    def sha256(self) -> str:
        return sha256_hex(self.content)

    def entry(self) -> dict:
        return {
            "name": self.name,
            "size": len(self.content),
            "sha256": self.sha256,
            "created_at": _utc(self.created_at),
        }


def _utc(moment: datetime) -> str:
    return _ts(moment.astimezone(UTC)).replace("+00:00", "Z")


class Mailbox:
    def __init__(
        self,
        provider_id: str,
        *,
        poll_interval_s: int = 300,
        max_file_bytes: int = 10 * 1024 * 1024,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ):
        self.provider_id, self.poll_interval_s, self.max_file_bytes = provider_id, poll_interval_s, max_file_bytes
        self.clock = clock
        self.outbox: dict[str, StoredFile] = {}
        self.inbox: dict[str, StoredFile] = {}
        self.last_poll_at: datetime | None = None
        self.agent = ""
        self.unavailable_until: datetime | None = None
        self._lock = threading.Lock()

    # --- the agent's calls ------------------------------------------------------------

    def admit(self, agent: str = "") -> tuple[int, dict] | None:
        """Every authorised call is a poll; a 503 while the mailbox is down (`/_sim/.../unavailable`)."""
        now = self.clock()
        self.last_poll_at = now
        self.agent = agent[:80] or self.agent  # the last agent that named itself
        if self.unavailable_until and now < self.unavailable_until:
            wait = max(1, int((self.unavailable_until - now).total_seconds() + 0.999))
            return backpressure(503, "TEMPORARILY_UNAVAILABLE", "The mailbox is down for maintenance.", wait)
        return None

    def listing(self) -> tuple[int, dict]:
        with self._lock:
            waiting = sorted(
                (f for f in self.outbox.values() if f.acked_at is None), key=lambda f: (f.created_at, f.name)
            )
            files = [f.entry() for f in waiting]
        body = {"provider_id": self.provider_id, "poll_interval_s": self.poll_interval_s}
        return 200, body | {"max_file_bytes": self.max_file_bytes, "files": files}

    def download(self, name: str) -> tuple[int, dict | bytes]:
        found = self.outbox.get(name)
        if found is None:
            return problem(404, "NOT_FOUND", f"No file {name} in this mailbox.")
        return 200, found.content

    def ack(self, name: str) -> tuple[int, dict]:
        with self._lock:
            found = self.outbox.get(name)
            if found is None:
                return problem(404, "NOT_FOUND", f"No file {name} in this mailbox.")
            found.acked_at = found.acked_at or self.clock()
            return 200, {"name": name, "acked_at": _utc(found.acked_at)}

    def put(self, name: str, data: bytes) -> tuple[int, dict]:
        if why := inbox_name_problem(name):
            return problem(400, "INVALID_FILE_NAME", f"{name}: {why}.")
        if not data:
            return problem(400, "EMPTY_FILE", "The upload has no bytes; a file needs at least its header row.")
        if len(data) > self.max_file_bytes:
            return self.too_large()
        with self._lock:
            stored = self.inbox.get(name)
            if stored is not None and stored.content != data:
                return problem(
                    409, "FILE_NAME_TAKEN", f"{name} is already stored with other content; upload it under a new name."
                )
            created = stored is None
            if created:
                stored = self.inbox[name] = StoredFile(name, data, self.clock())
        receipt = {"name": name, "stored": created, "sha256": stored.sha256, "size": len(data)}
        return (201 if created else 200), receipt | {"received_at": _utc(stored.created_at)}

    def too_large(self) -> tuple[int, dict]:
        return problem(
            413, "FILE_TOO_LARGE", f"Uploads are limited to {self.max_file_bytes} bytes; split the rows over files."
        )

    # --- simulation ---------------------------------------------------------------------

    def queue(self, content: bytes | None = None, name: str | None = None) -> tuple[int, dict]:
        """Put a request file in the outbox, as the requester would; default: the example file."""
        if content is None:
            content = (data_root() / DEFAULT_REQUESTS).read_bytes()
        with self._lock:
            now = self.clock()
            if name is None:
                stamp, n = now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ"), 1
                name = f"tds_requests_{stamp}.csv"
                while name in self.outbox:
                    n += 1
                    name = f"tds_requests_{stamp}_{n}.csv"
            if not is_outbox_name(name):
                return problem(400, "INVALID_FILE_NAME", f"{name} is not a requests file name per SPEC.md §9.2.")
            if name in self.outbox:
                return problem(409, "FILE_NAME_TAKEN", f"{name} is already in the outbox.")
            self.outbox[name] = StoredFile(name, content, now)
            return 201, self.outbox[name].entry()

    def state(self) -> tuple[int, dict]:
        """What the agent did: last poll, acknowledgements, and uploaded files with their text."""
        outbox = [f.entry() | {"acked_at": f.acked_at and _utc(f.acked_at)} for f in self.outbox.values()]
        inbox = [
            f.entry() | {"received_at": _utc(f.created_at), "content": f.content.decode("utf-8", "replace")}
            for f in self.inbox.values()
        ]
        for entry in inbox:
            entry.pop("created_at")
        return 200, {
            "provider_id": self.provider_id,
            "last_poll_at": self.last_poll_at and _utc(self.last_poll_at),
            "agent": self.agent,
            "outbox": outbox,
            "inbox": inbox,
        }

    def sim_unavailable(self, body: dict | None) -> tuple[int, dict]:
        seconds = (body or {}).get("for_s", 30)
        if not isinstance(seconds, int | float) or seconds < 0:
            return problem(400, "INVALID_REQUEST", "for_s must be a number of seconds >= 0.")
        self.unavailable_until = self.clock() + timedelta(seconds=seconds)
        return 200, {"unavailable_until": _utc(self.unavailable_until)}
