from pathlib import Path

import pytest

from backend_scout.application_answers import ApplicationFormAnswers
from backend_scout.config import TrackerName
from backend_scout.guided_submission import (
    GuidedControlKind,
    GuidedFieldMapping,
    GuidedFormField,
    GuidedFormPlan,
    GuidedFormSnapshot,
    build_guided_candidate_facts,
    guided_planner_payload,
    validate_guided_form_plan,
)
from backend_scout.models import CareerEvidence


def _evidence() -> CareerEvidence:
    return CareerEvidence.model_validate(
        {
            "identity": {
                "full_name": "Test Candidate",
                "email": "candidate@example.com",
                "phone": "+972500000000",
                "location": "Tel Aviv, Israel",
                "links": ["https://linkedin.com/in/test-candidate"],
            },
            "skills": [{"category": "Backend", "items": ["Python"]}],
        }
    )


def _plan(field_id: str, fact_key: str) -> GuidedFormPlan:
    return GuidedFormPlan(
        mappings=[
            GuidedFieldMapping(
                field_id=field_id,
                fact_key=fact_key,
                reason="Exact semantic match",
            )
        ],
        summary="Mapped one approved fact.",
    )


def test_guided_facts_keep_values_local_and_descriptors_value_free(tmp_path: Path) -> None:
    cv_path = tmp_path / "approved.pdf"
    facts = build_guided_candidate_facts(_evidence(), cv_path, None)

    assert facts["identity.email"].value == "candidate@example.com"
    assert facts["approved.cv_pdf"].value == str(cv_path)
    descriptors = [fact.descriptor.model_dump() for fact in facts.values()]
    assert all("value" not in descriptor for descriptor in descriptors)
    assert "candidate@example.com" not in str(descriptors)

    snapshot = GuidedFormSnapshot(
        page_url="https://careers.example.test/form?candidate=private",
        fields=[
            GuidedFormField(
                field_id="field-1",
                label="Contact inbox",
                kind=GuidedControlKind.EMAIL,
            )
        ],
    )
    payload = guided_planner_payload(snapshot, facts)
    assert "candidate@example.com" not in str(payload)
    assert "candidate=private" not in str(payload)


def test_saved_form_answers_become_candidate_confirmed_facts(tmp_path: Path) -> None:
    answers = ApplicationFormAnswers(
        notion_page_id="page-1",
        tracker=TrackerName.PRODUCTION,
        field_values={"sponsorship": "Yes"},
        checkbox_values={"experience": "1-2 years"},
    )

    facts = build_guided_candidate_facts(_evidence(), tmp_path / "approved.pdf", answers)

    assert facts["confirmed.field:sponsorship"].descriptor.candidate_confirmed
    assert facts["confirmed.choice:experience:0"].descriptor.candidate_confirmed


def test_guided_plan_accepts_exact_identity_mapping(tmp_path: Path) -> None:
    field = GuidedFormField(
        field_id="field-1",
        label="Contact inbox",
        kind=GuidedControlKind.EMAIL,
        required=True,
    )
    snapshot = GuidedFormSnapshot(page_url="https://careers.example.test", fields=[field])
    facts = build_guided_candidate_facts(_evidence(), tmp_path / "approved.pdf", None)

    validate_guided_form_plan(_plan(field.field_id, "identity.email"), snapshot, facts)


@pytest.mark.parametrize(
    ("field", "fact_key", "message"),
    [
        (
            GuidedFormField(
                field_id="captcha",
                label="Verify you are human",
                kind=GuidedControlKind.TEXT,
            ),
            "identity.email",
            "human-verification",
        ),
        (
            GuidedFormField(
                field_id="submit",
                label="Submit application",
                kind=GuidedControlKind.TEXT,
            ),
            "identity.email",
            "submission controls",
        ),
        (
            GuidedFormField(
                field_id="resume",
                label="Resume",
                kind=GuidedControlKind.FILE,
            ),
            "identity.email",
            "approved CV fact",
        ),
    ],
)
def test_guided_plan_rejects_disallowed_control_mappings(
    tmp_path: Path,
    field: GuidedFormField,
    fact_key: str,
    message: str,
) -> None:
    snapshot = GuidedFormSnapshot(page_url="https://careers.example.test", fields=[field])
    facts = build_guided_candidate_facts(_evidence(), tmp_path / "approved.pdf", None)

    with pytest.raises(ValueError, match=message):
        validate_guided_form_plan(_plan(field.field_id, fact_key), snapshot, facts)


def test_guided_plan_rejects_unknown_fact_and_populated_field(tmp_path: Path) -> None:
    field = GuidedFormField(
        field_id="field-1",
        label="Email",
        kind=GuidedControlKind.EMAIL,
        current_value_present=True,
    )
    snapshot = GuidedFormSnapshot(page_url="https://careers.example.test", fields=[field])
    facts = build_guided_candidate_facts(_evidence(), tmp_path / "approved.pdf", None)

    with pytest.raises(ValueError, match="unknown fact"):
        validate_guided_form_plan(_plan(field.field_id, "invented.answer"), snapshot, facts)
    with pytest.raises(ValueError, match="overwrite"):
        validate_guided_form_plan(_plan(field.field_id, "identity.email"), snapshot, facts)


def test_guided_plan_rejects_semantically_wrong_identity_mapping(tmp_path: Path) -> None:
    field = GuidedFormField(
        field_id="years",
        label="Years of software experience",
        kind=GuidedControlKind.TEXT,
    )
    snapshot = GuidedFormSnapshot(page_url="https://careers.example.test", fields=[field])
    facts = build_guided_candidate_facts(_evidence(), tmp_path / "approved.pdf", None)

    with pytest.raises(ValueError, match="does not match"):
        validate_guided_form_plan(_plan(field.field_id, "identity.email"), snapshot, facts)


def test_sensitive_field_requires_candidate_confirmed_answer(tmp_path: Path) -> None:
    field = GuidedFormField(
        field_id="sponsorship",
        label="Will you require visa sponsorship?",
        kind=GuidedControlKind.TEXT,
        sensitive=True,
    )
    snapshot = GuidedFormSnapshot(page_url="https://careers.example.test", fields=[field])
    answers = ApplicationFormAnswers(
        notion_page_id="page-1",
        tracker=TrackerName.PRODUCTION,
        field_values={"sponsorship": "Yes"},
    )
    facts = build_guided_candidate_facts(
        _evidence(),
        tmp_path / "approved.pdf",
        answers,
    )

    with pytest.raises(ValueError, match="candidate-confirmed"):
        validate_guided_form_plan(_plan(field.field_id, "identity.email"), snapshot, facts)
    validate_guided_form_plan(
        _plan(field.field_id, "confirmed.field:sponsorship"),
        snapshot,
        facts,
    )


def test_guided_plan_rejects_unknown_unresolved_field(tmp_path: Path) -> None:
    snapshot = GuidedFormSnapshot(page_url="https://careers.example.test", fields=[])
    facts = build_guided_candidate_facts(_evidence(), tmp_path / "approved.pdf", None)
    plan = GuidedFormPlan(
        unresolved_field_ids=["missing-field"],
        summary="No mapping available.",
    )

    with pytest.raises(ValueError, match="unknown unresolved"):
        validate_guided_form_plan(plan, snapshot, facts)
