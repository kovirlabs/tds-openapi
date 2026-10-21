"""`tds-conform fuzz`: schemathesis against the reference provider, and proof it can fail."""

import pytest

from tds_conform.fuzz import default_spec, fuzz
from tds_conform.mock import Provider, ProviderConfig

pytestmark = pytest.mark.skipif(not default_spec().is_file(), reason="needs dist/openapi.yaml (npm run bundle)")

REQ = "throughride-demo"


def run(url: str, **kwargs):
    kwargs.setdefault("max_examples", 5)
    return fuzz(url, REQ, REQ, "aaata-a-ride", **kwargs)


def test_reference_provider_passes(serve):
    settings, _ = serve(Provider(ProviderConfig()))
    results = run(settings.url)
    assert [r for r in results if r.status in ("FAIL", "ERROR")] == []
    assert {r.operation for r in results if r.status == "PASS"} >= {"POST /trips", "POST /tenders", "GET /health"}
    assert all(r.status == "SKIPPED" for r in results if r.operation == "POST /subscriptions")


class BadHealth(Provider):
    def health(self):
        return 200, {"status": "OK"}  # spec_versions, profiles, operated_via missing


class TeapotConfirm(Provider):
    def confirm_trip(self, requester, key, trip_id):
        return 418, {"type": "about:blank", "title": "Teapot", "status": 418, "code": "INVALID_REQUEST"}


@pytest.mark.parametrize(
    ("provider", "operation", "fragment"),
    [
        (BadHealth, "GET /health", "schema"),
        (TeapotConfirm, "POST /trips/{trip_id}/confirm", "Undocumented status code"),
    ],
)
def test_faulty_providers_fail(serve, provider, operation, fragment):
    settings, _ = serve(provider(ProviderConfig()))
    (result,) = run(settings.url, include={operation}, modes=("positive",))
    assert result.status == "FAIL" and fragment.lower() in result.detail.lower(), result.detail


def test_mock_rejects_undecodable_bodies(serve):
    """Found by fuzzing: a body that is not UTF-8 JSON used to drop the connection."""
    settings, client = serve(Provider(ProviderConfig()))
    response = client.post(
        "/trips",
        content=b"\x00\x00\x00\x80\x00",
        headers={"Authorization": f"Bearer {REQ}", "Idempotency-Key": "k", "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.headers["content-type"] == "application/problem+json"


def test_unreachable_endpoint_is_an_error_not_a_failure():
    (result,) = run("http://127.0.0.1:9", include={"GET /health"}, modes=("positive",), max_examples=1)
    assert result.status == "ERROR"


def test_missing_spec_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError, match="npm run bundle"):
        run("http://127.0.0.1:9", spec=tmp_path / "none.yaml")
