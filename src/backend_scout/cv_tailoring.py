import json
import re
from typing import Any

from backend_scout.models import (
    CareerEvidence,
    CvStyle,
    Job,
    TailoredBullet,
    TailoredCv,
    TailoredExperience,
    TailoredProject,
)

TAILORING_INSTRUCTIONS = """You tailor a CV using only the supplied career evidence.
Never invent or infer skills, achievements, metrics, dates, employers, education,
or responsibilities. Every summary or bullet must cite one or more evidence IDs.
Select and rewrite only evidence relevant to the job. Keep language concrete and
professional. Preserve any ownership qualifier in the evidence, including
AI-assisted, directed, guided, reviewed, collaborated, or taught; never turn it
into a claim of sole hands-on implementation. Follow every cv_style rule. Never
put a raw URL in headline, summary, or a bullet; document rendering handles links.
Return JSON that exactly matches the supplied schema."""

RAW_URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)


def openai_json_schema(model: type[TailoredCv]) -> dict[str, Any]:
    """Adapt a Pydantic schema to the strict JSON Schema subset used by Responses."""
    schema = model.model_json_schema()
    _require_all_object_properties(schema)
    return schema


def _require_all_object_properties(schema: dict[str, Any]) -> None:
    """Require every property recursively; nullable types retain optional semantics."""
    properties = schema.get("properties")
    if isinstance(properties, dict):
        schema["required"] = list(properties)
        for property_schema in properties.values():
            if isinstance(property_schema, dict):
                _require_all_object_properties(property_schema)

    definitions = schema.get("$defs")
    if isinstance(definitions, dict):
        for definition in definitions.values():
            if isinstance(definition, dict):
                _require_all_object_properties(definition)

    items = schema.get("items")
    if isinstance(items, dict):
        _require_all_object_properties(items)

    for branch_key in ("anyOf", "oneOf", "allOf"):
        branches = schema.get(branch_key)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    _require_all_object_properties(branch)


def generate_tailored_cv(
    evidence: CareerEvidence,
    job: Job,
    style: CvStyle,
    api_key: str,
    model: str,
    revision_feedback: str | None = None,
) -> TailoredCv:
    """Generate a structured CV draft with strict evidence citations."""
    from openai import OpenAI

    response = OpenAI(api_key=api_key).responses.create(
        model=model,
        instructions=TAILORING_INSTRUCTIONS,
        input=json.dumps(
            {
                "job": job.model_dump(mode="json"),
                "career_evidence": evidence.model_dump(mode="json"),
                "cv_style": style.model_dump(mode="json"),
                "revision_feedback": revision_feedback,
            },
            ensure_ascii=False,
        ),
        text={
            "format": {
                "type": "json_schema",
                "name": "tailored_cv",
                "schema": openai_json_schema(TailoredCv),
                "strict": True,
            }
        },
        store=False,
    )
    if not response.output_text:
        raise ValueError("OpenAI returned no CV draft text")

    draft = TailoredCv.model_validate_json(response.output_text)
    validate_tailored_cv_against_evidence(draft, evidence, style)
    return draft


def build_evidence_only_draft(evidence: CareerEvidence) -> TailoredCv:
    """Create a no-model draft for preview and deterministic test coverage."""
    return TailoredCv(
        summary=evidence.summary,
        summary_evidence_ids=sorted(evidence.evidence_ids()) if evidence.summary else [],
        skills=[group.model_copy(deep=True) for group in evidence.skills],
        experience=[
            TailoredExperience(
                evidence_id=item.id,
                bullets=[TailoredBullet(text=bullet.text, evidence_ids=[bullet.id]) for bullet in item.bullets],
            )
            for item in evidence.experience
        ],
        projects=[
            TailoredProject(
                evidence_id=item.id,
                bullets=[TailoredBullet(text=bullet.text, evidence_ids=[bullet.id]) for bullet in item.bullets],
            )
            for item in evidence.projects
        ],
        education=[item.model_copy(deep=True) for item in evidence.education],
        publications=[item.model_copy(deep=True) for item in evidence.publications],
    )


def validate_tailored_cv_against_evidence(
    draft: TailoredCv,
    evidence: CareerEvidence,
    style: CvStyle | None = None,
) -> None:
    if style and not style.include_headline and draft.headline:
        raise ValueError("CV draft includes a headline despite the style contract")
    _reject_raw_urls(draft)

    allowed_ids = evidence.evidence_ids()
    if set(draft.summary_evidence_ids) - allowed_ids:
        raise ValueError("CV summary cites unknown evidence")
    if draft.summary and not draft.summary_evidence_ids:
        raise ValueError("CV summary must cite career evidence")

    evidence_skills = {item.strip().casefold() for group in evidence.skills for item in group.items}
    for group in draft.skills:
        if any(item.casefold() not in evidence_skills for item in group.items):
            raise ValueError("CV draft includes a skill not present in career evidence")

    experience_by_id = {item.id: item for item in evidence.experience}
    project_by_id = {item.id: item for item in evidence.projects}
    _validate_tailored_sections(draft.experience, experience_by_id, "experience")
    _validate_tailored_sections(draft.projects, project_by_id, "project")
    if any(item not in evidence.education for item in draft.education):
        raise ValueError("CV draft includes education not present in career evidence")
    if any(item not in evidence.publications for item in draft.publications):
        raise ValueError("CV draft includes a publication not present in career evidence")


def _reject_raw_urls(draft: TailoredCv) -> None:
    visible_text = [draft.headline, draft.summary]
    visible_text.extend(
        bullet.text for section in draft.experience for bullet in section.bullets
    )
    visible_text.extend(
        bullet.text for section in draft.projects for bullet in section.bullets
    )
    if any(text and RAW_URL_PATTERN.search(text) for text in visible_text):
        raise ValueError("CV draft includes a raw URL in visible text")


def _validate_tailored_sections(
    sections: list[TailoredExperience] | list[TailoredProject],
    evidence_by_id: dict[str, Any],
    section_name: str,
) -> None:
    for section in sections:
        source = evidence_by_id.get(section.evidence_id)
        if source is None:
            raise ValueError(f"CV draft references unknown {section_name} evidence")
        source_bullet_ids = {bullet.id for bullet in source.bullets}
        for bullet in section.bullets:
            if not set(bullet.evidence_ids).issubset(source_bullet_ids):
                raise ValueError(f"CV {section_name} bullet cites unrelated evidence")
