from pathlib import Path

from backend_scout.submission import (
    _candidate_answer_locator,
    _capture_submission_screenshot,
    _matches_confirmed_option,
    contains_human_verification,
    is_explicit_apply_now_label,
    is_submission_confirmation,
    resolve_apply_now_url,
)


def test_submission_detects_captcha_without_attempting_to_solve_it() -> None:
    assert contains_human_verification("Please complete the CAPTCHA to continue")
    assert contains_human_verification("", ["https://www.google.com/recaptcha/api2/anchor"])
    assert not contains_human_verification("Application form")


def test_submission_requires_an_explicit_confirmation_page_before_recording_success() -> None:
    assert is_submission_confirmation("Thank you for applying. We received your application.")
    assert not is_submission_confirmation("The form is ready to submit")


def test_submit_button_requires_the_exact_apply_now_label() -> None:
    assert is_explicit_apply_now_label("Apply Now")
    assert is_explicit_apply_now_label(" apply now ")
    assert not is_explicit_apply_now_label("Apply")
    assert not is_explicit_apply_now_label("Submit application")


def test_apply_now_url_resolution_allows_only_public_http_destinations() -> None:
    current = "https://careers.example.test/jobs/backend"

    assert resolve_apply_now_url(current, "/apply/backend") == "https://careers.example.test/apply/backend"
    assert resolve_apply_now_url(current, "https://ats.example.test/jobs/123") == "https://ats.example.test/jobs/123"
    assert resolve_apply_now_url(current, "javascript:submit()") is None
    assert resolve_apply_now_url(current, None) is None


def test_candidate_confirmed_answers_can_target_stable_input_ids() -> None:
    class Page:
        selector = ""

        def locator(self, selector: str):
            self.selector = selector
            return self

        @property
        def first(self):
            return self

    page = Page()

    assert _candidate_answer_locator(page, "country") is page
    assert 'input[name="country"]' in page.selector
    assert 'textarea[aria-label="country"]' in page.selector
    assert 'input[id="country"]' in page.selector


def test_combobox_option_must_match_the_candidate_confirmed_value() -> None:
    assert _matches_confirmed_option("Yes", "yes")
    assert _matches_confirmed_option("Israel +972", "Israel")
    assert not _matches_confirmed_option("No", "Yes")
    assert not _matches_confirmed_option("Israel", "Isra")


def test_submission_proof_screenshot_is_saved_and_hashed(tmp_path) -> None:
    class Page:
        def screenshot(self, path: str, full_page: bool) -> None:
            assert full_page
            Path(path).write_bytes(b"fake png")

    proof_path = tmp_path / "proof.png"

    screenshot_path, screenshot_sha256 = _capture_submission_screenshot(Page(), proof_path)

    assert screenshot_path == str(proof_path.resolve())
    assert screenshot_sha256 == "6fc1ef73e2efe0bf82f806590c5e3001d7b9d4390079b02aa918090d9a7a775a"
