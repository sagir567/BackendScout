import json
from pathlib import Path

REVISION_ROOT = Path("data/revisions")


def save_revision_feedback(page_id: str, feedback: str, root: Path = REVISION_ROOT) -> Path:
    text = feedback.strip()
    if not text:
        raise ValueError("Revision feedback must not be blank")
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{page_id}.json"
    path.write_text(json.dumps({"page_id": page_id, "feedback": text}, indent=2), encoding="utf-8")
    return path


def load_revision_feedback(page_id: str, root: Path = REVISION_ROOT) -> str:
    path = root / f"{page_id}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError("No revision feedback has been recorded for this job") from exc
    feedback = payload.get("feedback")
    if not isinstance(feedback, str) or not feedback.strip():
        raise ValueError("Stored revision feedback is invalid")
    return feedback.strip()
