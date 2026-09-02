from backend_scout.submission import contains_human_verification, is_submission_confirmation


def test_submission_detects_captcha_without_attempting_to_solve_it() -> None:
    assert contains_human_verification("Please complete the CAPTCHA to continue")
    assert contains_human_verification("", ["https://www.google.com/recaptcha/api2/anchor"])
    assert not contains_human_verification("Application form")


def test_submission_requires_an_explicit_confirmation_page_before_recording_success() -> None:
    assert is_submission_confirmation("Thank you for applying. We received your application.")
    assert not is_submission_confirmation("The form is ready to submit")
