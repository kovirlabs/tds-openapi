"""Reading, changing and ending subscriptions (SPEC.md §9.3): the reference provider, delivery, the kit."""

import time

import pytest

import tds_conform.scenarios  # noqa: F401 - registers the scenarios
from tds_conform.data import example, schema_errors
from tds_conform.events import delivery_problems
from tds_conform.mock.network import Dispatcher
from tds_conform.mock.provider import Provider, ProviderConfig
from tds_conform.receiver import Receiver
from tds_conform.runner import run_scenario

REQ = "throughride-demo"
SUB_REQUEST = example("subscription-changes", "01-subscribe.request.json")
CHANGE = example("subscription-changes", "03-add-eta-update.request.json")


@pytest.fixture
def provider(clock):
    return Provider(ProviderConfig(), clock=clock)


def subscribe(provider: Provider, requester: str = REQ, key: str = "k-sub") -> dict:
    status, body = provider.subscribe(requester, key, SUB_REQUEST)
    assert status == 201
    return body


def failed_checks(result) -> set[str]:
    return {c.name for s in result.steps for c in s.checks if not c.passed}


# --- reference provider ---------------------------------------------------------------


def test_health_declares_subscription_management(provider):
    assert provider.health()[1]["subscription_management"] is True
    assert "subscription_management" not in Provider(ProviderConfig(subscription_management=False)).health()[1]


def test_read_and_list_never_return_the_secret(provider):
    sub = subscribe(provider)
    status, body = provider.get_subscription(REQ, sub["subscription_id"])
    assert status == 200 and schema_errors(body, "subscription") == []
    assert body == {k: v for k, v in sub.items() if k != "signing_secret"}
    status, listing = provider.list_subscriptions(REQ)
    assert status == 200 and schema_errors(listing, "subscription-list") == []
    assert listing == {"subscriptions": [body]}
    assert provider.list_subscriptions("someone-else") == (200, {"subscriptions": []})


def test_patch_replaces_event_types_and_keeps_the_secret(provider, clock):
    sub = subscribe(provider)
    sub_id = sub["subscription_id"]
    clock.advance(60)
    status, body = provider.update_subscription(REQ, "k-patch", sub_id, CHANGE)
    assert status == 200 and schema_errors(body, "subscription") == []
    assert body["event_types"] == CHANGE["event_types"] and "signing_secret" not in body
    assert body["updated_at"] == clock().isoformat(timespec="seconds")
    assert (body["subscription_id"], body["signing_secret_id"], body["created_at"]) == (
        sub_id,
        sub["signing_secret_id"],
        sub["created_at"],
    )
    stored = provider._subscriptions[sub_id][1]
    assert provider.signing_secrets(stored) == [sub["signing_secret"]]
    assert provider.subscriptions_for(REQ, "ETA_UPDATE") == [stored]
    # Replace, not merge: dropping a type stops it.
    provider.update_subscription(REQ, "k-drop", sub_id, {"event_types": ["ETA_UPDATE"]})
    assert provider.subscriptions_for(REQ, "MILESTONE") == []


def test_patch_other_fields(provider):
    sub = subscribe(provider)
    change = {"callback_url": "https://broker.example.org/tds/v2/events", "cloudevents": True}
    status, body = provider.update_subscription(REQ, "k1", sub["subscription_id"], change)
    assert status == 200 and body["callback_url"] == change["callback_url"] and body["cloudevents"] is True
    assert body["event_types"] == sub["event_types"]


def test_patch_is_idempotent(provider):
    sub = subscribe(provider)
    first = provider.update_subscription(REQ, "k1", sub["subscription_id"], CHANGE)
    assert provider.update_subscription(REQ, "k1", sub["subscription_id"], CHANGE) == first
    status, body = provider.update_subscription(REQ, "k1", sub["subscription_id"], {"cloudevents": True})
    assert (status, body["code"]) == (409, "IDEMPOTENCY_KEY_REUSED")
    assert provider.update_subscription(REQ, None, sub["subscription_id"], CHANGE)[0] == 400


@pytest.mark.parametrize(
    "change",
    [
        {},
        {"event_types": []},
        {"event_types": ["NOT_AN_EVENT"]},
        {"callback_url": "http://broker.example.org/events"},
        {"unrelated": True},
    ],
)
def test_patch_validates(provider, change):
    sub = subscribe(provider)
    status, body = provider.update_subscription(REQ, "k1", sub["subscription_id"], change)
    assert (status, body["code"]) == (400, "INVALID_REQUEST")


def test_scoped_to_the_requester(provider):
    sub = subscribe(provider)
    sub_id = sub["subscription_id"]
    for requester, ident in (("someone-else", sub_id), (REQ, "sub-missing")):
        assert provider.get_subscription(requester, ident)[1]["code"] == "NOT_FOUND"
        assert provider.update_subscription(requester, "k1", ident, CHANGE)[0] == 404
        assert provider.delete_subscription(requester, "k2", ident)[0] == 404
    assert provider._subscriptions[sub_id][1]["event_types"] == sub["event_types"]


def test_delete_ends_the_subscription(provider):
    sub = subscribe(provider)
    sub_id = sub["subscription_id"]
    stored = provider._subscriptions[sub_id][1]
    status, body = provider.delete_subscription(REQ, "k-del", sub_id)
    assert status == 200 and schema_errors(body, "subscription") == [] and "signing_secret" not in body
    assert provider.delete_subscription(REQ, "k-del", sub_id) == (200, body)  # replay
    assert provider.delete_subscription(REQ, "k-other", sub_id)[0] == 404
    assert provider.get_subscription(REQ, sub_id)[0] == 404
    assert provider.list_subscriptions(REQ)[1] == {"subscriptions": []}
    assert provider.subscriptions_for(REQ, "TRIP_STATUS_CHANGED") == []
    assert provider.signing_secrets(stored) == []
    assert provider.list_secrets(REQ, sub_id)[0] == 404  # its secrets went with it


def test_operations_are_absent_when_not_declared(clock):
    provider = Provider(ProviderConfig(subscription_management=False), clock=clock)
    sub = subscribe(provider)
    assert provider.list_subscriptions(REQ)[0] == 404
    assert provider.get_subscription(REQ, sub["subscription_id"])[0] == 404
    assert provider.update_subscription(REQ, "k1", sub["subscription_id"], CHANGE)[0] == 404
    assert provider.delete_subscription(REQ, "k2", sub["subscription_id"])[0] == 404


# --- delivery -------------------------------------------------------------------------


def test_dispatcher_drops_retries_for_an_ended_subscription():
    """A retry due after DELETE is not attempted (SPEC.md §9.3)."""
    dispatcher = Dispatcher(retry_base_s=0.05)
    provider = Provider(ProviderConfig())
    sub = subscribe(provider)
    sub["callback_url"] = "http://127.0.0.1:9/unreachable"
    stored = provider._subscriptions[sub["subscription_id"]][1]
    stored["callback_url"] = sub["callback_url"]
    dispatcher.start()
    try:
        dispatcher.submit(stored, {"event_id": "evt-1"}, lambda: provider.signing_secrets(stored))
        deadline = time.monotonic() + 3
        while not dispatcher.log and time.monotonic() < deadline:
            time.sleep(0.01)
        provider.delete_subscription(REQ, "k-del", sub["subscription_id"])
        while not any(e["outcome"].startswith("dropped") for e in dispatcher.log) and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        dispatcher.stop()
    outcomes = [e["outcome"] for e in dispatcher.log]
    assert outcomes[0].startswith("error") and outcomes[-1] == "dropped: subscription ended"
    assert len(outcomes) == 2


def test_example_delivery_verifies_with_the_original_secret():
    sub = example("subscription-changes", "01-subscribe.response.json")
    changed = example("subscription-changes", "03-add-eta-update.response.json")
    delivery = example("subscription-changes", "05-eta-update.delivery.json")
    ts = int(delivery["headers"]["X-TDS-Timestamp"])
    assert delivery_problems(sub["signing_secret"], delivery["headers"], delivery["body"].encode(), now=ts) == []
    assert "ETA_UPDATE" in changed["event_types"] and "ETA_UPDATE" not in sub["event_types"]
    assert changed["signing_secret_id"] == sub["signing_secret_id"]


# --- kit scenario ---------------------------------------------------------------------


def test_reference_provider_passes(serve):
    settings, client = serve(Provider())
    with Receiver() as receiver:
        settings.receiver = receiver
        result = run_scenario("subscription_management", settings, client, 0)
    assert result.outcome == "PASS", [(c.name, c.detail) for s in result.steps for c in s.checks if not c.passed]
    assert len(result.steps) > 15


def test_passes_without_receiver_on_api_checks_only(serve):
    settings, client = serve(Provider())
    result = run_scenario("subscription_management", settings, client, 0)
    assert result.outcome == "PASS", failed_checks(result)


def test_undeclared_management_is_not_checked(serve):
    settings, client = serve(Provider(ProviderConfig(subscription_management=False)))
    result = run_scenario("subscription_management", settings, client, 0)
    assert result.outcome == "PASS" and "not declared" in result.note


class ReadsSecret(Provider):
    """Returns the signing secret when the subscription is read."""

    def get_subscription(self, requester, subscription_id):
        status, body = super().get_subscription(requester, subscription_id)
        if status == 200:
            body["signing_secret"] = self._subscriptions[subscription_id][1]["signing_secret"]
        return status, body


class MergesEventTypes(Provider):
    """Adds the new event types to the old ones instead of replacing them."""

    def _update_subscription(self, requester, subscription_id, body):
        owned = self._subscriptions.get(subscription_id)
        if owned and isinstance(body, dict) and "event_types" in body:
            body = {**body, "event_types": list(dict.fromkeys(owned[1]["event_types"] + body["event_types"]))}
        return super()._update_subscription(requester, subscription_id, body)


class NewSecretOnChange(Provider):
    """Mints a new secret when the subscription changes, without telling the subscriber."""

    def _update_subscription(self, requester, subscription_id, body):
        status, answer = super()._update_subscription(requester, subscription_id, body)
        if status == 200:
            self._subscriptions[subscription_id][1]["signing_secret"] = self.new_secret()
        return status, answer


class KeepsDelivering(Provider):
    """Answers DELETE but keeps the subscription for deliveries."""

    def _delete_subscription(self, requester, subscription_id):
        owned = self._subscriptions.get(subscription_id)
        status, body = super()._delete_subscription(requester, subscription_id)
        if status == 200:
            self._ghosts = [*getattr(self, "_ghosts", []), owned]
        return status, body

    def subscriptions_for(self, requester, event_type):
        ghosts = [
            s for owner, s in getattr(self, "_ghosts", []) if owner == requester and event_type in s["event_types"]
        ]
        return super().subscriptions_for(requester, event_type) + ghosts

    def signing_secrets(self, subscription):
        return super().signing_secrets(subscription) or [subscription["signing_secret"]]


class ForgetsNothing(Provider):
    """DELETE answers 200 but the subscription can still be read."""

    def _delete_subscription(self, requester, subscription_id):
        refusal = self._check_subscription_api(requester, subscription_id)
        return refusal or (200, self.get_subscription(requester, subscription_id)[1])


@pytest.mark.parametrize(
    ("provider", "failing_check"),
    [
        (ReadsSecret(), "reading carries no signing_secret"),
        (MergesEventTypes(), "event_types replaced as a whole"),
        (MergesEventTypes(), "after the change, only the new event types are delivered"),
        (NewSecretOnChange(), "after the change, deliveries verify with the secret from subscribing"),
        (KeepsDelivering(), "nothing is delivered to an ended subscription"),
        (ForgetsNothing(), "status is 404"),
    ],
)
def test_kit_catches_faults(serve, provider, failing_check):
    settings, client = serve(provider)
    with Receiver() as receiver:
        settings.receiver = receiver
        result = run_scenario("subscription_management", settings, client, 0)
    assert result.outcome == "FAIL", result.note
    assert failing_check in failed_checks(result)
