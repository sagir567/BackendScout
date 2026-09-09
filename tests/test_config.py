from backend_scout.config import Settings, TrackerName


def test_settings_parse_comma_separated_telegram_user_ids() -> None:
    settings = Settings.model_validate(
        {
            "telegram_allowed_user_ids": " 12345, 67890 ,12345 ",
        }
    )

    assert settings.telegram_allowed_user_ids == "12345,67890,12345"
    assert settings.telegram_allowed_user_id_set == {12345, 67890}


def test_settings_resolve_test_and_production_trackers_separately() -> None:
    settings = Settings.model_validate(
        {
            "notion_applications_data_source_id": "legacy-test",
            "notion_test_applications_data_source_id": "",
            "notion_production_applications_data_source_id": "production-id",
        }
    )

    assert settings.applications_data_source_id_for(TrackerName.TEST) == "legacy-test"
    assert settings.applications_data_source_id_for(TrackerName.PRODUCTION) == "production-id"


def test_settings_exposes_private_submission_proof_root() -> None:
    settings = Settings.model_validate(
        {
            "submission_proof_root": "/tmp/backendscout-proofs",
        }
    )

    assert str(settings.submission_proof_root) == "/tmp/backendscout-proofs"
