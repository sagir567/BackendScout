"""Visible, conservative browser preparation for approved application forms."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import sleep

from backend_scout.models import CareerEvidence


@dataclass(frozen=True)
class BrowserPreparationResult:
    state: str
    filled_fields: tuple[str, ...]
    message: str


HUMAN_VERIFICATION_MARKERS = ("captcha", "recaptcha", "hcaptcha", "turnstile", "verify you are human")
SUBMISSION_CONFIRMATION_MARKERS = (
    "application received",
    "application submitted",
    "thank you for applying",
    "thanks for applying",
)


def contains_human_verification(page_text: str, frame_urls: list[str] | None = None) -> bool:
    haystack = " ".join([page_text, *(frame_urls or [])]).casefold()
    return any(marker in haystack for marker in HUMAN_VERIFICATION_MARKERS)


def is_submission_confirmation(page_text: str) -> bool:
    return any(marker in page_text.casefold() for marker in SUBMISSION_CONFIRMATION_MARKERS)


def prepare_visible_submission(
    application_url: str,
    profile_root: Path,
    evidence: CareerEvidence,
    approved_attachment: Path,
    on_human_verification: Callable[[], None] | None = None,
    wait_for_human_seconds: int = 0,
) -> BrowserPreparationResult:
    """Open a persistent, visible browser and fill only evidence-backed basics.

    This intentionally does not submit. The separate resume command only acts
    after the form is prepared and no human verification is present.
    """
    from playwright.sync_api import sync_playwright

    profile_root.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_root), headless=False, accept_downloads=True
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(application_url, wait_until="domcontentloaded")
        if contains_human_verification(page.locator("body").inner_text(), [frame.url for frame in page.frames]):
            if on_human_verification:
                on_human_verification()
            # Keep the visible persistent browser alive while the candidate uses
            # Chrome Remote Desktop. No challenge is inspected, answered, or bypassed.
            deadline = wait_for_human_seconds
            while deadline > 0 and contains_human_verification(
                page.locator("body").inner_text(), [frame.url for frame in page.frames]
            ):
                sleep(2)
                deadline -= 2
            if not contains_human_verification(
                page.locator("body").inner_text(), [frame.url for frame in page.frames]
            ):
                return _fill_safe_fields(page, evidence, approved_attachment)
            context.close()
            return BrowserPreparationResult(
                "awaiting_human_verification", (), "Human verification detected; complete it through Chrome Remote Desktop."
            )

        result = _fill_safe_fields(page, evidence, approved_attachment)
        context.close()
    return result


def _fill_safe_fields(page, evidence: CareerEvidence, approved_attachment: Path) -> BrowserPreparationResult:
    candidates = {
        "name": evidence.identity.full_name,
        "email": evidence.identity.email,
        "phone": evidence.identity.phone,
        "location": evidence.identity.location,
    }
    filled: list[str] = []
    for field, value in candidates.items():
        if not value:
            continue
        selector = f'input[name*="{field}" i], input[id*="{field}" i]'
        locator = page.locator(selector).first
        if locator.count() and locator.is_visible() and locator.input_value() == "":
            locator.fill(value)
            filled.append(field)

    upload = page.locator('input[type="file"]').first
    if upload.count() and upload.is_visible():
        upload.set_input_files(str(approved_attachment))
        filled.append("cv_attachment")
    return BrowserPreparationResult("submission_prepared", tuple(filled), "Prepared visible form without submitting it.")


def submit_visible_submission(
    application_url: str,
    profile_root: Path,
    evidence: CareerEvidence,
    approved_attachment: Path,
    on_human_verification: Callable[[], None] | None = None,
) -> BrowserPreparationResult:
    """Submit an already-approved portal application only when confirmation is visible."""
    from playwright.sync_api import sync_playwright

    profile_root.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(profile_root), headless=False, accept_downloads=True
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(application_url, wait_until="domcontentloaded")
        if contains_human_verification(page.locator("body").inner_text(), [frame.url for frame in page.frames]):
            if on_human_verification:
                on_human_verification()
            context.close()
            return BrowserPreparationResult(
                "awaiting_human_verification", (), "Human verification is still required."
            )
        prepared = _fill_safe_fields(page, evidence, approved_attachment)
        submit = page.locator('button[type="submit"], input[type="submit"]').first
        if not submit.count() or not submit.is_visible():
            context.close()
            return BrowserPreparationResult(
                "submission_prepared",
                prepared.filled_fields,
                "No unambiguous submit control was found; the application remains prepared.",
            )
        submit.click()
        page.wait_for_timeout(1_000)
        body = page.locator("body").inner_text()
        if contains_human_verification(body, [frame.url for frame in page.frames]):
            if on_human_verification:
                on_human_verification()
            context.close()
            return BrowserPreparationResult(
                "awaiting_human_verification", prepared.filled_fields, "Human verification appeared after submit."
            )
        if is_submission_confirmation(body):
            context.close()
            return BrowserPreparationResult(
                "submitted", prepared.filled_fields, "Portal confirmed that the application was submitted."
            )
        context.close()
    return BrowserPreparationResult(
        "submission_prepared",
        prepared.filled_fields,
        "Submit was clicked but no confirmation page was detected; submission was not recorded.",
    )
