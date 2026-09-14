from backend_scout.portal_adapters import adapter_for_url


def test_detects_supported_ats_hosts() -> None:
    assert adapter_for_url("https://boards.greenhouse.io/acme/jobs/123").name == "greenhouse"
    assert adapter_for_url("https://www.comeet.com/jobs/acme/123").name == "comeet"
    assert adapter_for_url("https://jobs.lever.co/acme/123").name == "lever"


def test_rejects_unknown_company_portal() -> None:
    assert adapter_for_url("https://careers.unknown-company.test/jobs/123") is None


def test_adapter_confirmation_markers_are_specific() -> None:
    adapter = adapter_for_url("https://jobs.lever.co/acme/123")
    assert adapter is not None
    assert "application submitted" in adapter.confirmation_markers
