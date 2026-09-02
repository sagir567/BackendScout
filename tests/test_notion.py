import pytest

from backend_scout.models import ApplicationStatus, Job
from backend_scout.notion import (
    APPLICATIONS_PROPERTY_NAMES,
    APPLICATIONS_PROPERTY_TYPES,
    build_job_page_properties,
    missing_submission_property_definitions,
    upsert_job_page,
    validate_applications_data_source,
)


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


def test_validate_applications_data_source_accepts_expected_schema() -> None:
    data_source = {
        "properties": {
            name: {"type": APPLICATIONS_PROPERTY_TYPES[key]}
            for key, name in APPLICATIONS_PROPERTY_NAMES.items()
        }
    }

    assert validate_applications_data_source(data_source) == []


def test_validate_applications_data_source_reports_missing_or_wrong_properties() -> None:
    data_source = {
        "properties": {
            "Role": {"type": "title"},
            "Company": {"type": "number"},
        }
    }

    problems = validate_applications_data_source(data_source)

    assert "Property Company should be rich_text, got number" in problems
    assert "Missing property: Status (status)" in problems


def test_submission_field_definitions_are_additive_and_refuse_wrong_existing_types() -> None:
    definitions = missing_submission_property_definitions({"properties": {}})

    assert definitions["Submission Channel"]["select"]["options"][0]["name"] == "email"
    assert definitions["Contact Source"] == {"rich_text": {}}

    with pytest.raises(ValueError, match="Submission Channel"):
        missing_submission_property_definitions(
            {"properties": {"Submission Channel": {"type": "rich_text"}}}
        )


def test_build_job_page_properties_can_preserve_existing_status_on_update() -> None:
    job = Job(
        source="manual",
        source_url="https://example.com/jobs/backend",
        company="Example",
        title="Backend Engineer",
        description="Build APIs and workers.",
    )

    properties = build_job_page_properties(job, include_status=False)

    assert APPLICATIONS_PROPERTY_NAMES["status"] not in properties
    assert APPLICATIONS_PROPERTY_NAMES["discovered_at"] not in properties
    assert properties[APPLICATIONS_PROPERTY_NAMES["role"]]["title"][0]["text"]["content"] == job.title


def test_upsert_job_page_creates_when_match_is_missing() -> None:
    created_jobs: list[tuple[str, Job, ApplicationStatus]] = []

    class FakeNotionClient:
        def find_job_page(self, data_source_id: str, job: Job) -> None:
            return None

        def create_job_page(
            self, data_source_id: str, job: Job, status: ApplicationStatus = ApplicationStatus.FOUND
        ) -> dict[str, str]:
            created_jobs.append((data_source_id, job, status))
            return {"id": "page_new"}

    job = Job(
        source="manual",
        source_url="https://example.com/jobs/backend",
        company="Example",
        title="Backend Engineer",
        description="Build APIs and workers.",
    )

    action, payload = upsert_job_page(FakeNotionClient(), "data-source-123", job)

    assert action == "created"
    assert payload["id"] == "page_new"
    assert created_jobs[0][2] == ApplicationStatus.FOUND


def test_upsert_job_page_updates_when_existing_page_is_found() -> None:
    updated_pages: list[tuple[str, Job]] = []

    class FakeNotionClient:
        def find_job_page(self, data_source_id: str, job: Job) -> dict[str, str]:
            return {"id": "page_existing"}

        def update_job_page(self, page_id: str, job: Job) -> dict[str, str]:
            updated_pages.append((page_id, job))
            return {"id": page_id}

    job = Job(
        source="manual",
        source_url="https://example.com/jobs/backend",
        company="Example",
        title="Backend Engineer",
        description="Build APIs and workers.",
    )

    action, payload = upsert_job_page(FakeNotionClient(), "data-source-123", job)

    assert action == "updated"
    assert payload["id"] == "page_existing"
    assert updated_pages[0][0] == "page_existing"
