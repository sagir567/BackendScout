import pytest

from backend_scout.cv_tailoring import (
    build_evidence_only_draft,
    openai_json_schema,
    validate_tailored_cv_against_evidence,
)
from backend_scout.models import (
    CareerEvidence,
    CvStyle,
    TailoredBullet,
    TailoredCv,
    TailoredExperience,
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


def test_evidence_only_draft_is_valid_and_cited() -> None:
    evidence = make_evidence()

    draft = build_evidence_only_draft(evidence)

    validate_tailored_cv_against_evidence(draft, evidence)
    assert draft.experience[0].bullets[0].evidence_ids == ["exp_1_bullet_1"]


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
