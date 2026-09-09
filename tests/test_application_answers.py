from pathlib import Path

from backend_scout.application_answers import (
    ApplicationFormAnswers,
    load_application_form_answers,
    save_application_form_answers,
)
from backend_scout.config import TrackerName


def test_application_answers_are_private_and_bound_to_page_and_tracker(tmp_path: Path) -> None:
    answers = ApplicationFormAnswers(
        notion_page_id="page-123",
        tracker=TrackerName.PRODUCTION,
        field_values={"country": "Israel"},
        checkbox_values={"years": "1-2"},
    )

    path = save_application_form_answers(answers, tmp_path)

    assert path == tmp_path / "production" / "page-123.json"
    assert load_application_form_answers("page-123", TrackerName.PRODUCTION, tmp_path) == answers
    assert load_application_form_answers("page-123", TrackerName.TEST, tmp_path) is None
