import httpx
import pytest

from backend_scout.job_space import JobSpaceError, fetch_job_space_jobs, job_space_response_shape


def test_search_is_read_only_and_reports_only_shape():
    def respond(request):
        assert request.method == "GET"
        assert request.url.host == "jobs.ti-space.com"
        assert request.url.path == "/api/v1/jobs"
        assert dict(request.url.params) == {"q": "python", "seniority": "junior"}
        assert request.headers["authorization"] == "Bearer fixture"
        return httpx.Response(200, json={"jobs": [{"title": "private fixture value"}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        payload = fetch_job_space_jobs(client, "fixture", query="python")
    assert job_space_response_shape(payload) == {
        "root": "dict", "fields": ["jobs"], "job_fields": ["title"], "items": 1,
    }


@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
def test_errors_never_expose_body_or_forward_credential(status):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, text="private fixture", headers={"Location": "https://example.test/"})

    with (
        httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=True) as client,
        pytest.raises(JobSpaceError) as error,
    ):
        fetch_job_space_jobs(client, "fixture", query="python")
    assert len(requests) == 1
    assert "fixture" not in str(error.value)
