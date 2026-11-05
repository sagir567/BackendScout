import pytest

from backend_scout.collectors import (
    collect_public_jobs,
    is_israel_or_remote,
    parse_ashby_jobs,
    parse_greenhouse_jobs,
    parse_lever_jobs,
    parse_lin_srael_jobs,
)
from backend_scout.models import Job
from backend_scout.targets import AtsProvider, TargetCompany


def _target(provider: AtsProvider) -> TargetCompany:
    return TargetCompany(name="Israel Example", provider=provider, board_token="example")


def test_greenhouse_parser_normalizes_public_job_board_payload() -> None:
    jobs = parse_greenhouse_jobs(
        _target(AtsProvider.GREENHOUSE),
        {
            "jobs": [
                {
                    "title": "Backend Engineer",
                    "absolute_url": "https://jobs.example/backend",
                    "location": {"name": "Tel Aviv, Israel"},
                    "content": "<p>Build <strong>Python</strong> APIs.</p>",
                }
            ]
        },
    )

    assert jobs[0].company == "Israel Example"
    assert jobs[0].description == "Build Python APIs."
    assert jobs[0].required_skills == ["Python", "APIs"]
    assert is_israel_or_remote(jobs[0])


def test_lever_and_ashby_parsers_preserve_public_apply_urls() -> None:
    lever = parse_lever_jobs(
        _target(AtsProvider.LEVER),
        [
            {
                "text": "Backend Engineer",
                "hostedUrl": "https://jobs.lever.co/example/job",
                "categories": {"location": "Israel", "commitment": "Full-time"},
                "descriptionPlain": "Build services.",
            }
        ],
    )
    ashby = parse_ashby_jobs(
        _target(AtsProvider.ASHBY),
        {
            "jobs": [
                {
                    "isListed": True,
                    "title": "Remote Backend Engineer",
                    "jobUrl": "https://jobs.ashbyhq.com/example/job",
                    "location": "Europe",
                    "workplaceType": "Remote",
                    "descriptionPlain": "Build systems.",
                }
            ]
        },
    )

    assert lever[0].source_url.startswith("https://jobs.lever.co")
    assert lever[0].employment_type == "Full-time"
    assert ashby[0].remote_policy == "Remote"
    assert is_israel_or_remote(ashby[0])


def test_public_collectors_extract_skills_from_description_text() -> None:
    jobs = parse_greenhouse_jobs(
        _target(AtsProvider.GREENHOUSE),
        {
            "jobs": [
                {
                    "title": "Junior Backend Software Engineer",
                    "absolute_url": "https://jobs.example/backend",
                    "location": {"name": "Kfar Saba, Israel"},
                    "content": (
                        "<p>Requirements: 2+ years building C++ services on Linux, "
                        "working with Python, Docker, SQL, CI/CD, and multithreaded debugging.</p>"
                    ),
                }
            ]
        },
    )

    assert jobs[0].required_skills == [
        "Python",
        "C++",
        "SQL",
        "Docker",
        "Linux",
        "CI/CD",
        "Multithreading",
    ]
    assert jobs[0].years_experience == "2+ years"


def test_skill_extraction_uses_word_boundaries_for_short_terms() -> None:
    jobs = parse_lever_jobs(
        _target(AtsProvider.LEVER),
        [
            {
                "text": "Application Engineer",
                "hostedUrl": "https://jobs.lever.co/example/job",
                "categories": {"location": "Israel", "commitment": "Full-time"},
                "descriptionPlain": "Own applicant workflows and internal tooling.",
            }
        ],
    )

    assert "APIs" not in jobs[0].required_skills


def test_collector_uses_only_the_configured_public_endpoint() -> None:
    requested: list[str] = []

    jobs = collect_public_jobs(
        _target(AtsProvider.GREENHOUSE),
        lambda url: requested.append(url)
        or {"jobs": []},
    )

    assert jobs == []
    assert requested == ["https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true"]


def test_lin_srael_parser_preserves_employer_and_external_application_url() -> None:
    jobs = parse_lin_srael_jobs(
        _target(AtsProvider.LIN_SRAEL),
        {
            "jobs": [
                {
                    "linkedin_job_id": "123",
                    "published_at": "2026-09-09T08:30:00Z",
                    "title": "Backend Python Engineer",
                    "job_url": "https://www.linkedin.com/jobs/view/123",
                    "company_name": "Example Labs",
                    "location": "Tel Aviv, Israel (Hybrid)",
                    "apply_url": "https://careers.example.com/jobs/123",
                    "description": "Build Python APIs with Docker and PostgreSQL.",
                }
            ]
        },
    )

    assert jobs[0].company == "Example Labs"
    assert jobs[0].source == "lin_srael:Israel Example"
    assert jobs[0].source_url == "https://www.linkedin.com/jobs/view/123"
    assert jobs[0].application_url == "https://careers.example.com/jobs/123"
    assert jobs[0].remote_policy == "Hybrid"
    assert jobs[0].required_skills == ["Python", "PostgreSQL", "Docker", "APIs"]
    assert jobs[0].discovered_at.isoformat() == "2026-09-09T08:30:00+00:00"


def test_lin_srael_collection_searches_terms_and_deduplicates_jobs() -> None:
    requested: list[tuple[str, dict[str, object]]] = []
    target = TargetCompany(
        name="Lin-Srael",
        provider=AtsProvider.LIN_SRAEL,
        board_token="app-id",
        search_terms=["Backend", "Python"],
        result_limit=25,
    )

    def post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
        requested.append((url, payload))
        return {
            "jobs": [
                {
                    "title": "Backend Engineer",
                    "company_name": "Example",
                    "job_url": "https://www.linkedin.com/jobs/view/123",
                    "location": "Israel",
                    "description": "Build backend services.",
                }
            ]
        }

    jobs = collect_public_jobs(target, post_json=post_json)

    assert len(jobs) == 1
    assert requested == [
        (
            "https://lin-srael.com/api/apps/app-id/functions/searchJobs",
            {"title": "Backend", "page": 1, "limit": 25},
        ),
        (
            "https://lin-srael.com/api/apps/app-id/functions/searchJobs",
            {"title": "Python", "page": 1, "limit": 25},
        ),
    ]


def test_israel_filter_rejects_unrelated_onsite_locations() -> None:
    job = Job(
        source="test",
        source_url="https://example.com/job",
        company="Example",
        title="Backend Engineer",
        location="New York, United States",
        remote_policy="On-site",
        description="Build services.",
    )

    assert not is_israel_or_remote(job)


def test_israel_filter_accepts_hebrew_indeed_locations() -> None:
    job = Job(
        source="indeed_email",
        source_url="https://il.indeed.com/viewjob?jk=abc123",
        company="Example",
        title="Backend Engineer",
        location="רעננה, מחוז המרכז",
        description="Build services.",
    )

    assert is_israel_or_remote(job)


def _lin_job(number: int) -> dict[str, str]:
    return {"title": "Backend Engineer", "company_name": "Example",
            "job_url": f"https://www.linkedin.com/jobs/view/{number}", "location": "Israel"}


def test_lin_collection_reads_every_declared_page_and_deduplicates_overlap():
    target = _target(AtsProvider.LIN_SRAEL)
    pages = []

    def post(_url, payload):
        pages.append(payload["page"])
        return {"pages": 3, "jobs": [_lin_job(payload["page"]), _lin_job(99)]}

    jobs = collect_public_jobs(target, post_json=post)
    assert pages == [1, 2, 3]
    assert len(jobs) == 4


def test_lin_pagination_without_metadata_reads_until_short_page():
    target = _target(AtsProvider.LIN_SRAEL).model_copy(update={"result_limit": 1})
    pages = []

    def post(_url, payload):
        pages.append(payload["page"])
        return {"jobs": [_lin_job(payload["page"])] if payload["page"] < 3 else []}

    assert len(collect_public_jobs(target, post_json=post)) == 2
    assert pages == [1, 2, 3]


def test_lin_pagination_rejects_an_endpoint_that_repeats_pages():
    with pytest.raises(ValueError, match="did not advance"):
        collect_public_jobs(_target(AtsProvider.LIN_SRAEL),
                            post_json=lambda *_: {"pages": 2, "jobs": [_lin_job(1)]})


def test_lin_page_limit_is_reported_as_incomplete_not_silent_success():
    target = _target(AtsProvider.LIN_SRAEL).model_copy(update={"max_pages": 1})
    with pytest.raises(ValueError, match="collection is incomplete"):
        collect_public_jobs(target, post_json=lambda *_: {"pages": 2, "jobs": [_lin_job(1)]})


def test_lin_empty_results_do_not_request_extra_pages():
    calls = []
    jobs = collect_public_jobs(_target(AtsProvider.LIN_SRAEL),
                              post_json=lambda *args: calls.append(args) or {"pages": 0, "jobs": []})
    assert jobs == []
    assert len(calls) == 1
