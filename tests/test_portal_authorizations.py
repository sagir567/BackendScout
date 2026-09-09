from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from backend_scout.config import TrackerName
from backend_scout.cv_artifacts import CvDraftManifest, write_manifest
from backend_scout.portal_authorizations import (
    authorize_portal_submit,
    consume_portal_submit_authorization,
    create_portal_submit_authorization,
    find_pending_portal_submit_authorization,
    require_portal_submit_authorization,
    save_portal_submit_authorization,
)


def _manifest(root: Path, draft_bytes: bytes = b"draft") -> CvDraftManifest:
    directory = root / f"v{len(list(root.glob('v*'))) + 1}"
    directory.mkdir(parents=True)
    docx_path = directory / "cv_draft.docx"
    pdf_path = directory / "cv_draft.pdf"
    docx_path.write_bytes(draft_bytes)
    pdf_path.write_bytes(b"pdf-" + draft_bytes)
    return write_manifest(
        directory,
        "page-123",
        "Example Cloud",
        "Backend Engineer",
        docx_path,
        pdf_path,
        tracker="production",
    )


def test_portal_authorization_is_bound_to_one_cv_and_consumed_once(tmp_path: Path) -> None:
    root = tmp_path / "authorizations"
    manifest = _manifest(tmp_path / "drafts")
    authorization = create_portal_submit_authorization(
        "page-123", TrackerName.PRODUCTION, "https://jobs.example.test/apply", manifest, root
    )

    authorize_portal_submit(
        "page-123", authorization.authorization_id, TrackerName.PRODUCTION,
        "https://jobs.example.test/apply", manifest, root,
    )
    active = require_portal_submit_authorization(
        "page-123", authorization.authorization_id, TrackerName.PRODUCTION,
        "https://jobs.example.test/apply", manifest, root,
    )
    assert active.authorized_at is not None

    consume_portal_submit_authorization(
        "page-123", authorization.authorization_id, TrackerName.PRODUCTION,
        "https://jobs.example.test/apply", manifest, root,
    )
    with pytest.raises(ValueError, match="already used"):
        require_portal_submit_authorization(
            "page-123", authorization.authorization_id, TrackerName.PRODUCTION,
            "https://jobs.example.test/apply", manifest, root,
        )


def test_portal_authorization_rejects_wrong_tracker_url_and_newer_cv(tmp_path: Path) -> None:
    root = tmp_path / "authorizations"
    first = _manifest(tmp_path / "drafts", b"first")
    authorization = create_portal_submit_authorization(
        "page-123", TrackerName.PRODUCTION, "https://jobs.example.test/apply", first, root
    )

    with pytest.raises(ValueError, match="different tracker"):
        authorize_portal_submit(
            "page-123", authorization.authorization_id, TrackerName.TEST,
            "https://jobs.example.test/apply", first, root,
        )
    with pytest.raises(ValueError, match="different portal URL"):
        authorize_portal_submit(
            "page-123", authorization.authorization_id, TrackerName.PRODUCTION,
            "https://other.example.test/apply", first, root,
        )
    newer = _manifest(tmp_path / "drafts", b"newer")
    with pytest.raises(ValueError, match="older CV draft"):
        authorize_portal_submit(
            "page-123", authorization.authorization_id, TrackerName.PRODUCTION,
            "https://jobs.example.test/apply", newer, root,
        )


def test_portal_authorization_expires_and_finder_returns_latest_valid_record(tmp_path: Path) -> None:
    root = tmp_path / "authorizations"
    manifest = _manifest(tmp_path / "drafts")
    expired = create_portal_submit_authorization(
        "page-123", TrackerName.PRODUCTION, "https://jobs.example.test/apply", manifest, root
    )
    save_portal_submit_authorization(
        expired.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}), root
    )
    with pytest.raises(ValueError, match="expired"):
        authorize_portal_submit(
            "page-123", expired.authorization_id, TrackerName.PRODUCTION,
            "https://jobs.example.test/apply", manifest, root,
        )

    current = create_portal_submit_authorization(
        "page-123", TrackerName.PRODUCTION, "https://jobs.example.test/apply", manifest, root
    )
    authorize_portal_submit(
        "page-123", current.authorization_id, TrackerName.PRODUCTION,
        "https://jobs.example.test/apply", manifest, root,
    )
    found = find_pending_portal_submit_authorization(
        "page-123", TrackerName.PRODUCTION, "https://jobs.example.test/apply", manifest, root
    )
    assert found.authorization_id == current.authorization_id
