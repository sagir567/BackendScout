"""Visible, conservative browser preparation for approved application forms."""

import hashlib
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from time import sleep
from urllib.parse import urljoin, urlparse

from backend_scout.application_answers import ApplicationFormAnswers
from backend_scout.models import CareerEvidence
from backend_scout.verification_handoff import VerificationHandoffServer, apply_handoff_actions


@dataclass(frozen=True)
class BrowserPreparationResult:
    state: str
    filled_fields: tuple[str, ...]
    message: str
    unresolved_required_fields: tuple[str, ...] = ()
    resolved_application_url: str | None = None
    screenshot_path: str | None = None
    screenshot_sha256: str | None = None


HUMAN_VERIFICATION_MARKERS = ("captcha", "recaptcha", "hcaptcha", "turnstile", "verify you are human")
FRAME_HUMAN_VERIFICATION_MARKERS = ("hcaptcha", "turnstile", "challenge")
SUBMISSION_CONFIRMATION_MARKERS = (
    "application received",
    "application submitted",
    "thank you for applying",
    "thanks for applying",
)
FINAL_SUBMIT_LABELS = ("apply now", "submit application")
LOGGER = logging.getLogger(__name__)


def is_explicit_apply_now_label(text: str) -> bool:
    """Accept only the exact, applicant-facing final action label."""
    normalized = " ".join(text.strip().casefold().split())
    return normalized in FINAL_SUBMIT_LABELS


def contains_human_verification(page_text: str, frame_urls: list[str] | None = None) -> bool:
    text = page_text.casefold()
    if any(marker in text for marker in HUMAN_VERIFICATION_MARKERS):
        return True
    frame_haystack = " ".join(frame_urls or []).casefold()
    return any(marker in frame_haystack for marker in FRAME_HUMAN_VERIFICATION_MARKERS)


def wait_for_human_verification_clear(
    is_required: Callable[[], bool],
    wait_seconds: int,
    *,
    poll_seconds: int = 2,
    sleeper: Callable[[float], None] = sleep,
) -> bool:
    """Keep a browser-backed check alive until verification clears or time expires."""
    remaining = max(0, wait_seconds)
    interval = max(1, poll_seconds)
    while remaining > 0:
        if not is_required():
            return True
        delay = min(interval, remaining)
        sleeper(delay)
        remaining -= delay
    return not is_required()


def wait_for_page_verification_clear(
    page,
    wait_seconds: int,
    on_human_verification: Callable[[str | None], None] | None,
    handoff_host: str | None = None,
    handoff_port: int = 0,
) -> bool:
    """Wait for a human check and optionally expose only this page over Tailscale."""

    def is_required() -> bool:
        return contains_human_verification(
            _page_and_frame_text(page), [frame.url for frame in page.frames]
        )

    handoff = (
        VerificationHandoffServer(handoff_host, handoff_port)
        if handoff_host and wait_seconds > 0
        else None
    )
    if handoff is None:
        if on_human_verification:
            on_human_verification(None)
        return wait_for_human_verification_clear(is_required, wait_seconds)

    try:
        handoff.start()
    except OSError as exc:
        LOGGER.warning("Tailscale verification handoff could not start: %s", exc)
        if on_human_verification:
            on_human_verification(None)
        return wait_for_human_verification_clear(is_required, wait_seconds)

    try:
        handoff.publish_frame(page.screenshot(type="png"))
        if on_human_verification:
            on_human_verification(handoff.url)

        def poll_required() -> bool:
            apply_handoff_actions(page, handoff.drain_actions())
            handoff.publish_frame(page.screenshot(type="png"))
            return is_required()

        return wait_for_human_verification_clear(poll_required, wait_seconds)
    finally:
        handoff.close()


def is_submission_confirmation(page_text: str) -> bool:
    return any(marker in page_text.casefold() for marker in SUBMISSION_CONFIRMATION_MARKERS)


def prepare_visible_submission(
    application_url: str,
    profile_root: Path,
    evidence: CareerEvidence,
    approved_attachment: Path,
    on_human_verification: Callable[[str | None], None] | None = None,
    wait_for_human_seconds: int = 0,
    form_answers: ApplicationFormAnswers | None = None,
    verification_handoff_host: str | None = None,
    verification_handoff_port: int = 0,
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
        _follow_verified_apply_link(page)
        if contains_human_verification(_page_and_frame_text(page), [frame.url for frame in page.frames]):
            # Keep the visible persistent browser alive while the candidate uses
            # the private handoff. No challenge is inspected, answered, or bypassed.
            if wait_for_page_verification_clear(
                page,
                wait_for_human_seconds,
                on_human_verification,
                verification_handoff_host,
                verification_handoff_port,
            ):
                return _fill_safe_fields(page, evidence, approved_attachment, form_answers)
            context.close()
            return BrowserPreparationResult(
                "awaiting_human_verification", (), "Human verification detected; complete it through Chrome Remote Desktop."
            )

        result = _fill_safe_fields(page, evidence, approved_attachment, form_answers)
        context.close()
    return result


def _fill_safe_fields(
    page,
    evidence: CareerEvidence,
    approved_attachment: Path,
    form_answers: ApplicationFormAnswers | None = None,
) -> BrowserPreparationResult:
    name_parts = evidence.identity.full_name.split(maxsplit=1)
    linkedin_url = next(
        (link for link in evidence.identity.links if "linkedin.com" in link.casefold()), ""
    )
    candidates = (
        (
            "first_name",
            'input[name="first_name" i], input[id="first_name" i], input[name="firstName" i], input[id="inputFirstName" i]',
            name_parts[0],
            "First Name",
        ),
        (
            "last_name",
            'input[name="last_name" i], input[id="last_name" i], input[name="lastName" i], input[id="inputLastName" i]',
            name_parts[1] if len(name_parts) > 1 else "",
            "Last Name",
        ),
        ("name", 'input[name="name" i], input[id="name" i]', evidence.identity.full_name, "Full Name"),
        ("email", 'input[type="email"], input[name="email" i], input[id="email" i]', evidence.identity.email, "Email"),
        ("phone", 'input[type="tel"], input[name="phone" i], input[id="phone" i]', evidence.identity.phone, "Phone"),
        ("location", 'input[name="location" i], input[id="location" i]', evidence.identity.location, "Location"),
        ("linkedin", 'input[name="linkedin" i], input[id="linkedin" i]', linkedin_url, "LinkedIn Profile"),
    )
    filled: list[str] = []
    scopes = _form_scopes(page)
    for scope in scopes:
        for field, selector, value, label in candidates:
            if not value:
                continue
            locator = scope.locator(selector).first
            if not locator.count() or not locator.is_visible():
                locator = scope.get_by_label(label, exact=True).first
            if locator.count() and locator.is_visible() and locator.input_value() == "":
                locator.fill(value)
                filled.append(field)

        upload = scope.locator(
            'input[type="file"][id*="resume" i], input[type="file"][name*="resume" i], '
            'input[type="file"][id*="cv" i], input[type="file"][name*="cv" i], '
            'input[type="file"]'
        ).first
        if upload.count():
            upload.set_input_files(str(approved_attachment))
            if upload.input_value():
                filled.append("cv_attachment")
                break
    if form_answers:
        for scope in scopes:
            filled.extend(_fill_candidate_confirmed_answers(scope, form_answers))
    unresolved = _unresolved_required_fields(page)
    if "cv_attachment" not in filled:
        unresolved = (*unresolved, "cv_attachment")
    message = "Prepared visible form without submitting it."
    if unresolved:
        message = "Prepared visible form, but some required fields still need candidate attention."
    return BrowserPreparationResult(
        "submission_prepared",
        tuple(filled),
        message,
        unresolved,
        page.url,
    )


def _fill_candidate_confirmed_answers(page, answers: ApplicationFormAnswers) -> list[str]:
    filled: list[str] = []
    for field_key, value in answers.field_values.items():
        locator = _candidate_answer_locator(page, field_key)
        if not locator.count() or not locator.is_visible():
            continue
        if locator.evaluate("element => element.tagName") == "SELECT":
            if locator.input_value():
                continue
            locator.select_option(label=value)
            filled.append(field_key)
            continue
        if locator.get_attribute("role") == "combobox":
            if _control_has_value(locator):
                continue
            if not _select_confirmed_combobox_option(page, locator, value):
                continue
        else:
            if locator.input_value():
                continue
            locator.fill(value)
        filled.append(field_key)
    for field_name, label in answers.checkbox_values.items():
        inputs = page.locator(f'input[type="checkbox"][name="{field_name}"], input[type="radio"][name="{field_name}"]')
        for index in range(inputs.count()):
            checkbox = inputs.nth(index)
            nearby_text = checkbox.evaluate(
                "element => element.closest('label')?.innerText || element.parentElement?.innerText || element.parentElement?.parentElement?.innerText || ''"
            )
            if label.casefold() in nearby_text.casefold() and not checkbox.is_checked():
                checkbox.check()
                filled.append(field_name)
                break
    return filled


def _candidate_answer_locator(page, field_key: str):
    """Find a candidate-approved control by its stable name or id attribute."""
    escaped_key = json.dumps(field_key)
    return page.locator(
        f"input[name={escaped_key}], input[id={escaped_key}], "
        f"textarea[name={escaped_key}], textarea[id={escaped_key}], "
        f"select[name={escaped_key}], select[id={escaped_key}], "
        f"input[aria-label={escaped_key}], textarea[aria-label={escaped_key}], "
        f"select[aria-label={escaped_key}], input[placeholder={escaped_key}], "
        f"textarea[placeholder={escaped_key}]"
    ).first


def _select_confirmed_combobox_option(page, locator, value: str) -> bool:
    """Select only a visible option that matches the candidate-confirmed value."""
    locator.click()
    locator.fill(value)
    page.wait_for_timeout(750)
    options = page.locator('[role="option"]:visible')
    for index in range(options.count()):
        option = options.nth(index)
        if _matches_confirmed_option(option.inner_text(), value):
            option.click()
            return True
    return False


def _matches_confirmed_option(option_text: str, value: str) -> bool:
    normalized_option = option_text.strip().casefold()
    normalized_value = value.strip().casefold()
    return normalized_option == normalized_value or normalized_option.startswith(f"{normalized_value} +")


def _unresolved_required_fields(page) -> tuple[str, ...]:
    """Return visible required controls still blank after conservative preparation."""
    unresolved: list[str] = []
    for scope in _form_scopes(page):
        required_controls = scope.locator(
            'input[aria-required="true"], textarea[aria-required="true"], select[aria-required="true"], '
            'input[required]:not([type="checkbox"]):not([type="radio"]), textarea[required], select[required]'
        )
        for index in range(required_controls.count()):
            control = required_controls.nth(index)
            label = _control_label(control)
            if not control.is_visible() or not label:
                continue
            if not _control_has_value(control):
                unresolved.append(label)

        required_choices = scope.locator('input[type="checkbox"][required], input[type="radio"][required]')
        checked_groups: set[str] = set()
        choice_groups: dict[str, list[object]] = {}
        for index in range(required_choices.count()):
            choice = required_choices.nth(index)
            if not choice.is_visible():
                continue
            key = choice.get_attribute("name") or choice.get_attribute("id") or f"choice-{index}"
            choice_groups.setdefault(key, []).append(choice)
            if choice.is_checked():
                checked_groups.add(key)
        for key, choices in choice_groups.items():
            if key not in checked_groups:
                unresolved.append(_control_label(choices[0]) or key)
    return tuple(dict.fromkeys(unresolved))


def _form_scopes(page) -> list[object]:
    frames = getattr(page, "frames", [])
    main_frame = getattr(page, "main_frame", None)
    return [page, *(frame for frame in frames if frame != main_frame)]


def _control_label(control) -> str | None:
    return (
        control.get_attribute("aria-label")
        or control.get_attribute("name")
        or control.get_attribute("id")
    )


def _control_has_value(control) -> bool:
    if control.input_value():
        return True
    if control.get_attribute("role") != "combobox":
        return False
    return bool(
        control.evaluate(
            "element => Boolean("
            "element.closest('[class*=\"value-container\"]')?.querySelector('[class*=\"single-value\"]')"
            ")"
        )
    )


def resolve_apply_now_url(current_url: str, href: str | None) -> str | None:
    """Return a safe public application destination from an official CTA href."""
    if not href:
        return None
    destination = urljoin(current_url, href)
    parsed = urlparse(destination)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return destination


def _follow_verified_apply_link(page) -> None:
    """Follow only an explicit opening apply CTA; never click a final submit control here."""
    links = page.locator("a")
    for index in range(links.count()):
        link = links.nth(index)
        if not link.is_visible() or link.inner_text().strip().casefold() != "apply now":
            continue
        destination = resolve_apply_now_url(page.url, link.get_attribute("href"))
        if destination and destination != page.url:
            page.goto(destination, wait_until="domcontentloaded")
            _wait_for_application_form(page)
        return
    buttons = page.locator("button")
    for index in range(buttons.count()):
        button = buttons.nth(index)
        label = button.inner_text().strip()
        if not button.is_visible() or not re.fullmatch(r"apply(?: for this job| now)?", label, flags=re.IGNORECASE):
            continue
        if button.evaluate("element => Boolean(element.closest('form'))"):
            continue
        button.click()
        _wait_for_application_form(page)
        return


def _wait_for_application_form(page, timeout_ms: int = 8_000) -> None:
    elapsed = 0
    while elapsed <= timeout_ms:
        for scope in _form_scopes(page):
            if scope.locator("input, textarea, select").count() > 0:
                return
        page.wait_for_timeout(250)
        elapsed += 250


def submit_visible_submission(
    application_url: str,
    profile_root: Path,
    evidence: CareerEvidence,
    approved_attachment: Path,
    on_human_verification: Callable[[str | None], None] | None = None,
    before_submit: Callable[[], None] | None = None,
    form_answers: ApplicationFormAnswers | None = None,
    wait_for_human_seconds: int = 0,
    proof_path: Path | None = None,
    verification_handoff_host: str | None = None,
    verification_handoff_port: int = 0,
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
        _follow_verified_apply_link(page)
        verification_present = contains_human_verification(
            _page_and_frame_text(page), [frame.url for frame in page.frames]
        )
        if verification_present and not wait_for_page_verification_clear(
            page,
            wait_for_human_seconds,
            on_human_verification,
            verification_handoff_host,
            verification_handoff_port,
        ):
            context.close()
            return BrowserPreparationResult(
                "awaiting_human_verification",
                (),
                "Human verification is still required.",
            )
        prepared = _fill_safe_fields(page, evidence, approved_attachment, form_answers)
        if prepared.unresolved_required_fields:
            context.close()
            return BrowserPreparationResult(
                "submission_prepared",
                prepared.filled_fields,
                "Required fields are unresolved; the portal was not submitted.",
                prepared.unresolved_required_fields,
                page.url,
            )
        submit = _find_unambiguous_submit_control(page)
        if not submit.count() or not submit.is_visible():
            context.close()
            return BrowserPreparationResult(
                "submission_prepared",
                prepared.filled_fields,
                "No unambiguous submit control was found; the application remains prepared.",
            )
        if before_submit:
            before_submit()
        submit.click()
        page.wait_for_timeout(5_000)
        body = _page_and_frame_text(page)
        if contains_human_verification(body, [frame.url for frame in page.frames]):
            wait_for_page_verification_clear(
                page,
                wait_for_human_seconds,
                on_human_verification,
                verification_handoff_host,
                verification_handoff_port,
            )
            body = _page_and_frame_text(page)
            if not contains_human_verification(body, [frame.url for frame in page.frames]):
                if is_submission_confirmation(body):
                    screenshot_path, screenshot_sha256 = _capture_submission_screenshot(page, proof_path)
                    context.close()
                    return BrowserPreparationResult(
                        "submitted",
                        prepared.filled_fields,
                        "Portal confirmed that the application was submitted after human verification.",
                        screenshot_path=screenshot_path,
                        screenshot_sha256=screenshot_sha256,
                    )
                context.close()
                return BrowserPreparationResult(
                    "submission_prepared",
                    prepared.filled_fields,
                    "Human verification completed, but no portal confirmation appeared. "
                    "Request a fresh final approval before another submit click.",
                )
            context.close()
            return BrowserPreparationResult(
                "awaiting_human_verification", prepared.filled_fields, "Human verification appeared after submit."
            )
        if is_submission_confirmation(body):
            screenshot_path, screenshot_sha256 = _capture_submission_screenshot(page, proof_path)
            context.close()
            return BrowserPreparationResult(
                "submitted",
                prepared.filled_fields,
                "Portal confirmed that the application was submitted.",
                screenshot_path=screenshot_path,
                screenshot_sha256=screenshot_sha256,
            )
        context.close()
    return BrowserPreparationResult(
        "submission_prepared",
        prepared.filled_fields,
        "Submit was clicked but no confirmation page was detected; submission was not recorded.",
    )


def _find_unambiguous_submit_control(page):
    for scope in _form_scopes(page):
        submit = scope.locator('button[type="submit"], input[type="submit"]').first
        if submit.count() and submit.is_visible():
            return submit
        buttons = scope.locator("button")
        matches = []
        for index in range(buttons.count()):
            button = buttons.nth(index)
            if button.is_visible() and is_explicit_apply_now_label(button.inner_text()):
                matches.append(button)
        if len(matches) == 1:
            return matches[0]
    return page.locator("button").filter(has_text="__backend_scout_no_submit_match__").first


def _capture_submission_screenshot(page, proof_path: Path | None) -> tuple[str | None, str | None]:
    if proof_path is None:
        return None, None
    proof_path.parent.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(proof_path), full_page=True)
    digest = hashlib.sha256(proof_path.read_bytes()).hexdigest()
    return str(proof_path.resolve()), digest


def _page_and_frame_text(page) -> str:
    texts: list[str] = []
    for scope in _form_scopes(page):
        try:
            texts.append(scope.locator("body").inner_text(timeout=2_000))
        except Exception:
            LOGGER.debug("Could not read text from a page/frame while checking submission state.", exc_info=True)
            continue
    return "\n".join(texts)
