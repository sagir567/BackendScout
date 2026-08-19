from backend_scout.models import ApplicationStatus, Job
from backend_scout.notion import APPLICATIONS_PROPERTY_NAMES, build_job_page_properties


def test_build_job_page_properties_uses_data_source_schema_names() -> None:
    job = Job(
        source="manual",
        source_url="https://example.com/jobs/backend",
        company="Example",
        title="Backend Engineer",
        description="Build APIs and workers.",
        required_skills=["Python", "PostgreSQL"],
        match_score=88,
    )

    properties = build_job_page_properties(job, ApplicationStatus.DIGEST_SENT)

    assert properties[APPLICATIONS_PROPERTY_NAMES["role"]]["title"][0]["text"]["content"] == job.title
    assert properties[APPLICATIONS_PROPERTY_NAMES["status"]]["status"]["name"] == "digest_sent"
    assert properties[APPLICATIONS_PROPERTY_NAMES["source_url"]]["url"] == job.source_url
    assert properties[APPLICATIONS_PROPERTY_NAMES["match_score"]]["number"] == 88
    assert properties[APPLICATIONS_PROPERTY_NAMES["required_skills"]]["multi_select"] == [
        {"name": "Python"},
        {"name": "PostgreSQL"},
    ]
