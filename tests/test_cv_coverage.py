from backend_scout.cv_coverage import CV_EVIDENCE_COVERAGE_TARGET, assess_cv_evidence_coverage
from backend_scout.models import CareerEvidence, Job


def _evidence() -> CareerEvidence:
    return CareerEvidence.model_validate(
        {
            "identity": {"full_name": "Test Candidate", "email": "candidate@example.com"},
            "skills": [
                {
                    "category": "Engineering",
                    "items": [
                        "Python",
                        "Modular architecture",
                        "CI/CD",
                        "Docker",
                        "Azure Web Apps",
                        "OpenAI API",
                    ],
                }
            ],
            "experience": [
                {
                    "id": "exp_1",
                    "organization": "Example",
                    "title": "Software Developer",
                    "start_date": "2024",
                    "end_date": "Present",
                    "bullets": [
                        {
                            "id": "exp_1_bullet_1",
                            "text": "Designed system architecture and deployed Docker services to Azure.",
                        }
                    ],
                }
            ],
        }
    )


def _job(required_skills: list[str]) -> Job:
    return Job(
        source="manual",
        source_url="https://example.com/jobs/backend",
        company="Example",
        title="Backend Engineer",
        description="Build backend systems.",
        required_skills=required_skills,
    )


def test_evidence_coverage_uses_supported_aliases() -> None:
    result = assess_cv_evidence_coverage(
        _evidence(),
        _job(
            [
                "Software development",
                "Software design",
                "System design",
                "CI/CD",
                "Containers",
                "Cloud infrastructure",
                "AI tools",
            ]
        ),
    )

    assert result.coverage_score == 100
    assert result.target_score == CV_EVIDENCE_COVERAGE_TARGET
    assert result.missing_requirements == []
    assert result.clarification_questions == []
    assert "exp_1_bullet_1" in result.supporting_evidence_ids


def test_evidence_coverage_can_reach_the_target_with_a_remaining_disclosed_gap() -> None:
    result = assess_cv_evidence_coverage(
        _evidence(),
        _job(
            [
                "Software development",
                "Software design",
                "System design",
                "CI/CD",
                "Containers",
                "Cloud infrastructure",
                "AI tools",
                "Python",
                "Docker",
                "Kubernetes",
            ]
        ),
    )

    assert result.coverage_score == CV_EVIDENCE_COVERAGE_TARGET
    assert result.missing_requirements == ["Kubernetes"]
    assert result.clarification_questions


def test_evidence_coverage_reports_truthful_gaps_as_questions() -> None:
    result = assess_cv_evidence_coverage(
        _evidence(),
        _job(["Python", "Distributed systems", "Kubernetes", "English"]),
    )

    assert result.coverage_score == 25
    assert result.covered_requirements == ["Python"]
    assert result.missing_requirements == ["Distributed systems", "Kubernetes", "English"]
    assert len(result.clarification_questions) == 3
    assert "Kubernetes" in result.clarification_questions[1]


def test_evidence_coverage_uses_education_and_requirement_aliases() -> None:
    evidence_data = _evidence().model_dump()
    evidence_data["experience"][0]["bullets"].append(
        {
            "id": "exp_1_bullet_2",
            "text": "Practiced algorithmic thinking and problem solving while debugging backend services.",
        }
    )
    evidence_data["education"] = [
        {
            "institution": "Ariel University",
            "credential": "B.Sc. in Computer Science and Mathematics",
            "end_date": "2024",
        }
    ]
    evidence = CareerEvidence.model_validate(evidence_data)

    result = assess_cv_evidence_coverage(
        evidence,
        _job(["B.Sc. Computer Science or equivalent", "Problem solving"]),
    )

    assert result.coverage_score == 100
    assert result.missing_requirements == []


def test_evidence_coverage_uses_linux_and_multithreading_aliases() -> None:
    evidence_data = _evidence().model_dump()
    evidence_data["skills"][0]["items"].extend(["Linux", "Multithreading"])
    evidence_data["experience"][0]["bullets"].extend(
        [
            {
                "id": "exp_1_bullet_2",
                "text": "Adapted Python code for Linux-based HPC execution.",
            },
            {
                "id": "exp_1_bullet_3",
                "text": "Practiced multithreaded programming and debugging in Java, Python, C, and C++.",
            },
        ]
    )
    evidence = CareerEvidence.model_validate(evidence_data)

    result = assess_cv_evidence_coverage(
        evidence,
        _job(["Linux", "Multi-threaded debugging"]),
    )

    assert result.coverage_score == 100
    assert result.missing_requirements == []


def test_evidence_coverage_is_not_penalized_when_job_has_no_explicit_requirements() -> None:
    result = assess_cv_evidence_coverage(_evidence(), _job([]))

    assert result.coverage_score == 100
    assert result.covered_requirements == []
    assert result.missing_requirements == []
