"""Standard Webhooks headers alongside X-TDS-Signature (SPEC.md §12)."""

import base64
import json

import tds_conform.scenarios  # noqa: F401 - registers the scenarios
from tds_conform.events import (
    MAX_SKEW_S,
    delivery_problems,
    new_standard_secret,
    sign,
    sign_standard,
    standard_key,
    verify,
    verify_any,
    verify_standard,
)
from tds_conform.mock.provider import Provider
from tds_conform.runner import run_scenario

# Published test vector of the Standard Webhooks reference libraries.
VECTOR_SECRET = "whsec_MfKQ9r8GKYqrTwjUPD8ILPZIo2LaLaSw"
VECTOR_ID = "msg_p5jXN8AQM9LWM0D4loKWxJek"
VECTOR_TS = 1614265330
VECTOR_BODY = b'{"test": 2432232314}'
VECTOR_SIG = "v1,g0hM9SsE+OTPJTGt/tmIKtSyZlE3uFJELVlNIOLJ1OE="

EVENT = {
    "event_id": "evt-0001",
    "event_type": "TRIP_STATUS_CHANGED",
    "occurred_at": "2026-11-03T09:05:00-05:00",
    "provider_id": "aaata-a-ride",
    "trip_id": "tr-20261103-0001",
    "leg_id": "leg-1",
    "schema_version": "0.6.0",
    "payload": {"status": "IN_PROGRESS", "previous_status": "SCHEDULED"},
}
BODY = json.dumps(EVENT).encode()
TS = 1793714702


def test_matches_the_standard_webhooks_test_vector():
    headers = sign_standard(VECTOR_SECRET, VECTOR_ID, VECTOR_BODY, VECTOR_TS)
    assert headers == {"webhook-id": VECTOR_ID, "webhook-timestamp": str(VECTOR_TS), "webhook-signature": VECTOR_SIG}
    assert verify_standard(VECTOR_SECRET, headers, VECTOR_BODY, now=VECTOR_TS) is None


def test_secret_format():
    secret = new_standard_secret()
    assert secret.startswith("whsec_") and len(standard_key(secret)) == 32
    assert standard_key("whsec_" + base64.b64encode(b"x" * 16).decode()) is None  # too short
    assert standard_key("whsec_not base64!") is None
    assert standard_key("plain-shared-secret-0123456789abcdef") is None


def test_non_standard_secret_gets_x_tds_signature_only():
    assert sign_standard("plain-shared-secret-0123456789abcdef", "evt-1", BODY, TS) == {}


def test_rotation_lists_one_signature_per_secret_and_either_verifies():
    old, new = new_standard_secret(), new_standard_secret()
    headers = sign_standard([new, old], "evt-0001", BODY, TS)
    assert len(headers["webhook-signature"].split(" ")) == 2
    assert verify_standard(old, headers, BODY, now=TS) is None
    assert verify_standard(new, headers, BODY, now=TS) is None
    assert verify_standard(new_standard_secret(), headers, BODY, now=TS) == "no webhook-signature entry matches"


def test_unknown_versions_are_ignored_and_tampering_detected():
    secret = new_standard_secret()
    headers = sign_standard(secret, "evt-0001", BODY, TS)
    headers["webhook-signature"] = "v1a,AAAA " + headers["webhook-signature"]
    assert verify_standard(secret, headers, BODY, now=TS) is None
    assert verify_standard(secret, headers, BODY + b" ", now=TS) is not None
    assert verify_standard(secret, headers | {"webhook-id": "evt-9"}, BODY, now=TS) is not None
    assert "from now" in verify_standard(secret, headers, BODY, now=TS + MAX_SKEW_S + 1)


def test_header_names_are_case_insensitive():
    secret = new_standard_secret()
    sent = {**sign(secret, BODY, TS), **sign_standard(secret, "evt-0001", BODY, TS)}
    headers = {k.upper(): v for k, v in sent.items()}
    assert verify(secret, headers, BODY, now=TS) is None
    assert verify_standard(secret, headers, BODY, now=TS) is None


def test_receiver_accepts_either_signature():
    secret = new_standard_secret()
    x_tds = sign(secret, BODY, TS)
    standard = sign_standard(secret, "evt-0001", BODY, TS)
    assert verify_any(secret, x_tds, BODY, now=TS) is None
    assert verify_any(secret, standard, BODY, now=TS) is None
    assert verify_any(secret, x_tds | standard, BODY, now=TS) is None
    broken = x_tds | {"X-TDS-Signature": "sha256=" + "0" * 64}
    assert verify_any(secret, broken | standard, BODY, now=TS) is None
    assert verify_any(secret, broken, BODY, now=TS) == "signature does not match"
    # A receiver mid-rotation holds both secrets; the sender still signs X-TDS with the old one.
    old = new_standard_secret()
    assert verify_any([secret, old], sign(old, BODY, TS), BODY, now=TS) is None


def test_delivery_problems_checks_webhook_id_is_the_event_id():
    secret = new_standard_secret()
    headers = {**sign(secret, BODY, TS), **sign_standard(secret, "evt-other", BODY, TS)}
    assert "webhook-id is not the event_id" in delivery_problems(secret, headers, BODY, now=TS)
    good = {**sign(secret, BODY, TS), **sign_standard(secret, "evt-0001", BODY, TS)}
    assert delivery_problems(secret, good, BODY, now=TS) == []


def test_kit_checks_standard_webhooks_headers(serve, monkeypatch):
    from tds_conform.mock import network
    from tds_conform.receiver import Receiver

    real = network.sign_standard
    monkeypatch.setattr(network, "sign_standard", lambda s, i, b, t: real(new_standard_secret(), i, b, t))
    settings, client = serve(Provider())  # X-TDS-Signature right, webhook-signature with another secret
    with Receiver() as receiver:
        settings.receiver = receiver
        result = run_scenario("webhook_delivery", settings, client, 0)
    failed = {c.name for s in result.steps for c in s.checks if not c.passed}
    assert result.outcome == "FAIL"
    assert failed == {"Standard Webhooks headers, when sent, verify (webhook-signature v1, webhook-id = event_id)"}


def test_reference_provider_sends_both_header_sets(serve):
    from tds_conform.receiver import Receiver

    settings, client = serve(Provider())
    with Receiver() as receiver:
        settings.receiver = receiver
        result = run_scenario("webhook_delivery", settings, client, 0)
        assert result.outcome == "PASS", [(c.name, c.detail) for s in result.steps for c in s.checks if not c.passed]
        names = {k.lower() for k in receiver.deliveries[0].headers}
    assert {"x-tds-signature", "x-tds-timestamp", "webhook-id", "webhook-timestamp", "webhook-signature"} <= names
