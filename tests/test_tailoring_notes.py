from backend_scout.tailoring_notes import (
    load_tailoring_note,
    save_tailoring_note,
    tailoring_note_sha256,
)


def test_tailoring_note_is_private_and_tracker_scoped(tmp_path) -> None:
    note = save_tailoring_note(
        "page-123",
        "production",
        "Emphasize the verified PrintIt C#/.NET 8 work.",
        tmp_path,
    )

    loaded = load_tailoring_note("page-123", "production", tmp_path)

    assert loaded == note
    assert tailoring_note_sha256(loaded) is not None
    assert load_tailoring_note("page-123", "test", tmp_path) is None
