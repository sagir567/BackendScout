"""Acknowledged receipt cleanup stays separate from employer delivery."""

from unittest.mock import Mock

from backend_scout.config import TrackerName


def test_worker_never_closes_paused_context_and_releases_acknowledged_success(review_service, monkeypatch):
    from backend_scout import cli

    service = review_service
    package, path = service.preview_package("example-job", TrackerName.TEST)
    service.store.save_package(package, str(path.relative_to(service.store.private_root)))
    service.store.approve("example-job", TrackerName.TEST, package.fingerprint(), "12345")
    monkeypatch.setattr(cli, "Settings", lambda: service.settings)
    monkeypatch.setattr("backend_scout.submission_notifications.acknowledged_submission_sessions",
                        lambda _: [("example-job", TrackerName.TEST, package.fingerprint())])
    manager = Mock()
    cli._service_submission_notifications(service.store.database_path, portal_sessions=manager, send=False)
    manager.close_session.assert_not_called()
    attempt = service.store.begin_attempt("example-job", TrackerName.TEST, package.fingerprint(), "cleanup-fixture")
    proof = service.store.private_root / "proof.png"
    proof.write_bytes(b"Synthetic proof bytes, never dispatched")
    service.store.finish_attempt(attempt.attempt_id, "submitted", "Synthetic receipt", "proof.png")
    cli._service_submission_notifications(service.store.database_path, portal_sessions=manager, send=False)
    manager.close_session.assert_called_once_with("example-job", TrackerName.TEST)


def test_worker_does_not_close_session_for_another_fingerprint(review_service, monkeypatch):
    from backend_scout import cli

    service = review_service
    package, path = service.preview_package("example-job", TrackerName.TEST)
    service.store.save_package(package, str(path.relative_to(service.store.private_root)))
    monkeypatch.setattr(cli, "Settings", lambda: service.settings)
    monkeypatch.setattr("backend_scout.submission_notifications.acknowledged_submission_sessions",
                        lambda _: [("example-job", TrackerName.TEST, "0" * 64)])
    manager = Mock()
    cli._service_submission_notifications(service.store.database_path, portal_sessions=manager, send=False)
    manager.close_session.assert_not_called()
