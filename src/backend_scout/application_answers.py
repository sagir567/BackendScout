"""Private, candidate-confirmed answers for one application form."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from backend_scout.config import TrackerName

APPLICATION_ANSWERS_ROOT = Path("data/application_answers")


class ApplicationFormAnswers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notion_page_id: str
    tracker: TrackerName
    field_values: dict[str, str] = Field(default_factory=dict)
    checkbox_values: dict[str, str] = Field(default_factory=dict)


def load_application_form_answers(
    page_id: str,
    tracker: TrackerName,
    root: Path = APPLICATION_ANSWERS_ROOT,
) -> ApplicationFormAnswers | None:
    path = _answers_path(page_id, tracker, root)
    if not path.is_file():
        return None
    answers = ApplicationFormAnswers.model_validate_json(path.read_text(encoding="utf-8"))
    if answers.notion_page_id != page_id or answers.tracker != tracker:
        raise ValueError("Stored application answers belong to a different application")
    return answers


def save_application_form_answers(
    answers: ApplicationFormAnswers,
    root: Path = APPLICATION_ANSWERS_ROOT,
) -> Path:
    path = _answers_path(answers.notion_page_id, answers.tracker, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(answers.model_dump_json(indent=2), encoding="utf-8")
    return path


def _answers_path(page_id: str, tracker: TrackerName, root: Path) -> Path:
    return root / tracker.value / f"{page_id}.json"
