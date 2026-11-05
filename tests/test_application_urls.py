import pytest

from backend_scout.application_urls import application_entry_url, is_authentication_url


@pytest.mark.parametrize("url", [
    "https://www.linkedin.com/login?session_redirect=job",
    "https://www.linkedin.com/authwall",
    "https://jobs.example/en-US/careers/signin",
    "https://jobs.example/signup/",
])
def test_sign_in_pages_are_not_reusable_application_destinations(url):
    assert is_authentication_url(url)
    assert application_entry_url(url, "https://example.com/jobs/123") == "https://example.com/jobs/123"


@pytest.mark.parametrize("url", [
    "javascript:alert(1)", "file:///private/cv.pdf", "https://user:password@example.com/jobs/1",
    "https://example.com/jobs/1?access_token=private", "https://example.com/jobs/1?code=private",
    "https://example.com/jobs/1?password=private", "https://[invalid",
])
def test_application_links_never_expose_credentials_or_non_web_urls(url):
    assert application_entry_url(url) is None


def test_official_entry_is_preferred_without_rewriting_job_identity():
    url = "https://jobs.example/job/login-engineer_JR-123/apply?source=LinkedIn"
    assert application_entry_url(url, "https://linkedin.com/jobs/view/123") == url
    assert not is_authentication_url(url)
