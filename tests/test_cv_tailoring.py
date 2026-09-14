import pytest

from backend_scout.cv_tailoring import (
    build_evidence_only_draft,
    evidence_for_job,
    openai_json_schema,
    remove_unsupported_skills,
    validate_tailored_cv_against_evidence,
)
from backend_scout.models import (
    CareerEvidence,
    CvStyle,
    Job,
    TailoredBullet,
    TailoredCv,
    TailoredExperience,
    TailoredProject,
)


def make_evidence() -> CareerEvidence:
    return CareerEvidence.model_validate(
        {
            "identity": {"full_name": "Test Candidate", "email": "candidate@example.com"},
            "summary": "Backend developer with Python experience.",
            "skills": [{"category": "Backend", "items": ["Python", "FastAPI"]}],
            "experience": [
                {
                    "id": "exp_1",
                    "organization": "Example Company",
                    "title": "Backend Developer",
                    "start_date": "2024-01",
                    "end_date": "Present",
                    "bullets": [
                        {
                            "id": "exp_1_bullet_1",
                            "text": "Built Python APIs for internal tools.",
                        }
                    ],
                }
            ],
            "projects": [],
            "education": [],
        }
    )


def make_project_evidence() -> CareerEvidence:
    data = make_evidence().model_dump(mode="json")
    data["projects"] = [
        {
            "id": "project_cpp",
            "name": "C++ Practice",
            "link": "https://github.com/test-candidate/cpp",
            "selection_keywords": ["C++", "cpp"],
            "bullets": [
                {
                    "id": "project_cpp_bullet",
                    "text": "Built coursework projects in C++.",
                }
            ],
        },
        {
            "id": "project_agents",
            "name": "Agent Automation",
            "selection_keywords": ["AI agent", "agentic", "OpenAI API"],
            "bullets": [
                {
                    "id": "project_agents_bullet",
                    "text": "Built an AI agent automation workflow.",
                }
            ],
        },
    ]
    return CareerEvidence.model_validate(data)


def make_job(title: str, description: str, required_skills: list[str]) -> Job:
    return Job(
        source="test",
        source_url="https://example.test/job",
        company="Example",
        title=title,
        description=description,
        required_skills=required_skills,
    )


def test_evidence_only_draft_is_valid_and_cited() -> None:
    evidence = make_evidence()

    draft = build_evidence_only_draft(evidence)

    validate_tailored_cv_against_evidence(draft, evidence)
    assert draft.experience[0].bullets[0].evidence_ids == ["exp_1_bullet_1"]


def test_python_agent_job_uses_agent_project_and_excludes_cpp_repository() -> None:
    evidence = make_project_evidence()
    job = make_job(
        "Python AI Agent Engineer",
        "Build agentic backend workflows with Python and the OpenAI API.",
        ["Python", "OpenAI API"],
    )

    selected = evidence_for_job(evidence, job)

    assert [project.id for project in selected.projects] == ["project_agents"]


def test_cpp_job_can_select_cpp_repository_project() -> None:
    evidence = make_project_evidence()
    job = make_job(
        "C++ Software Engineer",
        "Develop and debug modern C++ systems.",
        ["C++"],
    )

    selected = evidence_for_job(evidence, job)

    assert [project.id for project in selected.projects] == ["project_cpp"]


def test_validator_rejects_cpp_project_for_python_agent_job() -> None:
    evidence = make_project_evidence()
    job = make_job(
        "Python AI Agent Engineer",
        "Build agentic backend workflows with Python.",
        ["Python"],
    )
    draft = TailoredCv(
        projects=[
            TailoredProject(
                evidence_id="project_cpp",
                bullets=[
                    TailoredBullet(
                        text="Built coursework projects in C++.",
                        evidence_ids=["project_cpp_bullet"],
                    )
                ],
            )
        ]
    )

    with pytest.raises(ValueError, match="unrelated to the target job"):
        validate_tailored_cv_against_evidence(draft, evidence, job=job)


def test_tailored_draft_rejects_unknown_evidence_citation() -> None:
    evidence = make_evidence()
    draft = TailoredCv(
        summary="Unsupported summary.",
        summary_evidence_ids=["missing"],
        experience=[
            TailoredExperience(
                evidence_id="exp_1",
                bullets=[TailoredBullet(text="Claim", evidence_ids=["exp_1_bullet_1"])],
            )
        ],
    )

    with pytest.raises(ValueError, match="unknown evidence"):
        validate_tailored_cv_against_evidence(draft, evidence)


def test_tailored_draft_rejects_skill_not_in_evidence() -> None:
    evidence = make_evidence()
    draft = build_evidence_only_draft(evidence)
    draft.skills[0].items.append("Kubernetes")

    with pytest.raises(ValueError, match="skill not present"):
        validate_tailored_cv_against_evidence(draft, evidence)


def test_unsupported_model_skill_is_removed_without_changing_verified_skills() -> None:
    evidence = make_evidence()
    draft = build_evidence_only_draft(evidence)
    draft.skills[0].items.append("Kubernetes")

    filtered = remove_unsupported_skills(draft, evidence)

    assert filtered.skills[0].items == ["Python", "FastAPI"]
    validate_tailored_cv_against_evidence(filtered, evidence)


def test_openai_schema_requires_every_nested_property() -> None:
    schema = openai_json_schema(TailoredCv)

    assert set(schema["required"]) == set(schema["properties"])
    skill_group_schema = schema["$defs"]["SkillGroup"]
    assert set(skill_group_schema["required"]) == set(skill_group_schema["properties"])
    education_schema = schema["$defs"]["EducationEvidence"]
    assert set(education_schema["required"]) == set(education_schema["properties"])
    experience_schema = schema["$defs"]["TailoredExperience"]
    assert set(experience_schema["required"]) == set(experience_schema["properties"])


def test_tailored_draft_rejects_visible_raw_url() -> None:
    evidence = make_evidence()
    draft = TailoredCv(
        summary="See https://example.com for more details.",
        summary_evidence_ids=["exp_1_bullet_1"],
    )

    with pytest.raises(ValueError, match="raw URL"):
        validate_tailored_cv_against_evidence(draft, evidence, CvStyle())


def test_tailored_draft_rejects_headline_when_style_hides_it() -> None:
    evidence = make_evidence()
    draft = TailoredCv(headline="Backend Engineer")

    with pytest.raises(ValueError, match="headline"):
        validate_tailored_cv_against_evidence(draft, evidence, CvStyle())
