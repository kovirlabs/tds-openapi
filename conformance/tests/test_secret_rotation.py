"""Subscription secret rotation (SPEC.md §12.1): the reference provider, signing, and the kit scenario."""

import json
from datetime import datetime, timedelta

import pytest

import tds_conform.scenarios  # noqa: F401 - registers the scenarios
from tds_conform.data import example, schema_errors
from tds_conform.events import delivery_problems, new_standard_secret, sign, sign_standard
from tds_conform.mock.provider import Provider, ProviderConfig
from tds_conform.receiver import Receiver
from tds_conform.runner import run_scenario

REQ = "throughride-demo"
SUB_REQUEST = example("secret-rotation", "01-subscribe.request.json")


@pytest.fixture
def provider(clock):
    return Provider(ProviderConfig(), clock=clock)


def subscribe(provider: Provider) -> dict:
    status, body = provider.subscribe(REQ, "k-sub", SUB_REQUEST)
    assert status == 201
    return body


def failed_checks(result) -> set[str]:
    return {c.name for s in result.steps for c in s.checks if not c.passed}


# --- reference provider ---------------------------------------------------------------


def test_health_declares_secret_rotation(provider):
    assert provider.health()[1]["secret_rotation"] is True
    assert "secret_rotation" not in Provider(ProviderConfig(secret_rotation=False)).health()[1]


def test_subscription_names_its_first_secret(provider):
    sub = subscribe(provider)
    assert sub["signing_secret_id"] == "sec-1"
    assert schema_errors(sub, "subscription") == []


def test_rotation_keeps_the_old_secret_for_the_overlap(provider, clock):
    sub = subscribe(provider)
    old, sub_id = sub["signing_secret"], sub["subscription_id"]
    status, body = provider.rotate_secret(REQ, "k-rot", sub_id, {"overlap_s": 3600})
    assert status == 201 and schema_errors(body, "subscription-secret-list") == []
    new, older = body["secrets"]
    assert new["secret_id"] == "sec-2" and new["signing_secret"] != old and "expires_at" not in new
    assert older == {
        "secret_id": "sec-1",
        "created_at": sub["created_at"],
        "expires_at": (clock() + timedelta(seconds=3600)).isoformat(timespec="seconds"),
    }
    stored = provider._subscriptions[sub_id][1]
    assert provider.signing_secrets(stored) == [new["signing_secret"], old]

    clock.advance(3600)
    assert provider.signing_secrets(stored) == [new["signing_secret"]]
    status, listing = provider.list_secrets(REQ, sub_id)
    assert status == 200 and [s["secret_id"] for s in listing["secrets"]] == ["sec-2"]
    assert "signing_secret" not in listing["secrets"][0]


def test_rotation_without_body_uses_the_default_overlap(provider, clock):
    sub = subscribe(provider)
    status, body = provider.rotate_secret(REQ, "k-rot", sub["subscription_id"], None)
    assert status == 201
    assert body["secrets"][1]["expires_at"] == (clock() + timedelta(days=1)).isoformat(timespec="seconds")


def test_overlap_zero_retires_older_secrets_at_once(provider):
    sub = subscribe(provider)
    status, body = provider.rotate_secret(REQ, "k-rot", sub["subscription_id"], {"overlap_s": 0})
    assert status == 201 and [s["secret_id"] for s in body["secrets"]] == ["sec-2"]
    assert provider.signing_secrets(provider._subscriptions[sub["subscription_id"]][1]) == [
        body["secrets"][0]["signing_secret"]
    ]


def test_overlap_is_capped_and_never_extends_an_expiry(provider, clock):
    sub, cap = subscribe(provider), provider.config.max_overlap_s
    _, first = provider.rotate_secret(REQ, "k1", sub["subscription_id"], {"overlap_s": 3600})
    _, second = provider.rotate_secret(REQ, "k2", sub["subscription_id"], {"overlap_s": cap * 2})
    expiries = {s["secret_id"]: s.get("expires_at") for s in second["secrets"]}
    assert expiries["sec-1"] == first["secrets"][1]["expires_at"]  # not lengthened
    assert expiries["sec-2"] == (clock() + timedelta(seconds=cap)).isoformat(timespec="seconds")


def test_rotation_is_idempotent(provider):
    sub = subscribe(provider)
    first = provider.rotate_secret(REQ, "k-rot", sub["subscription_id"], {"overlap_s": 60})
    assert provider.rotate_secret(REQ, "k-rot", sub["subscription_id"], {"overlap_s": 60}) == first
    status, body = provider.rotate_secret(REQ, "k-rot", sub["subscription_id"], {"overlap_s": 61})
    assert (status, body["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")


def test_rotation_validates_and_scopes(provider):
    sub = subscribe(provider)
    status, body = provider.rotate_secret(REQ, "k1", sub["subscription_id"], {"overlap_s": -1})
    assert (status, body["code"]) == (400, "INVALID_REQUEST")
    for requester, sub_id in (("someone-else", sub["subscription_id"]), (REQ, "sub-missing")):
        status, body = provider.rotate_secret(requester, "k2", sub_id, {})
        assert (status, body["code"]) == (404, "NOT_FOUND")
        assert provider.list_secrets(requester, sub_id)[0] == 404


def test_too_many_active_secrets_is_refused(clock):
    provider = Provider(ProviderConfig(max_active_secrets=2), clock=clock)
    sub = subscribe(provider)
    assert provider.rotate_secret(REQ, "k1", sub["subscription_id"], {})[0] == 201
    status, body = provider.rotate_secret(REQ, "k2", sub["subscription_id"], {})
    assert (status, body["code"]) == (409, "INVALID_STATE")


def test_retiring_secrets(provider, clock):
    sub = subscribe(provider)
    sub_id = sub["subscription_id"]
    provider.rotate_secret(REQ, "k-rot", sub_id, {"overlap_s": 3600})
    status, body = provider.retire_secret(REQ, "k1", sub_id, "sec-2")
    assert (status, body["code"]) == (409, "INVALID_STATE")
    clock.advance(10)
    status, body = provider.retire_secret(REQ, "k2", sub_id, "sec-1")
    assert status == 200 and schema_errors(body, "subscription-secret") == []
    assert body["expires_at"] == clock().isoformat(timespec="seconds") and "signing_secret" not in body
    assert [s["secret_id"] for s in provider.list_secrets(REQ, sub_id)[1]["secrets"]] == ["sec-2"]
    clock.advance(10)
    assert provider.retire_secret(REQ, "k3", sub_id, "sec-1") == (200, body)  # already expired: unchanged
    assert provider.retire_secret(REQ, "k4", sub_id, "sec-9")[0] == 404
    assert provider.retire_secret(REQ, None, sub_id, "sec-1")[0] == 400


def test_operations_are_absent_when_not_declared(clock):
    provider = Provider(ProviderConfig(secret_rotation=False), clock=clock)
    sub = subscribe(provider)
    assert "signing_secret_id" not in sub
    assert provider.rotate_secret(REQ, "k1", sub["subscription_id"], {})[0] == 404


# --- signatures -----------------------------------------------------------------------

EVENT = example("secret-rotation", "04-delivery-during-overlap.delivery.json")


def test_delivery_problems_wants_an_entry_per_active_secret():
    old, new = new_standard_secret(), new_standard_secret()
    body, ts = EVENT["body"].encode(), 1793631602
    event_id = json.loads(body)["event_id"]
    both = {**sign(new, body, ts), **sign_standard([new, old], event_id, body, ts)}
    assert delivery_problems(new, both, body, now=ts, older_secrets=[old]) == []
    only_new = {**sign(new, body, ts), **sign_standard(new, event_id, body, ts)}
    assert delivery_problems(new, only_new, body, now=ts) == []
    assert delivery_problems(new, only_new, body, now=ts, older_secrets=[old]) == [
        "no webhook-signature entry for an older secret still active"
    ]


def test_example_delivery_has_both_signatures():
    sub = example("secret-rotation", "01-subscribe.response.json")
    rotation = example("secret-rotation", "02-rotate.response.json")
    newest = rotation["secrets"][0]["signing_secret"]
    ts = int(EVENT["headers"]["X-TDS-Timestamp"])
    body = EVENT["body"].encode()
    assert delivery_problems(newest, EVENT["headers"], body, now=ts, older_secrets=[sub["signing_secret"]]) == []
    # Rotated and delivered within the overlap the example states.
    sent = datetime.fromtimestamp(ts).astimezone()
    assert datetime.fromisoformat(rotation["secrets"][0]["created_at"]) <= sent
    assert sent < datetime.fromisoformat(rotation["secrets"][1]["expires_at"])


# --- kit scenario ---------------------------------------------------------------------


def test_reference_provider_passes(serve):
    settings, client = serve(Provider())
    with Receiver() as receiver:
        settings.receiver = receiver
        result = run_scenario("secret_rotation", settings, client, 0)
    assert result.outcome == "PASS", [(c.name, c.detail) for s in result.steps for c in s.checks if not c.passed]
    assert len(result.steps) > 10


def test_passes_without_receiver_on_api_checks_only(serve):
    settings, client = serve(Provider())
    result = run_scenario("secret_rotation", settings, client, 0)
    assert result.outcome == "PASS", failed_checks(result)


def test_undeclared_rotation_is_not_checked(serve):
    settings, client = serve(Provider(ProviderConfig(secret_rotation=False)))
    result = run_scenario("secret_rotation", settings, client, 0)
    assert result.outcome == "PASS" and "not declared" in result.note


class DropsOldSecret(Provider):
    """Signs only with the newest secret during the overlap."""

    def signing_secrets(self, subscription):
        return super().signing_secrets(subscription)[:1]


class RetiresNewest(Provider):
    """Lets the newest secret be retired."""

    def _retire_secret(self, requester, subscription_id, secret_id):
        secrets_ = self._secrets.get(subscription_id, [])
        if secrets_ and secrets_[0].secret_id == secret_id:
            return 200, secrets_[0].public()
        return super()._retire_secret(requester, subscription_id, secret_id)


class ListsValues(Provider):
    """Puts secret values in listings."""

    def list_secrets(self, requester, subscription_id):
        status, body = super().list_secrets(requester, subscription_id)
        if status == 200:
            values = {s.secret_id: s.value for s in self._secrets[subscription_id]}
            for entry in body["secrets"]:
                entry["signing_secret"] = values[entry["secret_id"]]
        return status, body


class KeepsRetiredSecret(Provider):
    """Retiring answers 200 but keeps signing with the old secret."""

    def _retire_secret(self, requester, subscription_id, secret_id):
        status, body = super()._retire_secret(requester, subscription_id, secret_id)
        for secret in self._secrets.get(subscription_id, []):
            if secret.secret_id == secret_id and secret is not self._secrets[subscription_id][0]:
                secret.expires_at = None
        return status, body


@pytest.mark.parametrize(
    ("provider", "failing_check"),
    [
        (DropsOldSecret(), "during the overlap, webhook-signature has a v1 entry for the new and the old secret"),
        (RetiresNewest(), "status is 409"),
        (ListsValues(), "a listing carries no secret values"),
        (KeepsRetiredSecret(), "after retiring, no signature is made with the old secret"),
    ],
)
def test_kit_catches_faults(serve, provider, failing_check):
    settings, client = serve(provider)
    with Receiver() as receiver:
        settings.receiver = receiver
        result = run_scenario("secret_rotation", settings, client, 0)
    assert result.outcome == "FAIL", result.note
    assert failing_check in failed_checks(result)
