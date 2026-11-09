"""Conservative delivery of an already-approved immutable portal package.

``PackagePortalAdapter(manager, settings)(package, cv_path,
attempt_id) -> BrowserPreparationResult`` is the application-service callable.
The service owns authenticated approval, durable attempt claiming and restart
reconciliation. This adapter never loads legacy answer files or calls a model.
Identity comes only from the immutable reviewed package, never mutable evidence
at delivery time. Ambiguous/sensitive answers must match explicit package answer keys (a
visible label, HTML name or ID); values are never inferred from a CV.

Every acquired session stays open, including on input requests and failures.
Only an exact final submit in one verified form can be clicked, at most once per
application per adapter instance. After that boundary, any uncertainty is
submission_unknown, never an automatic retry. Proofs are private PNG files at
``settings.cv_archive_root.resolve().parent/submission_proofs/<attempt_id>.png``.
"""

import hashlib
import io
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qsl, urljoin, urlsplit

from PIL import Image

from backend_scout.application_package import ApplicationPackage
from backend_scout.config import Settings, TrackerName
from backend_scout.guided_submission import (
    GuidedControlKind,
    is_human_verification_field,
    is_sensitive_form_field,
)
from backend_scout.portal_adapters import PortalAdapter, adapter_for_url
from backend_scout.portal_sessions import PortalSessionError, PortalSessionManager
from backend_scout.submission import (
    BrowserPreparationResult,
    _fill_control_if_blank,
    _form_scopes,
    capture_guided_form,
    contains_human_verification,
    portal_authentication_required,
)

__all__ = ["PackagePortalAdapter"]

_IDENTITY_KEYS = {
    "name": "identity.full_name",
    "full name": "identity.full_name",
    "first name": "identity.first_name",
    "given name": "identity.first_name",
    "last name": "identity.last_name",
    "family name": "identity.last_name",
    "surname": "identity.last_name",
    "email": "identity.email",
    "email address": "identity.email",
    "e mail": "identity.email",
    "phone": "identity.phone",
    "phone number": "identity.phone",
    "mobile": "identity.phone",
    "location": "identity.location",
    "current location": "identity.location",
    "city": "identity.location",
    "linkedin": "identity.linkedin",
    "linkedin profile": "identity.linkedin",
    "linkedin url": "identity.linkedin",
    "linkedin profile url": "identity.linkedin",
}
_FINAL_LABELS = {"submit application", "send application", "apply now", "שליחת הבקשה"}
_ATTEMPT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")

_FILE_HASHES = """async element => {
    return Promise.all(Array.from(element.files || []).map(async file => {
        const digest = await crypto.subtle.digest('SHA-256', await file.arrayBuffer());
        return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('');
    }));
}"""
_BUTTON_METADATA = """element => ({
    label: element.innerText || element.value || element.getAttribute('aria-label') || '',
    type: element.type,
    form_index: element.form ? Array.from(document.forms).indexOf(element.form) : -1,
    action: element.getAttribute('formaction') || element.form?.getAttribute('action') || '',
    target: element.getAttribute('formtarget') || element.form?.getAttribute('target') || '',
    invalid: Array.from(element.form?.elements || [])
        .filter(e => e.willValidate && !e.validity.valid)
        .map(e => e.labels?.[0]?.innerText || e.getAttribute('aria-label')
            || e.name || e.id || 'Unlabelled required field')
})"""
_REQUIRED_METADATA = """elements => elements.filter(e =>
    !e.disabled && (e.type === 'file' || Boolean(e.getClientRects().length))
).map(e => ({
    label: e.labels?.[0]?.innerText || e.getAttribute('aria-label')
        || e.getAttribute('placeholder') || e.name || e.id || 'Unlabelled required field',
    invalid: Boolean(e.willValidate && !e.validity.valid)
}))"""


@dataclass
class _Upload:
    control: object
    scope: int
    form_index: int


class PackagePortalAdapter:
    def __init__(self, manager: PortalSessionManager, settings: Settings) -> None:
        self.manager = manager
        self.settings = settings
        self._attempted: set[tuple[TrackerName, str]] = set()

    def __call__(
        self, package: ApplicationPackage, cv_path: Path, attempt_id: str
    ) -> BrowserPreparationResult:
        if self.settings.portal_delivery_mode == "hybrid":
            from backend_scout.hybrid_portal import HybridPortalAdapter

            return HybridPortalAdapter(self.manager, self.settings)(package, cv_path, attempt_id)
        page = None
        filled: list[str] = []
        clicked = False
        adapter = None

        def result(state: str, message: str, *missing: str, **proof) -> BrowserPreparationResult:
            return BrowserPreparationResult(
                state,
                tuple(dict.fromkeys(filled)),
                message,
                tuple(dict.fromkeys(missing)),
                page.url if page is not None else None,
                adapter_name=adapter.name if adapter else None,
                **proof,
            )

        try:
            if package.delivery_route != "portal":
                return result(
                    "needs_input", "This adapter only supports portal delivery.", "delivery_route"
                )
            key = (package.tracker, package.application_id)
            if key in self._attempted:
                return result(
                    "submission_unknown",
                    "A submit was already attempted; reconcile before retrying.",
                )
            adapter = adapter_for_url(package.official_destination, tracker=package.tracker)
            if adapter is None:
                return result(
                    "needs_input",
                    "The official portal has no supported adapter.",
                    "supported_portal_adapter",
                )
            proof_path = self._proof_path(attempt_id)
            if proof_path.exists() or proof_path.is_symlink():
                return result(
                    "submission_unknown",
                    "This attempt already has proof storage; reconcile before retrying.",
                )
            cv_path = Path(cv_path).expanduser().resolve(strict=True)
            cv_bytes = self._cv_bytes(cv_path, package.cv_sha256)
            answers = json.dumps(
                [answer.model_dump() for answer in package.answers],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            binding = (
                package.application_id,
                package.tracker,
                package.official_destination,
                package.cv_sha256,
                hashlib.sha256(answers.encode("utf-8")).hexdigest(),
            )
            try:
                session = self.manager.acquire(*binding)
            except PortalSessionError as exc:
                if exc.reason != "binding_changed":
                    raise
                # The service has already approved this changed package. Never
                # reuse a form still holding a different CV or reviewed answers.
                self.manager.close_session(package.application_id, package.tracker)
                session = self.manager.acquire(*binding)
            page = session.page
            for landing_pass in range(2):
                if not _allowed(page.url, adapter, package.tracker) or not _same_job(
                    page.url, package.official_destination
                ):
                    return result(
                        "needs_input",
                        "The browser left the supported official portal.",
                        "application_url",
                    )
                view = _trusted_view(page, adapter, package.tracker, package.official_destination)
                if _human_check(page, view):
                    return result(
                        "awaiting_human_verification",
                        "Complete the human check in the open browser.",
                    )
                if any(portal_authentication_required(scope) for scope in _form_scopes(view)):
                    return result(
                        "needs_input",
                        "Sign in using the open portal browser.",
                        "portal_authentication",
                    )
                if _receipts(view, adapter):
                    return result(
                        "submission_unknown",
                        "A receipt was already visible before this attempt; reconcile it first.",
                    )
                if _foreign_form(page, view):
                    return result(
                        "needs_input",
                        "An embedded form is outside the supported portal.",
                        "supported_portal_adapter",
                    )
                if capture_guided_form(view).snapshot.fields:
                    break
                if landing_pass or not _follow_landing(page, adapter, package):
                    return result(
                        "needs_input",
                        "No supported application form is visible at the official entry.",
                        "application_url",
                    )

            identity = {
                answer.key: answer.value
                for answer in package.answers
                if answer.key.startswith("identity.")
            }
            _prepare_fields(view, package, identity, filled, fill=True)
            upload = _attach_cv(view, cv_path.name, cv_bytes, package.cv_sha256, filled)
            gaps = []
            if upload is None:
                gaps.append("cv_attachment")
            # Reacquire React-rendered controls and audit every value, not just new fills.
            _, current_gaps = _prepare_fields(view, package, identity, filled, fill=False)
            gaps.extend(current_gaps)
            if gaps:
                return result(
                    "needs_input",
                    "Review the labelled fields in the open browser; nothing was submitted.",
                    *gaps,
                )

            submit, invalid = _unique_submit(
                view, adapter, package.tracker, upload, package.official_destination
            )
            if invalid:
                return result(
                    "needs_input",
                    "The application form is incomplete or its final action is ambiguous.",
                    *invalid,
                )
            # Approval bytes, upload and current origin are checked at the final boundary.
            self._cv_bytes(cv_path, package.cv_sha256)
            if (
                _attach_cv(view, cv_path.name, cv_bytes, package.cv_sha256, filled, attach=False)
                is None
            ):
                return result(
                    "needs_input",
                    "The exact approved CV attachment cannot be verified.",
                    "cv_attachment",
                )
            if not _allowed(page.url, adapter, package.tracker) or not _same_job(
                page.url, package.official_destination
            ):
                return result(
                    "needs_input",
                    "The portal destination changed before submit.",
                    "application_url",
                )
            if _human_check(page, view):
                return result(
                    "awaiting_human_verification", "Complete the human check in the open browser."
                )
            if any(portal_authentication_required(scope) for scope in _form_scopes(view)):
                return result(
                    "needs_input", "Sign in using the open portal browser.", "portal_authentication"
                )
            if _receipts(view, adapter):
                return result(
                    "submission_unknown",
                    "A receipt appeared before the submit boundary; reconcile it first.",
                )
            self._attempted.add(key)
            clicked = True  # A click that times out might still have transmitted.
            submit.click(timeout=5_000)
            for _ in range(20):
                page.wait_for_timeout(250)
                if not _allowed(page.url, adapter, package.tracker):
                    return result(
                        "submission_unknown",
                        "The portal left its supported host after submit; reconcile the result.",
                    )
                view = _trusted_view(page, adapter, package.tracker, package.official_destination)
                if _human_check(page, view):
                    return result(
                        "submission_unknown",
                        "A human check appeared after submit; reconcile before retrying.",
                    )
                if any(portal_authentication_required(scope) for scope in _form_scopes(view)):
                    return result(
                        "submission_unknown",
                        "Sign-in appeared after submit; reconcile before retrying.",
                    )
                if _receipts(view, adapter):
                    page.bring_to_front()
                    page.evaluate(
                        "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
                    )
                    raw = page.screenshot(type="png", full_page=True, animations="disabled")
                    _validate_png(raw)
                    if not _allowed(page.url, adapter, package.tracker) or not _receipts(
                        _trusted_view(page, adapter, package.tracker, package.official_destination),
                        adapter,
                    ):
                        return result(
                            "submission_unknown",
                            "The visible receipt changed while capturing proof.",
                        )
                    _save_proof(proof_path, raw)
                    return result(
                        "submitted",
                        "The portal displayed a new receipt; private screenshot proof was saved.",
                        screenshot_path=str(proof_path),
                        screenshot_sha256=hashlib.sha256(raw).hexdigest(),
                    )
            return result(
                "submission_unknown",
                "Submit was attempted without a new visible receipt; reconcile before retrying.",
            )
        except PortalSessionError as exc:
            return result(
                "needs_input",
                "The portal session needs attention; no submit was attempted.",
                exc.reason,
            )
        except Exception:  # noqa: BLE001 - fail closed before click, uncertain after any click attempt.
            if clicked:
                return result(
                    "submission_unknown",
                    "The portal attempt was interrupted; reconcile before retrying.",
                )
            return result(
                "needs_input",
                "The approved portal package could not be safely prepared.",
                "portal_preparation",
            )

    def _cv_bytes(self, path: Path, expected: str) -> bytes:
        if not path.is_relative_to(self.settings.cv_archive_root.expanduser().resolve()):
            raise ValueError("CV is outside the private archive")
        data = path.read_bytes()
        if path.suffix.casefold() != ".pdf" or not data.startswith(b"%PDF-"):
            raise ValueError("The approved attachment must be a PDF")
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError("The approved CV bytes changed")
        return data

    def _proof_path(self, attempt_id: str) -> Path:
        if not isinstance(attempt_id, str) or not _ATTEMPT_ID.fullmatch(attempt_id):
            raise ValueError("Invalid attempt ID")
        directory = (
            self.settings.cv_archive_root.expanduser().resolve().parent / "submission_proofs"
        )
        if directory.is_symlink():
            raise ValueError("Proof directory must not be a symlink")
        if directory.exists() and stat.S_IMODE(directory.stat().st_mode) & 0o077:
            raise ValueError("Proof directory must be private")
        return directory / f"{attempt_id}.png"


def _normal(value: str) -> str:
    return " ".join(value.strip().rstrip("*").casefold().split())


def _identifier(value: str) -> str:
    return _normal(re.sub(r"([a-z])([A-Z])", r"\1 \2", value).replace("_", " ").replace("-", " "))


def _allowed(url: str, adapter: PortalAdapter, tracker: TrackerName) -> bool:
    try:
        parsed = urlsplit(url)
        selected = adapter_for_url(url, tracker=tracker)
        return bool(
            parsed.scheme == "https"
            and parsed.port in {None, 443}
            and selected is not None
            and selected.name == adapter.name
        )
    except ValueError:
        return False


def _same_job(current: str, approved: str) -> bool:
    current_url, approved_url = urlsplit(current), urlsplit(approved)
    # Query-based vacancies share a path; tracking parameters are not identity.
    identity_keys = {
        "jobid",
        "job_id",
        "positionid",
        "position_id",
        "position_uid",
        "uid",
        "gh_jid",
    }
    identities = [
        [
            (key.casefold(), value)
            for key, value in parse_qsl(url.query, keep_blank_values=True)
            if key.casefold() in identity_keys
        ]
        for url in (current_url, approved_url)
    ]
    if any(len(items) > 1 or any(not value.strip() for _, value in items) for items in identities):
        return False
    if identities[0] != identities[1]:
        return False
    base = approved_url.path.rstrip("/")
    for suffix in ("/application", "/apply"):
        if base.endswith(suffix):
            base = base.removesuffix(suffix)
    return (
        bool(base)
        and current_url.hostname == approved_url.hostname
        and (current_url.path.rstrip("/") == base or current_url.path.startswith(base + "/"))
    )


def _trusted_view(page, adapter: PortalAdapter, tracker: TrackerName, destination: str):
    return SimpleNamespace(
        url=page.url,
        main_frame=page.main_frame,
        locator=page.locator,
        frames=[
            frame
            for frame in page.frames
            if _allowed(frame.url, adapter, tracker) and _same_job(frame.url, destination)
        ],
    )


def _human_check(page, view) -> bool:
    return contains_human_verification(
        "\n".join(scope.locator("body").inner_text(timeout=2_000) for scope in _form_scopes(view)),
        [frame.url for frame in page.frames],
    )


def _receipts(view, adapter: PortalAdapter) -> set[str]:
    text = "\n".join(
        scope.locator("body").inner_text(timeout=2_000) for scope in _form_scopes(view)
    ).casefold()
    return {marker for marker in adapter.confirmation_markers if marker in text}


def _foreign_form(page, view) -> bool:
    for frame in page.frames:
        if frame == page.main_frame or frame in view.frames:
            continue
        controls = frame.locator("input, textarea, select")
        if any(controls.nth(index).is_visible() for index in range(controls.count())):
            return True
    return False


def _follow_landing(page, adapter: PortalAdapter, package: ApplicationPackage) -> bool:
    entries = []
    links = page.locator("a")
    approved = urlsplit(package.official_destination)
    base = approved.path.rstrip("/")
    for index in range(links.count()):
        link = links.nth(index)
        if not link.is_visible() or _normal(link.inner_text()) not in {
            "apply",
            "apply now",
            "apply for this job",
        }:
            continue
        href = link.get_attribute("href")
        if not href:
            continue
        destination = urljoin(page.url, href)
        parsed = urlsplit(destination)
        if (
            _allowed(destination, adapter, package.tracker)
            and parsed.hostname == approved.hostname
            and parsed.path.rstrip("/") in {f"{base}/application", f"{base}/apply"}
        ):
            entries.append(destination)
    if len(set(entries)) != 1:
        return False
    page.goto(entries[0], wait_until="domcontentloaded")
    return True


def _field_keys(field, control) -> list[str]:
    return [field.label] + [
        value for key in ("name", "id", "aria-label") if (value := control.get_attribute(key))
    ]


def _prepare_fields(view, package, identity, filled: list[str], *, fill: bool):
    captured = capture_guided_form(view)
    answers: dict[str, list[str]] = {}
    for answer in package.answers:
        answers.setdefault(_normal(answer.key), []).append(answer.value)
    fields = captured.snapshot.fields
    gaps = []
    radio_groups: dict[tuple[str, str], list] = {}
    for field in fields:
        control = captured.controls[field.field_id]
        if field.kind == GuidedControlKind.FILE:
            continue
        keys = _field_keys(field, control)
        values = {value for key in keys for value in answers.get(_normal(key), [])}
        sensitive = field.sensitive or any(
            is_sensitive_form_field(_identifier(key)) for key in keys
        )
        expected = next(iter(values)) if len(values) == 1 else None
        if len(values) > 1:
            gaps.append(field.label)
            continue
        if expected is None and not sensitive:
            fact_keys = {
                _IDENTITY_KEYS[_identifier(key)]
                for key in keys
                if _identifier(key) in _IDENTITY_KEYS
            }
            if len(fact_keys) == 1:
                expected = identity.get(next(iter(fact_keys)))
        if field.kind == GuidedControlKind.RADIO:
            group = (
                field.field_id.split("control-")[0],
                control.get_attribute("name") or field.field_id,
            )
            radio_groups.setdefault(group, []).append((field, control, expected))
            continue
        if expected is None:
            if field.required or field.current_value_present:
                gaps.append(field.label)
            continue
        if is_human_verification_field(field.label):
            gaps.append(field.label)
            continue
        if field.kind == GuidedControlKind.COMBOBOX:
            gaps.append(field.label)
            continue  # Custom dropdown semantics need a verified portal-specific implementation.
        if field.kind == GuidedControlKind.CHECKBOX:
            desired = _normal(expected) in {"yes", "true", _normal(field.label)}
            if not desired and _normal(expected) not in {"no", "false"}:
                gaps.append(field.label)
                continue
            if fill and desired and not control.is_checked():
                control.check(timeout=5_000)
                filled.append(field.label)
            if control.is_checked() != desired or (field.required and not desired):
                gaps.append(field.label)
            continue
        if field.kind == GuidedControlKind.SELECT:
            selected = control.evaluate("element => element.selectedOptions?.[0]?.label || ''")
            if fill and not field.current_value_present and expected in field.options:
                control.select_option(label=expected, timeout=5_000)
                filled.append(field.label)
                selected = control.evaluate("element => element.selectedOptions?.[0]?.label || ''")
            if selected != expected:
                gaps.append(field.label)
        else:
            if fill and _fill_control_if_blank(control, expected):
                filled.append(field.label)
            if control.input_value(timeout=2_000) != expected:
                gaps.append(field.label)
    for choices in radio_groups.values():
        approved = {expected for _, _, expected in choices if expected is not None}
        question = choices[0][1].get_attribute("name") or choices[0][0].label
        if len(approved) != 1:
            if any(field.required or field.current_value_present for field, _, _ in choices):
                gaps.append(question)
            continue
        expected = next(iter(approved))
        matches = [
            control
            for field, control, _ in choices
            if expected in {field.label, control.get_attribute("value")}
        ]
        if len(matches) != 1:
            gaps.append(question)
            continue
        target = matches[0]
        checked = [control for _, control, _ in choices if control.is_checked()]
        if fill and not checked:
            target.check(timeout=5_000)
            filled.append(question)
        if not target.is_checked() or any(
            control is not target and control.is_checked() for _, control, _ in choices
        ):
            gaps.append(question)
    labels = {_normal(field.label) for field in fields}
    for scope in _form_scopes(view):
        required = scope.locator('[required], [aria-required="true"]').evaluate_all(
            _REQUIRED_METADATA
        )
        for item in required:
            if item["invalid"] or _normal(item["label"]) not in labels:
                gaps.append(item["label"])
    return captured, gaps


def _attach_cv(
    view, name: str, data: bytes, digest: str, filled, *, attach: bool = True
) -> _Upload | None:
    captured = capture_guided_form(view)
    files = []
    scopes = _form_scopes(view)
    for field in captured.snapshot.fields:
        if field.kind != GuidedControlKind.FILE:
            continue
        control = captured.controls[field.field_id]
        label = " ".join(_field_keys(field, control)).casefold()
        if "cover" not in label and re.search(r"\b(cv|resume|curriculum vitae)\b", label):
            files.append((field, control))
    if len(files) != 1:
        return None
    field, control = files[0]
    hashes = control.evaluate(_FILE_HASHES)
    if not hashes and attach:
        control.set_input_files(
            {"name": name, "mimeType": "application/pdf", "buffer": data}, timeout=5_000
        )
        filled.append("cv_attachment")
        captured = capture_guided_form(view)
        control = captured.controls.get(field.field_id)
        if control is None:
            return None
        hashes = control.evaluate(_FILE_HASHES)
    if hashes != [digest]:
        return None
    scope_index = int(field.field_id.split("-")[1])
    if scope_index >= len(scopes):
        return None
    form_index = control.evaluate(
        "element => element.form ? Array.from(document.forms).indexOf(element.form) : -1"
    )
    return _Upload(control, scope_index, form_index)


def _unique_submit(view, adapter, tracker, upload: _Upload, destination: str):
    candidates = []
    for scope_index, scope in enumerate(_form_scopes(view)):
        buttons = scope.locator(", ".join(adapter.submit_selectors))
        for index in range(buttons.count()):
            button = buttons.nth(index)
            if not button.is_visible() or not button.is_enabled():
                continue
            metadata = button.evaluate(_BUTTON_METADATA)
            if _normal(metadata["label"]) not in _FINAL_LABELS or metadata["type"] != "submit":
                continue
            candidates.append((button, metadata, scope_index))
    if len(candidates) != 1:
        return None, ["final_submit_control"]
    button, metadata, scope_index = candidates[0]
    if (
        metadata["form_index"] < 0
        or metadata["form_index"] != upload.form_index
        or scope_index != upload.scope
        or metadata["target"] not in {"", "_self"}
        or not _allowed(urljoin(view.url, metadata["action"]), adapter, tracker)
        or not _same_job(urljoin(view.url, metadata["action"]), destination)
    ):
        return None, ["final_submit_control"]
    return button, metadata["invalid"]


def _validate_png(raw: bytes) -> None:
    with Image.open(io.BytesIO(raw)) as picture:
        if picture.format != "PNG" or min(picture.size) < 16:
            raise ValueError("Receipt proof is not a painted PNG")
        rgba = picture.convert("RGBA")
        painted = Image.alpha_composite(Image.new("RGBA", picture.size, "white"), rgba).convert(
            "RGB"
        )
        if all(low == high for low, high in painted.getextrema()):
            raise ValueError("Receipt proof is blank")


def _save_proof(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, exist_ok=True)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if stat.S_IMODE(os.fstat(directory).st_mode) & 0o077:
            raise ValueError("Receipt proof directory is not private")
        descriptor = os.open(
            path.name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
            dir_fd=directory,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(directory)
