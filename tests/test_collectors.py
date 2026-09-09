from backend_scout.collectors import (
    collect_public_jobs,
    is_israel_or_remote,
    parse_ashby_jobs,
    parse_greenhouse_jobs,
    parse_lever_jobs,
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
