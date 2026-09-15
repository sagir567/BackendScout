from backend_scout.portal_adapters import adapter_for_url


def test_detects_supported_ats_hosts() -> None:
    assert adapter_for_url("https://boards.greenhouse.io/acme/jobs/123").name == "greenhouse"
    assert adapter_for_url("https://www.comeet.com/jobs/acme/123").name == "comeet"
    assert adapter_for_url("https://jobs.lever.co/acme/123").name == "lever"
    assert adapter_for_url("https://il.linkedin.com/jobs/view/123").name == "linkedin-easy-apply"


def test_linkedin_adapter_uses_only_final_submit_controls() -> None:
    adapter = adapter_for_url("https://il.linkedin.com/jobs/view/123")

    assert adapter is not None
    assert 'button[aria-label="Submit application"]' in adapter.submit_selectors
    assert all("next" not in selector.casefold() for selector in adapter.submit_selectors)


def test_rejects_unknown_company_portal() -> None:
    assert adapter_for_url("https://careers.unknown-company.test/jobs/123") is None


def test_adapter_confirmation_markers_are_specific() -> None:
    adapter = adapter_for_url("https://jobs.lever.co/acme/123")
    assert adapter is not None
    assert "application submitted" in adapter.confirmation_markers
