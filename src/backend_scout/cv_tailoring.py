import json
import re
from typing import Any

from backend_scout.models import (
    CareerEvidence,
    CvStyle,
    Job,
    ProjectEvidence,
    TailoredBullet,
    TailoredCv,
    TailoredExperience,
    TailoredProject,
)

TAILORING_INSTRUCTIONS = """You tailor a CV using only the supplied career evidence.
Never invent or infer skills, achievements, metrics, dates, employers, education,
or responsibilities. Every summary or bullet must cite one or more evidence IDs.
Select and rewrite only evidence relevant to the job. Keep language concrete and
professional. Preserve ownership scope: directed, guided, reviewed, collaborated,
or taught must not become sole hands-on implementation. In project descriptions,
describe verified functionality and actual contributions, not that the project
was created using AI. Use neutral contribution verbs only when supported.
Present verified AI and agent tool use (Codex, Cline, Ollama, OpenAI API) in a
separate AI and Agent Tooling skill group or a concise summary competency.
Keep genuine AI product functionality (agents, LLM integrations) where relevant.
Never imply unaided coding, independent expertise, or professional experience
that the evidence does not support. Follow every cv_style rule. Never
put a raw URL in headline, summary, or a bullet; document rendering handles links.
In Technical Skills, copy only exact item strings from career_evidence.skills;
do not rename, combine, broaden, or infer skill labels.
The supplied projects have already passed deterministic job-relevance filtering.
Use projects only when they strengthen this specific application, and omit unrelated
languages, frameworks, repositories, and projects even when they exist in the evidence.
Return JSON that exactly matches the supplied schema."""

RAW_URL_PATTERN = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
PROJECT_AI_AUTHORSHIP_PATTERN = re.compile(
    r"\b(?:AI|agent|LLM)[ -]assisted\b|"
    r"\b(?:built|created|developed|implemented|generated)\s+(?:entirely\s+)?"
    r"(?:using|with|by)\s+(?:AI|agents?|LLMs?|Codex|Cline)\b",
    re.IGNORECASE,
)


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
    tailoring_note: str | None = None,
    *,
    usage_budget=None,
) -> TailoredCv:
    """Generate a structured CV draft with strict evidence citations."""
    from openai import OpenAI

    tailoring_evidence = evidence_for_job(evidence, job)
    content = json.dumps(
        {"job": job.model_dump(mode="json"),
         "career_evidence": tailoring_evidence.model_dump(mode="json"),
         "cv_style": style.model_dump(mode="json"), "revision_feedback": revision_feedback,
         "tailoring_note": tailoring_note}, ensure_ascii=False,
    )
    schema = openai_json_schema(TailoredCv)
    reservation = None
    if usage_budget is not None:
        input_cap = len(content.encode()) + len(TAILORING_INSTRUCTIONS.encode()) + len(json.dumps(schema).encode()) + 4096
        reservation = usage_budget.reserve_external(model, input_cap, 6000)
    options = {"max_output_tokens": 6000} if usage_budget is not None else {}
    client_options = {"max_retries": 0, "timeout": 60} if usage_budget is not None else {}
    response = OpenAI(api_key=api_key, **client_options).responses.create(
        model=model,
        instructions=TAILORING_INSTRUCTIONS,
        input=content,
        text={
            "format": {
                "type": "json_schema",
                "name": "tailored_cv",
                "schema": schema,
                "strict": True,
            }
        },
        store=False,
        **options,
    )
    if reservation is not None:
        usage_budget.settle_external(reservation.reservation_id,
                                     response.usage.input_tokens, response.usage.output_tokens)
    if not response.output_text:
        raise ValueError("OpenAI returned no CV draft text")

    draft = remove_unsupported_skills(
        TailoredCv.model_validate_json(response.output_text),
        evidence,
    )
    validate_tailored_cv_against_evidence(draft, evidence, style, job)
    return draft


def remove_unsupported_skills(draft: TailoredCv, evidence: CareerEvidence) -> TailoredCv:
    """Drop model-selected skill labels that are absent from verified evidence."""
    allowed_skills = {item.casefold() for group in evidence.skills for item in group.items}
    filtered_groups = []
    for group in draft.skills:
        supported_items = [item for item in group.items if item.casefold() in allowed_skills]
        if supported_items:
            filtered_groups.append(group.model_copy(update={"items": supported_items}))
    return draft.model_copy(update={"skills": filtered_groups})


def build_evidence_only_draft(
    evidence: CareerEvidence,
    job: Job | None = None,
) -> TailoredCv:
    """Create a no-model draft for preview and deterministic test coverage."""
    selected_evidence = evidence_for_job(evidence, job) if job else evidence
    return TailoredCv(
        summary=selected_evidence.summary,
        summary_evidence_ids=(
            sorted(selected_evidence.evidence_ids()) if selected_evidence.summary else []
        ),
        skills=[group.model_copy(deep=True) for group in selected_evidence.skills],
        experience=[
            TailoredExperience(
                evidence_id=item.id,
                bullets=[TailoredBullet(text=bullet.text, evidence_ids=[bullet.id]) for bullet in item.bullets],
            )
            for item in selected_evidence.experience
        ],
        projects=[
            TailoredProject(
                evidence_id=item.id,
                bullets=[TailoredBullet(text=bullet.text, evidence_ids=[bullet.id]) for bullet in item.bullets],
            )
            for item in selected_evidence.projects
        ],
        education=[item.model_copy(deep=True) for item in selected_evidence.education],
        publications=[item.model_copy(deep=True) for item in selected_evidence.publications],
    )


def evidence_for_job(evidence: CareerEvidence, job: Job) -> CareerEvidence:
    """Keep only projects whose candidate-defined keywords appear in the job."""
    projects = [project for project in evidence.projects if project_is_relevant(project, job)]
    return evidence.model_copy(update={"projects": projects}, deep=True)


def project_is_relevant(project: ProjectEvidence, job: Job) -> bool:
    if not project.selection_keywords:
        return False
    job_text = " ".join([job.title, job.description, *job.required_skills]).casefold()
    return any(_contains_keyword(job_text, keyword) for keyword in project.selection_keywords)


def _contains_keyword(text: str, keyword: str) -> bool:
    return bool(
        re.search(
            rf"(?<![a-z0-9]){re.escape(keyword.casefold())}(?![a-z0-9])",
            text,
        )
    )


def validate_tailored_cv_against_evidence(
    draft: TailoredCv,
    evidence: CareerEvidence,
    style: CvStyle | None = None,
    job: Job | None = None,
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
    if any(
        PROJECT_AI_AUTHORSHIP_PATTERN.search(bullet.text)
        for project in draft.projects
        for bullet in project.bullets
    ):
        raise ValueError("Describe AI tooling as a skill, not project authorship")
    if job is not None:
        relevant_project_ids = {
            project.id for project in evidence.projects if project_is_relevant(project, job)
        }
        selected_project_ids = {project.evidence_id for project in draft.projects}
        if selected_project_ids - relevant_project_ids:
            raise ValueError("CV draft includes a project unrelated to the target job")
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
