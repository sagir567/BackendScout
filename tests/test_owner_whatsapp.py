import json
from dataclasses import replace

import httpx
import pytest

from backend_scout.owner_whatsapp import (
    OwnerWhatsAppClient,
    OwnerWhatsAppConfig,
    WhatsAppNotificationError,
)


def config():
    return OwnerWhatsAppConfig(enabled=True, access_token="test-secret", phone_number_id="12345678",
                               owner_number="972500000000", template_name="job_status")


def test_settings_default_disabled_and_secret_is_masked():
    from backend_scout.config import Settings

    settings = Settings(_env_file=None, owner_whatsapp_access_token="private-test-value")
    configured = OwnerWhatsAppConfig.from_settings(settings)
    assert configured.enabled is False
    assert configured.missing_fields() == ["phone_number_id", "owner_number", "template_name"]
    assert "private-test-value" not in repr(settings)
    assert "private-test-value" not in settings.model_dump_json()
    assert "private-test-value" not in repr(configured)


def test_owner_template_fixed_recipient_and_acceptance_only():
    requests = []

    def handler(request):
        requests.append(request)
        payload = json.loads(request.content)
        assert payload["to"] == config().owner_number
        assert payload["type"] == "template"
        assert payload["template"]["components"][0]["parameters"][0]["text"] == "New job found"
        return httpx.Response(200, json={"messages": [{"id": "wamid.example"}]})

    client = OwnerWhatsAppClient(config(), transport=httpx.MockTransport(handler))
    assert client.send_template("New job found") == "wamid.example"
    assert len(requests) == 1
    assert requests[0].url.host == "graph.facebook.com"
    assert "test-secret" not in repr(config())


@pytest.mark.parametrize("change", [{"enabled": False}, {"owner_number": ""},
                                  {"phone_number_id": "../other"}, {"access_token": ""}])
def test_invalid_config_never_sends(change):
    def handler(_):
        pytest.fail("No network call expected")

    with pytest.raises(WhatsAppNotificationError):
        OwnerWhatsAppClient(replace(config(), **change), transport=httpx.MockTransport(handler)).send_template("Update")


@pytest.mark.parametrize("status,body", [(302, {}), (400, {"error": "test-secret"}),
                                       (200, {}), (200, {"messages": []})])
def test_unacknowledged_response_no_retry_or_secret_disclosure(status, body):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, json=body, headers={"Location": "https://other.test"})

    with pytest.raises(WhatsAppNotificationError) as error:
        OwnerWhatsAppClient(config(), transport=httpx.MockTransport(handler)).send_template("Update")
    assert "test-secret" not in str(error.value)
    assert len(calls) == 1
