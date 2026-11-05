"""Public synthetic schemas only; all requests are mocked, with no candidate data."""

import gzip
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import httpx
import pytest

from backend_scout import greenhouse_forms as forms
from backend_scout import job_enrichment as enrichment
from backend_scout.greenhouse_forms import GreenhouseFormInspection, inspect_greenhouse_form
from backend_scout.models import Job

POSTING = "https://job-boards.greenhouse.io/example/jobs/12345"
API = "https://boards-api.greenhouse.io/v1/boards/example/jobs/12345?content=true&questions=true"


def _job(**updates):
    return Job(
        **{
            "source": "fixture",
            "source_url": POSTING,
            "title": "Backend Engineer",
            "company": "Example Labs",
            "description": "Public job summary.",
            **updates,
        }
    )


def _question(name="first_name", kind="input_text", *, required=True, **updates):
    return {
        "label": "First Name",
        "required": required,
        "fields": [{"name": name, "type": kind}],
        **updates,
    }


def _payload(**updates):
    return {
        "id": 12345,
        "title": "Backend Engineer",
        "company_name": "Example Labs",
        "questions": [_question()],
        **updates,
    }


@pytest.fixture
def fetcher_factory():
    clients = []

    def factory(handler):
        client = httpx.Client(
            transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False
        )
        clients.append(client)

        def fetch(url):
            return client.send(client.build_request("GET", url), stream=True)

        return fetch

    yield factory
    for client in clients:
        client.close()


@pytest.fixture
def inspect(fetcher_factory):
    def run(payload=None, *, job=None, status=200, headers=None, content=None):
        requests, responses = [], []

        def handle(request):
            requests.append(request)
            assert request.method == "GET"
            assert not request.content
            assert "authorization" not in request.headers
            assert "cookie" not in request.headers
            response = httpx.Response(
                status,
                **({"content": content} if content is not None else {"json": payload}),
                headers=headers,
            )
            responses.append(response)
            return response

        result = inspect_greenhouse_form(job or _job(), fetcher=fetcher_factory(handle))
        assert len(requests) <= 1
        assert all(response.is_closed for response in responses)
        return result, requests

    return run


def _unavailable(result, reason):
    assert isinstance(result, GreenhouseFormInspection)
    assert result.status == "unavailable"
    assert result.reason_code == reason
    assert result.questions == ()
    assert datetime.fromisoformat(result.checked_at).utcoffset() == UTC.utcoffset(None)


def test_valid_exact_public_get_and_frozen_result_preserve_job(inspect):
    job = _job(required_skills=["Python"], match_score=75)
    before = job.model_dump()
    start = datetime.now(UTC)
    result, requests = inspect(_payload(), job=job)
    assert result.status == "inspected"
    assert result.reason_code == "public_schema_inspected"
    assert result.source_url == API == str(requests[0].url)
    assert start <= datetime.fromisoformat(result.checked_at) <= datetime.now(UTC)
    assert result.questions == (
        {
            "key": "first_name",
            "label": "First Name",
            "required": True,
            "kind": "input_text",
            "options": [],
        },
    )
    assert job.model_dump() == before
    assert "completeness" in result.message and "unknown" in result.message
    assert "not approval or autonomous submission readiness" in result.message
    with pytest.raises(FrozenInstanceError):
        result.status = "unavailable"


def test_multiple_fields_and_select_options_preserve_public_schema(inspect):
    questions = [
        _question(
            label="Resume",
            fields=[
                {"name": "resume", "type": "input_file"},
                {"name": "resume_text", "type": "textarea", "values": []},
            ],
        ),
        _question(
            label="Work authorization",
            required=False,
            fields=[
                {
                    "name": "question_123",
                    "type": "multi_value_single_select",
                    "values": [
                        {"value": 0, "label": "No"},
                        {"value": "yes", "label": "Yes"},
                    ],
                },
            ],
        ),
    ]
    result, _ = inspect(_payload(questions=questions))
    assert result.status == "inspected"
    assert [q["key"] for q in result.questions] == ["resume", "resume_text", "question_123"]
    assert [q["kind"] for q in result.questions] == [
        "input_file",
        "textarea",
        "multi_value_single_select",
    ]
    assert [q["required"] for q in result.questions] == [True, True, False]
    assert result.questions[-1]["options"] == ["No", "Yes"]
    result.questions[-1]["options"].append("Local copy")
    assert len(questions[-1]["fields"][0]["values"]) == 2


@pytest.mark.parametrize("group", ["location_questions", "compliance", "education_questions"])
def test_additional_exposed_question_groups_are_not_omitted(inspect, group):
    result, _ = inspect(_payload(**{group: [_question("extra", "input_hidden")]}))
    assert result.status == "inspected"
    assert [q["key"] for q in result.questions] == ["first_name", "extra"]
    assert result.questions[-1]["required"] is True


@pytest.mark.parametrize(
    "source",
    [
        POSTING,
        "https://boards.greenhouse.io/example/jobs/12345",
        "https://boards.eu.greenhouse.io/example/jobs/12345",
        "https://boards.greenhouse.io/embed/job_app?for=example&token=12345",
    ],
)
def test_supported_public_greenhouse_entry_urls(inspect, source):
    result, requests = inspect(_payload(), job=_job(source_url=source))
    assert result.status == "inspected"
    assert str(requests[0].url) == API


def test_verified_application_url_can_be_used_without_fetching_discovery_source(inspect):
    result, requests = inspect(
        _payload(),
        job=_job(source_url="https://www.linkedin.com/jobs/view/123", application_url=POSTING),
    )
    assert result.status == "inspected"
    assert str(requests[0].url) == API


@pytest.mark.parametrize("identifier", [12346, "12345", 12345.0, True, False, None, {}, []])
def test_job_id_must_be_matching_actual_integer(inspect, identifier):
    result, _ = inspect(_payload(id=identifier))
    _unavailable(result, "identity_mismatch")


def test_boolean_id_is_rejected_even_when_equal_to_integer_one(inspect):
    result, _ = inspect(_payload(id=True), job=_job(source_url=POSTING.replace("12345", "1")))
    _unavailable(result, "identity_mismatch")


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"title": "Frontend Engineer"}, "identity_mismatch"),
        ({"company_name": "Other Labs"}, "identity_mismatch"),
        ({"title": None}, "invalid_response"),
        ({"title": True}, "invalid_response"),
        ({"company_name": None}, "invalid_response"),
        ({"company_name": {"name": "Example Labs"}}, "invalid_response"),
        ({"company_name": " "}, "invalid_response"),
    ],
)
def test_title_and_company_are_mandatory_and_must_match(inspect, changes, reason):
    result, _ = inspect(_payload(**changes))
    _unavailable(result, reason)


def test_missing_company_is_not_inferred_from_board_token(inspect):
    payload = _payload()
    del payload["company_name"]
    result, _ = inspect(payload)
    _unavailable(result, "invalid_response")


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "source_gated"),
        (403, "source_gated"),
        (429, "source_gated"),
        (999, "source_gated"),
        (404, "closed"),
        (410, "closed"),
        (500, "http_error"),
    ],
)
def test_gated_closed_and_http_errors(inspect, status, reason):
    result, _ = inspect(status=status)
    _unavailable(result, reason)


@pytest.mark.parametrize(
    "content,reason",
    [
        (b"<html>Sign in to view this job</html>", "source_gated"),
        (b"<html>This job has expired</html>", "closed"),
    ],
)
def test_html_placeholders_are_not_forms(inspect, content, reason):
    result, _ = inspect(content=content, headers={"content-type": "text/html"})
    _unavailable(result, reason)


@pytest.mark.parametrize(
    "content,reason",
    [
        ("Please verify you are human", "source_gated"),
        ("This job has been closed", "closed"),
    ],
)
def test_json_cached_questions_do_not_override_gate_or_closure(inspect, content, reason):
    result, _ = inspect(_payload(content=content))
    _unavailable(result, reason)


@pytest.mark.parametrize(
    "url,reason",
    [
        ("http://boards.greenhouse.io/example/jobs/12345", "unsafe_url"),
        ("https://secret:private@boards.greenhouse.io/example/jobs/12345", "unsafe_url"),
        (POSTING + "?api_key=private", "unsafe_url"),
        (POSTING + "#private", "unsafe_url"),
        (POSTING + "?next=https://127.0.0.1/private", "unsafe_url"),
        (POSTING.replace("/example/", "/example%2f/"), "unsafe_url"),
        (POSTING.replace(".io/", ".io:444/"), "unsafe_url"),
        ("https://127.0.0.1/jobs/12345", "unsupported_url"),
        (POSTING.replace(".io/", ".io.attacker.test/"), "unsupported_url"),
        ("https://boards.greenhouse.io/example", "unsupported_url"),
        ("https://boards.greenhouse.io/auth/login", "source_gated"),
        ("https://www.linkedin.com/jobs/view/12345", "unsupported_url"),
        ("https://api.lever.co/v0/postings/example/12345/apply", "unsupported_url"),
        (API, "unsupported_url"),
    ],
)
def test_unsafe_or_non_greenhouse_postings_never_fetch(url, reason):
    result = inspect_greenhouse_form(
        _job().model_copy(update={"source_url": url}),
        fetcher=lambda _: pytest.fail("Unsupported URL reached HTTP"),
    )
    _unavailable(result, reason)
    assert result.source_url is None
    assert "private" not in result.message


def test_replacement_official_destination_is_checked_not_obsolete_discovery(inspect):
    result, requests = inspect(
        _payload(id=12346), job=_job(application_url=POSTING.replace("12345", "12346")))
    assert result.status == "inspected"
    assert "/jobs/12346?" in str(requests[0].url)


def test_invalid_official_destination_never_falls_back_to_discovery_source():
    result = inspect_greenhouse_form(
        _job(application_url="https://127.0.0.1/private"),
        fetcher=lambda _: pytest.fail("Unsafe official destination reached HTTP"),
    )
    _unavailable(result, "unsupported_url")


@pytest.mark.parametrize(
    "destination,reason",
    [
        (API, "redirect_blocked"),
        (API.replace("12345", "12346"), "redirect_blocked"),
        ("https://127.0.0.1/private", "unsupported_url"),
        ("https://boards.greenhouse.io/login", "source_gated"),
        ("http://boards.greenhouse.io/example/jobs/12345", "unsafe_url"),
    ],
)
def test_redirects_are_never_followed(inspect, destination, reason):
    result, requests = inspect(status=302, headers={"location": destination})
    _unavailable(result, reason)
    assert len(requests) == 1
    assert result.source_url == API


def test_auto_followed_responses_are_rejected_and_closed():
    response = httpx.Response(
        200, json=_payload(), history=[httpx.Response(302, request=httpx.Request("GET", API))]
    )
    result = inspect_greenhouse_form(_job(), fetcher=lambda _: response)
    _unavailable(result, "redirect_blocked")
    assert response.is_closed


@pytest.mark.parametrize("method,url", [("POST", API), ("GET", API.replace("12345", "12346"))])
def test_injected_responses_must_come_from_exact_get(method, url):
    response = httpx.Response(200, json=_payload(), request=httpx.Request(method, url))
    result = inspect_greenhouse_form(_job(), fetcher=lambda _: response)
    _unavailable(result, "redirect_blocked")
    assert response.is_closed


@pytest.mark.parametrize("value", [None, {}, "questions", 1, True])
def test_questions_must_be_a_list(inspect, value):
    result, _ = inspect(_payload(questions=value))
    _unavailable(result, "unknown_schema")


def test_missing_questions_is_not_an_empty_success(inspect):
    payload = _payload()
    del payload["questions"]
    result, _ = inspect(payload)
    _unavailable(result, "unknown_schema")


def test_explicit_empty_public_questions_is_observed_without_completeness_claim(inspect):
    result, _ = inspect(
        _payload(questions=[], data_compliance=[], demographic_questions={"questions": []})
    )
    assert result.status == "inspected"
    assert result.questions == ()
    assert "unknown" in result.message


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_required_is_not_coerced_from_truthiness(inspect, value):
    result, _ = inspect(_payload(questions=[_question(required=value)]))
    _unavailable(result, "unknown_schema")


def test_missing_required_is_unknown_not_silently_optional(inspect):
    question = _question()
    del question["required"]
    result, _ = inspect(_payload(questions=[question]))
    _unavailable(result, "unknown_schema")


@pytest.mark.parametrize(
    "question",
    [
        None,
        [],
        "field",
        {},
        _question(label=None),
        _question(label=" "),
        _question(label=1),
        _question(fields=None),
        _question(fields={}),
        _question(fields=[]),
        _question(fields=[None]),
        _question(fields=[{"name": "first_name"}]),
        _question(fields=[{"name": "first_name", "type": False}]),
        _question(fields=[{"name": 1, "type": "input_text"}]),
        _question(fields=[{"name": "first_name", "type": "unrecognized_widget"}]),
        _question(fields=[{"name": "choice", "type": "multi_value_single_select"}]),
        _question(fields=[{"name": "first_name", "type": "input_text", "values": None}]),
        _question(fields=[{"name": "first_name", "type": "input_text", "required": "true"}]),
        _question(depends_on="another_field"),
    ],
)
def test_malformed_or_unsupported_question_schema_is_unavailable(inspect, question):
    result, _ = inspect(_payload(questions=[question]))
    _unavailable(result, "unknown_schema")


@pytest.mark.parametrize(
    "values",
    [
        None,
        {},
        "Yes",
        [None],
        ["Yes"],
        [{"label": "Yes"}],
        [{"value": 1}],
        [{"value": True, "label": "Yes"}],
        [{"value": None, "label": "Yes"}],
        [{"value": 1.0, "label": "Yes"}],
        [{"value": [], "label": "Yes"}],
        [{"value": 1, "label": False}],
        [{"value": "x" * (forms.MAX_VALUE_CHARS + 1), "label": "Yes"}],
        [{"value": 1, "label": "x" * (forms.MAX_LABEL_CHARS + 1)}],
        [{"value": 1, "label": "Yes", "free_form": True}],
    ],
)
def test_options_are_bounded_and_faithfully_typed(inspect, values):
    question = _question(
        fields=[{"name": "choice", "type": "multi_value_multi_select", "values": values}]
    )
    result, _ = inspect(_payload(questions=[question]))
    _unavailable(result, "unknown_schema")


@pytest.mark.parametrize(
    "updates",
    [
        {"location_questions": None},
        {"compliance": {}},
        {"education_questions": "required"},
        {"education": {"required": True}},
        {"educations": []},
        {"employment_questions": []},
        {"other_questions": []},
        {"data_compliance": None},
        {"data_compliance": [{"type": "gdpr", "requires_consent": True}]},
        {"demographic_questions": {"questions": [{"id": 1, "required": True}]}},
        {"demographic_questions": None},
    ],
)
def test_additional_unrepresentable_groups_never_claim_complete_schema(inspect, updates):
    result, _ = inspect(_payload(**updates))
    _unavailable(result, "unknown_schema")


def test_duplicate_field_keys_are_not_collapsed_or_overwritten(inspect):
    result, _ = inspect(_payload(questions=[_question(), _question()]))
    _unavailable(result, "unknown_schema")


@pytest.mark.parametrize("count", [forms.MAX_QUESTIONS, forms.MAX_QUESTIONS + 1])
def test_total_question_and_field_count_are_bounded(inspect, count):
    result, _ = inspect(_payload(questions=[_question(f"field_{n}") for n in range(count)]))
    if count == forms.MAX_QUESTIONS:
        assert result.status == "inspected"
        assert len(result.questions) == forms.MAX_QUESTIONS
    else:
        _unavailable(result, "unknown_schema")


def test_field_count_budget_is_shared_across_groups_and_multifield_questions(inspect):
    fields = [{"name": f"field_{n}", "type": "input_text"} for n in range(forms.MAX_QUESTIONS)]
    result, _ = inspect(
        _payload(questions=[_question(fields=fields)], location_questions=[_question("location")])
    )
    _unavailable(result, "unknown_schema")


@pytest.mark.parametrize(
    "question",
    [
        _question(label="x" * (forms.MAX_LABEL_CHARS + 1)),
        _question(name="x" * (forms.MAX_KEY_CHARS + 1)),
        _question(label="A\x00B"),
        _question(
            fields=[
                {
                    "name": "choice",
                    "type": "multi_value_multi_select",
                    "values": [{"value": n, "label": str(n)} for n in range(forms.MAX_OPTIONS + 1)],
                }
            ]
        ),
    ],
)
def test_labels_keys_and_option_counts_are_bounded_without_truncation(inspect, question):
    result, _ = inspect(_payload(questions=[question]))
    _unavailable(result, "unknown_schema")


@pytest.mark.parametrize("body", [b"not json", b"[]", b"null", b"true", b"\xff"])
def test_malformed_json_or_non_object_payload(inspect, body):
    result, _ = inspect(content=body, headers={"content-type": "application/json"})
    _unavailable(result, "invalid_response")


@pytest.mark.parametrize("compressed", [False, True])
def test_shared_reader_bounds_decoded_response_size(inspect, compressed):
    body = b"x" * (enrichment.MAX_BODY_BYTES + 1)
    headers = {"content-type": "application/json"}
    if compressed:
        body = gzip.compress(body)
        headers["content-encoding"] = "gzip"
    result, _ = inspect(content=body, headers=headers)
    _unavailable(result, "response_too_large")


@pytest.mark.parametrize(
    "error,reason",
    [
        (httpx.ReadTimeout("private credentials"), "fetch_timeout"),
        (httpx.ConnectError("private credentials"), "fetch_failed"),
        (OSError("private credentials"), "fetch_failed"),
    ],
)
def test_transport_failures_do_not_leak_exception_text(error, reason):
    def fail(_):
        raise error

    result = inspect_greenhouse_form(_job(), fetcher=fail)
    _unavailable(result, reason)
    assert "private" not in result.message


def test_shared_reader_total_deadline_is_enforced(monkeypatch, fetcher_factory):
    clock = [0.0]
    monkeypatch.setattr(enrichment.time, "monotonic", lambda: clock[0])

    def handle(request):
        clock[0] = enrichment.TOTAL_SECONDS + 1
        return httpx.Response(200, json=_payload())

    result = inspect_greenhouse_form(_job(), fetcher=fetcher_factory(handle))
    _unavailable(result, "fetch_timeout")


def test_default_transport_is_streaming_get_without_auth_redirects_or_environment(monkeypatch):
    real_client = httpx.Client
    options, requests, sends = {}, [], []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=_payload(), headers={"set-cookie": "session=unused"})

    class MockClient(real_client):
        def send(self, request, **kwargs):
            sends.append(kwargs)
            return super().send(request, **kwargs)

    def factory(**kwargs):
        options.update(kwargs)
        return MockClient(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(forms.httpx, "Client", factory)
    result = inspect_greenhouse_form(_job())
    assert result.status == "inspected"
    assert options["trust_env"] is False
    assert options["follow_redirects"] is False
    assert options["timeout"] == enrichment.TIMEOUT_SECONDS
    assert "auth" not in options and "cookies" not in options
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == API
    assert request.method == "GET" and not request.content
    assert "authorization" not in request.headers and "cookie" not in request.headers
    assert sends == [{"stream": True, "follow_redirects": False}]
