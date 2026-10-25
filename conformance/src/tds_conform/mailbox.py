"""Relay mailbox for CSV profile files (SPEC.md §9.4): the rules shared by `tds-mock` and the kit.

A requester serves the mailbox; a provider's agent, which can only call out, polls it:

    GET  /outbox               request files not yet acknowledged (csv/mailbox-listing)
    GET  /outbox/{file_name}   one file's bytes, with X-Relay-SHA256
    POST /ack/{file_name}      the agent has the file (csv/mailbox-ack); idempotent
    PUT  /inbox/{file_name}    an answers or statuses file, write-once by name (csv/mailbox-receipt)

`Authorization: Bearer <token>`; the token names one provider's mailbox.
"""

import hashlib
import re

from tds_conform.csvfile import FILE_NAME

__all__ = [
    "AGENT_HEADER",
    "INBOX_KINDS",
    "MIN_MAX_FILE_BYTES",
    "SAFE_NAME",
    "SHA256_HEADER",
    "inbox_name_problem",
    "is_outbox_name",
    "sha256_hex",
]

SHA256_HEADER = "X-Relay-SHA256"
AGENT_HEADER = "X-Relay-Agent"
# Characters a mailbox file name may use at all (SPEC.md §9.4): nothing that could leave the
# mailbox's folder or need quoting.
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
INBOX_KINDS = ("answers", "statuses")
# The least max_file_bytes a mailbox may declare.
MIN_MAX_FILE_BYTES = 1_000_000


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_outbox_name(name: str) -> bool:
    """A requests file name per SPEC.md §9.2."""
    match = FILE_NAME.match(name)
    return bool(match and match.group(1) == "requests")


def inbox_name_problem(name: str) -> str | None:
    """Why `name` cannot be uploaded (SPEC.md §9.4), or None when it is an answers or statuses
    file name per SPEC.md §9.2."""
    if not SAFE_NAME.match(name) or name.startswith("."):
        return "use only letters, digits, '_', '-' and '.'"
    match = FILE_NAME.match(name)
    if match is None:
        return "not a file name per SPEC.md §9.2 (tds_<answers|statuses>_<YYYYMMDDTHHMMSSZ>[_<tag>].csv)"
    if match.group(1) not in INBOX_KINDS:
        return f"a {match.group(1)} file goes from requester to provider; upload answers or statuses files"
    return None
