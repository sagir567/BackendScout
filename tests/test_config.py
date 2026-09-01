from backend_scout.config import Settings


def test_settings_parse_comma_separated_telegram_user_ids() -> None:
    settings = Settings.model_validate(
        {
            "telegram_allowed_user_ids": " 12345, 67890 ,12345 ",
        }
    )

    assert settings.telegram_allowed_user_ids == "12345,67890,12345"
    assert settings.telegram_allowed_user_id_set == {12345, 67890}
