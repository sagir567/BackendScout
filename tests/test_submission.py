from pathlib import Path

from playwright.sync_api import Error as PlaywrightError

from backend_scout.guided_submission import (
    GuidedControlKind,
    GuidedFieldMapping,
    GuidedFormField,
    GuidedFormPlan,
    GuidedFormSnapshot,
    build_guided_candidate_facts,
)
from backend_scout.models import CareerEvidence
from backend_scout.submission import (
    CapturedGuidedForm,
    _attach_approved_file,
    _candidate_answer_locator,
    _capture_submission_screenshot,
    _control_has_value,
    _follow_verified_apply_link,
    _form_scopes,
    _matches_confirmed_option,
    _unresolved_required_fields,
    apply_guided_form_plan,
    contains_human_verification,
    is_explicit_apply_now_label,
    is_submission_confirmation,
    redirected_from_job_to_home,
    resolve_apply_now_url,
    wait_for_human_verification_clear,
)


def _guided_evidence() -> CareerEvidence:
    return CareerEvidence.model_validate(
        {
            "identity": {
                "full_name": "Test Candidate",
                "email": "candidate@example.com",
            },
            "skills": [{"category": "Backend", "items": ["Python"]}],
        }
    )


def test_guided_executor_resolves_fact_key_to_local_value(tmp_path) -> None:
    class Control:
        value = ""

        def input_value(self, timeout: int) -> str:
            assert timeout == 2_000
            return self.value

        def get_attribute(self, name: str, timeout: int | None = None):
            assert name == "role"

        def fill(self, value: str, timeout: int) -> None:
            assert timeout == 5_000
            self.value = value

    field = GuidedFormField(
        field_id="field-1",
        label="Contact inbox",
        kind=GuidedControlKind.EMAIL,
        required=True,
    )
    control = Control()
    captured = CapturedGuidedForm(
        snapshot=GuidedFormSnapshot(
            page_url="https://careers.example.test",
            fields=[field],
        ),
        controls={field.field_id: control},
    )
    facts = build_guided_candidate_facts(
        _guided_evidence(),
        tmp_path / "approved.pdf",
        None,
    )
    plan = GuidedFormPlan(
        mappings=[
            GuidedFieldMapping(
                field_id=field.field_id,
                fact_key="identity.email",
                reason="Exact semantic match",
            )
        ],
        summary="Mapped email.",
    )

    filled = apply_guided_form_plan(object(), captured, plan, facts)

    assert filled == ["guided:Contact inbox"]
    assert control.value == "candidate@example.com"


def test_cv_attachment_accepts_visible_filename_after_react_removes_input(tmp_path) -> None:
    attachment = tmp_path / "approved_cv.pdf"
    attachment.write_bytes(b"approved")

    class Locator:
        def __init__(self, present: bool = False, text: str = "") -> None:
            self.present = present
            self.text = text

        @property
        def first(self):
            return self

        def count(self) -> int:
            return int(self.present)

        def set_input_files(self, path: str, timeout: int) -> None:
            assert path == str(attachment)
            assert timeout == 5_000

        def inner_text(self, timeout: int) -> str:
            assert timeout == 2_000
            return self.text

    class Scope:
        def locator(self, selector: str):
            if selector == "body":
                return Locator(text=f"Uploaded {attachment.name}")
            return Locator(present=True)

    assert _attach_approved_file(Scope(), attachment)


def test_form_scopes_skip_frames_detached_during_portal_loading() -> None:
    class Frame:
        def __init__(self, detached: bool) -> None:
            self.detached = detached

        def is_detached(self) -> bool:
            return self.detached

    main_frame = Frame(False)
    attached_frame = Frame(False)

    class Page:
        def __init__(self) -> None:
            self.frames = [main_frame, Frame(True), attached_frame]
            self.main_frame = main_frame

    page = Page()

    assert _form_scopes(page) == [page, attached_frame]


def test_submission_detects_captcha_without_attempting_to_solve_it() -> None:
    assert contains_human_verification("Please complete the CAPTCHA to continue")
    assert not contains_human_verification("", ["https://www.google.com/recaptcha/api2/anchor"])
    assert contains_human_verification("", ["https://captcha.example/challenge"])
    assert not contains_human_verification("Application form")


def test_human_verification_wait_keeps_polling_until_challenge_clears() -> None:
    checks = iter([True, True, False])
    delays: list[float] = []

    assert wait_for_human_verification_clear(
        lambda: next(checks),
        10,
        poll_seconds=2,
        sleeper=delays.append,
    )
    assert delays == [2, 2]


def test_human_verification_wait_stops_at_timeout() -> None:
    delays: list[float] = []

    assert not wait_for_human_verification_clear(
        lambda: True,
        5,
        poll_seconds=2,
        sleeper=delays.append,
    )
    assert delays == [2, 2, 1]


def test_submission_requires_an_explicit_confirmation_page_before_recording_success() -> None:
    assert is_submission_confirmation("Thank you for applying. We received your application.")
    assert not is_submission_confirmation("The form is ready to submit")


def test_submit_button_requires_the_exact_apply_now_label() -> None:
    assert is_explicit_apply_now_label("Apply Now")
    assert is_explicit_apply_now_label(" apply now ")
    assert is_explicit_apply_now_label("SUBMIT APPLICATION")
    assert not is_explicit_apply_now_label("Apply")
    assert not is_explicit_apply_now_label("Submit")


def test_apply_now_url_resolution_allows_only_public_http_destinations() -> None:
    current = "https://careers.example.test/jobs/backend"

    assert resolve_apply_now_url(current, "/apply/backend") == "https://careers.example.test/apply/backend"
    assert resolve_apply_now_url(current, "https://ats.example.test/jobs/123") == "https://ats.example.test/jobs/123"
    assert resolve_apply_now_url(current, "javascript:submit()") is None
    assert resolve_apply_now_url(current, None) is None


def test_job_redirect_to_company_home_is_not_treated_as_an_application() -> None:
    assert redirected_from_job_to_home(
        "https://makers.example.test/role/backend-engineer",
        "https://makers.example.test/",
    )
    assert not redirected_from_job_to_home(
        "https://makers.example.test/role/backend-engineer",
        "https://ats.example.test/jobs/123",
    )
    assert not redirected_from_job_to_home(
        "https://makers.example.test/role/backend-engineer",
        "https://makers.example.test/role/backend-engineer/",
    )


def test_follow_verified_apply_link_clicks_only_opening_cta_button() -> None:
    class Button:
        clicked = False

        def is_visible(self) -> bool:
            return True

        def inner_text(self) -> str:
            return "APPLY FOR THIS JOB"

        def evaluate(self, script: str) -> bool:
            return False

        def click(self) -> None:
            self.clicked = True

    class Locator:
        def __init__(self, items) -> None:
            self.items = items

        def count(self) -> int:
            return len(self.items)

        def nth(self, index: int):
            return self.items[index]

    class Page:
        url = "https://example.com/job"

        def __init__(self) -> None:
            self.button = Button()

        def locator(self, selector: str):
            return Locator([self.button]) if selector == "button" else Locator([])

        def wait_for_timeout(self, ms: int) -> None:
            pass

    page = Page()

    _follow_verified_apply_link(page)

    assert page.button.clicked


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


def test_transient_control_timeout_is_treated_as_unresolved() -> None:
    class ReplacedControl:
        def input_value(self, timeout: int):
            raise PlaywrightError("control was replaced")

    assert not _control_has_value(ReplacedControl())


def test_unresolved_required_fields_groups_required_radio_buttons() -> None:
    class Choice:
        def __init__(self, name: str, checked: bool = False) -> None:
            self.name = name
            self.checked = checked

        def is_visible(self) -> bool:
            return True

        def get_attribute(self, name: str):
            if name == "name":
                return self.name
            return None

        def is_checked(self) -> bool:
            return self.checked

    class Locator:
        def __init__(self, items):
            self.items = items

        def count(self) -> int:
            return len(self.items)

        def nth(self, index: int):
            return self.items[index]

    class Page:
        def __init__(self) -> None:
            self.frames = []

        @property
        def main_frame(self):
            return self

        def locator(self, selector: str):
            if 'input[type="checkbox"]' in selector:
                return Locator([Choice("question"), Choice("question")])
            return Locator([])

    assert _unresolved_required_fields(Page()) == ("question",)


def test_submission_proof_screenshot_is_saved_and_hashed(tmp_path) -> None:
    class Page:
        def screenshot(self, path: str, full_page: bool) -> None:
            assert full_page
            Path(path).write_bytes(b"fake png")

    proof_path = tmp_path / "proof.png"

    screenshot_path, screenshot_sha256 = _capture_submission_screenshot(Page(), proof_path)

    assert screenshot_path == str(proof_path.resolve())
    assert screenshot_sha256 == "6fc1ef73e2efe0bf82f806590c5e3001d7b9d4390079b02aa918090d9a7a775a"
