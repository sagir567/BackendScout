from pathlib import Path

import pytest
from pydantic import ValidationError

from backend_scout.job_imports import load_manual_job_import
from backend_scout.models import ManualJobImport


def test_load_manual_job_import_reads_example_file() -> None:
    manual_import = load_manual_job_import(Path("examples/manual_job.example.yaml"))

    assert len(manual_import.jobs) == 1
    assert manual_import.jobs[0].company == "Example Cloud"
    assert manual_import.jobs[0].required_skills == [
        "Python",
        "FastAPI",
        "PostgreSQL",
        "Docker",
    ]


def test_manual_job_import_rejects_empty_job_list() -> None:
    with pytest.raises(ValidationError, match="at least 1 item"):
        ManualJobImport.model_validate({"jobs": []})


def test_manual_job_import_rejects_blank_required_job_fields() -> None:
    with pytest.raises(ValidationError, match="must not be blank"):
        ManualJobImport.model_validate(
            {
                "jobs": [
                    {
                        "source": "manual",
                        "source_url": "https://example.com/jobs/backend",
                        "company": " ",
                        "title": "Backend Engineer",
                        "description": "Build APIs.",
                    }
                ]
            }
        )
