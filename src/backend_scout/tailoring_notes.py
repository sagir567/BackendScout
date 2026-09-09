import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict

TAILORING_NOTE_ROOT = Path("data/tailoring_notes")


class TailoringNote(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notion_page_id: str
    tracker: str
    text: str
    created_at: datetime


def save_tailoring_note(
    page_id: str,
    tracker: str,
    text: str,
    root: Path = TAILORING_NOTE_ROOT,
) -> TailoringNote:
    normalized = text.strip()
    if not normalized:
        raise ValueError("Tailoring note must not be blank")
    note = TailoringNote(
        notion_page_id=page_id,
        tracker=tracker,
        text=normalized,
        created_at=datetime.now(UTC),
    )
    path = root / tracker / f"{page_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note.model_dump_json(indent=2), encoding="utf-8")
    return note


def load_tailoring_note(
    page_id: str,
    tracker: str,
    root: Path = TAILORING_NOTE_ROOT,
) -> TailoringNote | None:
    path = root / tracker / f"{page_id}.json"
    if not path.is_file():
        return None
    note = TailoringNote.model_validate_json(path.read_text(encoding="utf-8"))
    if note.notion_page_id != page_id or note.tracker != tracker:
        raise ValueError("Tailoring note does not match this application")
    return note


def tailoring_note_sha256(note: TailoringNote | None) -> str | None:
    if note is None:
        return None
    return hashlib.sha256(note.text.encode("utf-8")).hexdigest()
