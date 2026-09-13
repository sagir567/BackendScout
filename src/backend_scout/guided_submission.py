"""OpenAI-guided form mapping with a deterministic, evidence-only boundary."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend_scout.application_answers import ApplicationFormAnswers
from backend_scout.models import CareerEvidence

GUIDED_FORM_INSTRUCTIONS = """You map application-form fields to approved candidate fact keys.
Return only the structured output schema. Use field IDs and fact keys exactly as supplied.
Every supplied fact descriptor is approved for ordinary application fields. The
candidate_confirmed flag is an additional requirement only for sensitive fields.
Never write, infer, transform, or invent a candidate value. Leave a field unresolved when no
approved fact is an exact semantic match. Do not map a field whose current_value_present flag
is true. Sensitive legal, eligibility, sponsorship, salary,
consent, or demographic fields may use only facts explicitly marked candidate_confirmed.
The application executes and validates mappings after your response; you cannot click,
navigate, upload, submit, or override its policy."""

HUMAN_VERIFICATION_FIELD_MARKERS = (
    "captcha",
    "recaptcha",
    "hcaptcha",
    "turnstile",
    "human verification",
    "verify you are human",
    "security code",
    "one-time code",
    "otp",
)
SENSITIVE_FIELD_MARKERS = (
    "salary",
    "compensation",
    "sponsorship",
    "sponsor",
    "visa",
    "work authorization",
    "legally authorized",
    "eligibility",
    "eligible",
    "gender",
    "sex",
    "race",
    "ethnicity",
    "disability",
    "veteran",
    "demographic",
    "consent",
    "privacy",
    "criminal",
)
SUBMISSION_FIELD_MARKERS = ("submit application", "apply now", "send application")
IDENTITY_FIELD_MARKERS = {
    "identity.full_name": ("full name", "legal name", "candidate name", "applicant name"),
    "identity.first_name": ("first name", "given name"),
    "identity.last_name": ("last name", "family name", "surname"),
    "identity.email": ("email", "e-mail", "inbox"),
    "identity.phone": ("phone", "mobile", "telephone", "contact number"),
    "identity.location": ("location", "city", "residence", "address"),
    "identity.linkedin": ("linkedin",),
}


class GuidedControlKind(str, Enum):
    TEXT = "text"
    EMAIL = "email"
    TEL = "tel"
    URL = "url"
    TEXTAREA = "textarea"
    SELECT = "select"
    COMBOBOX = "combobox"
    CHECKBOX = "checkbox"
    RADIO = "radio"
    FILE = "file"


class GuidedFactKind(str, Enum):
    TEXT = "text"
    CHOICE = "choice"
    FILE = "file"


class GuidedFormField(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    label: str
    kind: GuidedControlKind
    required: bool = False
    options: list[str] = Field(default_factory=list)
    current_value_present: bool = False
    sensitive: bool = False


class GuidedFormSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_url: str
    fields: list[GuidedFormField] = Field(default_factory=list)


class GuidedFactDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    description: str
    kind: GuidedFactKind
    candidate_confirmed: bool = False


@dataclass(frozen=True)
class GuidedCandidateFact:
    descriptor: GuidedFactDescriptor
    value: str


class GuidedFieldMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_id: str
    fact_key: str
    reason: str


class GuidedFormPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mappings: list[GuidedFieldMapping] = Field(default_factory=list)
    unresolved_field_ids: list[str] = Field(default_factory=list)
    summary: str


class GuidedPlanUnavailableError(RuntimeError):
    """The optional model planner could not produce a usable mapping."""


def is_human_verification_field(label: str) -> bool:
    normalized = " ".join(label.casefold().split())
    return any(marker in normalized for marker in HUMAN_VERIFICATION_FIELD_MARKERS)


def is_sensitive_form_field(label: str) -> bool:
    normalized = " ".join(label.casefold().split())
    return any(marker in normalized for marker in SENSITIVE_FIELD_MARKERS)


def is_submission_field(label: str) -> bool:
    normalized = " ".join(label.casefold().split())
    return any(marker in normalized for marker in SUBMISSION_FIELD_MARKERS)


def build_guided_candidate_facts(
    evidence: CareerEvidence,
    approved_attachment: Path,
    form_answers: ApplicationFormAnswers | None,
) -> dict[str, GuidedCandidateFact]:
    """Build the only local values a guided mapping is allowed to select."""
    name_parts = evidence.identity.full_name.split(maxsplit=1)
    linkedin = next(
        (link for link in evidence.identity.links if "linkedin.com" in link.casefold()),
        "",
    )
    identity_values = {
        "identity.full_name": (evidence.identity.full_name, "candidate's full name"),
        "identity.first_name": (name_parts[0], "candidate's first name"),
        "identity.last_name": (
            name_parts[1] if len(name_parts) > 1 else "",
            "candidate's last name",
        ),
        "identity.email": (evidence.identity.email, "candidate's email address"),
        "identity.phone": (evidence.identity.phone or "", "candidate's phone number"),
        "identity.location": (evidence.identity.location or "", "candidate's location"),
        "identity.linkedin": (linkedin, "candidate's LinkedIn profile URL"),
    }
    facts = {
        key: GuidedCandidateFact(
            GuidedFactDescriptor(
                key=key,
                description=description,
                kind=GuidedFactKind.TEXT,
            ),
            value,
        )
        for key, (value, description) in identity_values.items()
        if value
    }
    facts["approved.cv_pdf"] = GuidedCandidateFact(
        GuidedFactDescriptor(
            key="approved.cv_pdf",
            description="the exact approved CV PDF",
            kind=GuidedFactKind.FILE,
        ),
        str(approved_attachment),
    )
    if form_answers is None:
        return facts

    for field_key, value in form_answers.field_values.items():
        key = f"confirmed.field:{field_key}"
        facts[key] = GuidedCandidateFact(
            GuidedFactDescriptor(
                key=key,
                description=f"candidate-confirmed answer for saved form field {field_key!r}",
                kind=GuidedFactKind.TEXT,
                candidate_confirmed=True,
            ),
            value,
        )
    for field_name, configured_labels in form_answers.checkbox_values.items():
        labels = [configured_labels] if isinstance(configured_labels, str) else configured_labels
        for index, label in enumerate(labels):
            key = f"confirmed.choice:{field_name}:{index}"
            facts[key] = GuidedCandidateFact(
                GuidedFactDescriptor(
                    key=key,
                    description=f"candidate-confirmed choice for saved form field {field_name!r}",
                    kind=GuidedFactKind.CHOICE,
                    candidate_confirmed=True,
                ),
                label,
            )
    return facts


def validate_guided_form_plan(
    plan: GuidedFormPlan,
    snapshot: GuidedFormSnapshot,
    facts: dict[str, GuidedCandidateFact],
) -> None:
    """Fail closed when a model proposes anything outside the local contract."""
    fields = {field.field_id: field for field in snapshot.fields}
    mapped_ids: set[str] = set()
    for mapping in plan.mappings:
        field = fields.get(mapping.field_id)
        if field is None:
            raise ValueError(f"Guided plan references unknown field: {mapping.field_id}")
        if mapping.field_id in mapped_ids:
            raise ValueError(f"Guided plan maps a field more than once: {mapping.field_id}")
        mapped_ids.add(mapping.field_id)
        fact = facts.get(mapping.fact_key)
        if fact is None:
            raise ValueError(f"Guided plan references unknown fact: {mapping.fact_key}")
        if field.current_value_present:
            raise ValueError(f"Guided plan tried to overwrite a populated field: {field.field_id}")
        if is_human_verification_field(field.label):
            raise ValueError("Guided plan cannot interact with human-verification fields")
        if is_submission_field(field.label):
            raise ValueError("Guided plan cannot interact with submission controls")
        if field.sensitive and not fact.descriptor.candidate_confirmed:
            raise ValueError(
                f"Sensitive field {field.field_id} requires a candidate-confirmed answer"
            )
        _validate_fact_compatibility(field, fact.descriptor)
        _validate_fact_semantics(field, fact.descriptor)

    unknown_unresolved = set(plan.unresolved_field_ids) - fields.keys()
    if unknown_unresolved:
        raise ValueError(
            "Guided plan references unknown unresolved fields: "
            + ", ".join(sorted(unknown_unresolved))
        )


def _validate_fact_compatibility(
    field: GuidedFormField,
    fact: GuidedFactDescriptor,
) -> None:
    if field.kind == GuidedControlKind.FILE:
        if fact.kind != GuidedFactKind.FILE or fact.key != "approved.cv_pdf":
            raise ValueError(f"File field {field.field_id} requires the approved CV fact")
        return
    if fact.kind == GuidedFactKind.FILE:
        raise ValueError(f"Approved CV fact cannot populate non-file field {field.field_id}")
    if field.kind in {GuidedControlKind.CHECKBOX, GuidedControlKind.RADIO}:
        if fact.kind != GuidedFactKind.CHOICE or not fact.candidate_confirmed:
            raise ValueError(f"Choice field {field.field_id} requires a confirmed choice")
        return
    if fact.kind == GuidedFactKind.CHOICE:
        raise ValueError(f"Choice fact cannot populate text field {field.field_id}")


def _validate_fact_semantics(
    field: GuidedFormField,
    fact: GuidedFactDescriptor,
) -> None:
    normalized_label = " ".join(field.label.casefold().split())
    if fact.key == "approved.cv_pdf":
        if not any(marker in normalized_label for marker in ("resume", "cv", "curriculum")):
            raise ValueError(f"Approved CV fact does not match field {field.field_id}")
        return
    markers = IDENTITY_FIELD_MARKERS.get(fact.key)
    if markers is not None:
        if fact.key == "identity.full_name" and normalized_label.strip() == "name":
            return
        if not any(marker in normalized_label for marker in markers):
            raise ValueError(f"Identity fact {fact.key} does not match field {field.field_id}")
        return
    if fact.key.startswith("confirmed.field:"):
        answer_key = fact.key.removeprefix("confirmed.field:")
    elif fact.key.startswith("confirmed.choice:"):
        answer_key = fact.key.removeprefix("confirmed.choice:").rsplit(":", 1)[0]
    else:
        raise ValueError(f"Unsupported guided fact type: {fact.key}")
    normalized_key = " ".join(part for part in answer_key.casefold().replace("_", " ").split())
    if normalized_key not in normalized_label and normalized_label not in normalized_key:
        raise ValueError(f"Confirmed fact {fact.key} does not match field {field.field_id}")


def guided_planner_payload(
    snapshot: GuidedFormSnapshot,
    facts: dict[str, GuidedCandidateFact],
) -> dict[str, object]:
    """Build the compact model input without URLs or local candidate values."""
    return {
        "fields": [field.model_dump(mode="json") for field in snapshot.fields],
        "approved_facts": [
            fact.descriptor.model_dump(mode="json") for fact in facts.values()
        ],
    }


def generate_guided_form_plan(
    snapshot: GuidedFormSnapshot,
    facts: dict[str, GuidedCandidateFact],
    api_key: str,
    model: str,
) -> GuidedFormPlan:
    """Ask one focused Agents SDK planner for a mapping, then validate locally."""
    from agents import Agent, RunConfig, Runner, set_default_openai_key, set_tracing_disabled
    from agents.exceptions import AgentsException
    from openai import OpenAIError
    from pydantic import ValidationError

    set_default_openai_key(api_key, use_for_tracing=False)
    set_tracing_disabled(True)
    agent = Agent(
        name="BackendScout form mapper",
        instructions=GUIDED_FORM_INSTRUCTIONS,
        model=model,
        output_type=GuidedFormPlan,
    )
    payload = guided_planner_payload(snapshot, facts)
    try:
        # Playwright's synchronous API owns an event loop on this thread. Keep the
        # Agents SDK loop isolated so the browser runtime remains synchronous.
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="form-planner") as executor:
            result = executor.submit(
                Runner.run_sync,
                agent,
                json.dumps(payload, ensure_ascii=False),
                max_turns=1,
                run_config=RunConfig(
                    tracing_disabled=True,
                    trace_include_sensitive_data=False,
                    workflow_name="BackendScout guided form mapping",
                ),
            ).result()
        if isinstance(result.final_output, GuidedFormPlan):
            plan = result.final_output
        elif isinstance(result.final_output, str):
            plan = GuidedFormPlan.model_validate_json(result.final_output)
        else:
            plan = GuidedFormPlan.model_validate(result.final_output)
        validate_guided_form_plan(plan, snapshot, facts)
    except (AgentsException, OpenAIError, ValidationError, ValueError, RuntimeError) as exc:
        raise GuidedPlanUnavailableError("OpenAI form mapping did not produce a safe plan") from exc
    return plan
