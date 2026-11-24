from backend_scout.front2job import (
    FRONT2JOB_EMPLOYERS_URL,
    Front2JobEmployer,
    ats_targets_from_page,
    front2job_bundle_urls,
    parse_front2job_bundle,
    parse_front2job_employers,
)
from backend_scout.targets import AtsProvider


def test_directory_parser_keeps_only_external_employer_links() -> None:
    html = """
    <a href="https://front2job.com/about">About</a>
    <a href="https://job-boards.greenhouse.io/gongio?office=1">Gong</a>
    <a href="https://www.linkedin.com/company/front2job">LinkedIn</a>
    <a href="mailto:owner@example.com">Contact</a>
    """
    assert parse_front2job_employers(html) == [
        Front2JobEmployer(
            name="Gong",
            careers_url="https://job-boards.greenhouse.io/gongio?office=1",
        )
    ]


def test_supported_ats_is_discovered_from_official_careers_page() -> None:
    employer = Front2JobEmployer("Example", "https://example.com/careers")
    targets = ats_targets_from_page(
        employer,
        '<a href="https://jobs.lever.co/example/jobs/123">Open roles</a>',
    )
    assert [(item.provider, item.board_token) for item in targets] == [
        (AtsProvider.LEVER, "example")
    ]
    assert targets[0].notes == f"Discovered from {FRONT2JOB_EMPLOYERS_URL}"


def test_unknown_career_page_does_not_guess_an_ats_identifier() -> None:
    employer = Front2JobEmployer("Example", "https://example.com/careers")
    assert ats_targets_from_page(employer, "<h1>Careers</h1>") == []


def test_dynamic_directory_bundle_is_parsed_without_executing_javascript() -> None:
    html = '<script type="module" src="/assets/index-abc.js"></script>'
    bundle = (
        'x=[{name:"Gong",logo:Ab,url:"https://job-boards.greenhouse.io/gongio?office=1"},'
        '{name:"רפאל",logo:Bb,url:"https://career.rafael.co.il/search/"}]'
    )
    assert front2job_bundle_urls(html) == ["https://front2job.com/assets/index-abc.js"]
    assert parse_front2job_bundle(bundle) == [
        Front2JobEmployer("Gong", "https://job-boards.greenhouse.io/gongio?office=1"),
        Front2JobEmployer("רפאל", "https://career.rafael.co.il/search/"),
    ]
