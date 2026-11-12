"""Employer-first delivery inside the existing package/attempt transaction boundary.

The model can propose mappings, not values, uploads, destinations, consent or
submission. Browser actions and paid usage are reserved by deterministic code.
No exception after the one submit click is permission to replay that click.
"""

import hashlib
import json
import re
import time
import traceback
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError

from backend_scout.application_package import ReviewedAnswer
from backend_scout.application_routes import (
    ApplicationRoute,
    resolve_application_route,
    route_allows_url,
    safe_public_url,
)
from backend_scout.application_store import ApplicationStore
from backend_scout.browser_budget import BudgetExceeded
from backend_scout.budget_config import configured_budget
from backend_scout.config import TrackerName
from backend_scout.employer_forms import (
    find_application_form,
    form_review_signature,
    lever_choice_groups,
    prepare_reviewed_fields,
    proposed_notes,
    question,
    question_key,
    unique_submit,
)
from backend_scout.guided_submission import (
    GUIDED_FORM_INSTRUCTIONS,
    GuidedCandidateFact,
    GuidedFactDescriptor,
    GuidedFactKind,
    GuidedFormPlan,
    guided_planner_payload,
    validate_guided_form_plan,
)
from backend_scout.package_portal import (
    _BUTTON_METADATA,
    _FILE_HASHES,
    PackagePortalAdapter,
    _save_proof,
    _validate_png,
)
from backend_scout.portal_sessions import PortalSessionError
from backend_scout.submission import (
    BrowserPreparationResult,
    capture_guided_form,
    contains_human_verification,
)

RECEIPTS = (
    "your application has been submitted", "application received",
    "thank you for applying", "application successfully submitted",
    "your form has been successfully submitted", "קיבלנו את קורות החיים",
    "ההודעה נשלחה", "מועמדותך התקבלה",
)


def application_endpoint(url):
    return not re.search(r"/(?:contact(?:-us)?|newsletter|subscribe)(?:/|$)",
                         urlsplit(url).path, re.IGNORECASE)


def verification_resource(url, tracker):
    """Rendering/human handoff only; these origins never receive candidate facts."""
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    known = ((host in {"www.google.com", "www.recaptcha.net", "recaptcha.net"}
              and parsed.path.startswith("/recaptcha/"))
             or (host == "hcaptcha.com" or host.endswith(".hcaptcha.com"))
             or host == "challenges.cloudflare.com")
    return bool(known and parsed.scheme == "https" and not parsed.username
                and not parsed.password and parsed.port in {None, 443}
                and safe_public_url("https://" + host + "/", tracker))


def visible_receipts(page):
    """inner_text/locator visibility excludes hidden templates and script strings."""
    found = []
    for frame in page.frames:
        try:
            text = frame.locator("body").inner_text(timeout=1500).casefold()
            found.extend((frame, marker) for marker in RECEIPTS if marker in text)
        except PlaywrightError:
            pass  # Detached frames cannot establish a visible receipt.
    return found


def painted_receipt(control):
    return control.evaluate("""el => {
        for (let p=el;p;p=p.parentElement) {
            const s=getComputedStyle(p);
            if (s.display==='none'||s.visibility==='hidden'||Number(s.opacity)===0) return false;
        }
        const r=el.getBoundingClientRect();
        const x=Math.max(0,Math.min(innerWidth-1,r.left+r.width/2));
        const y=Math.max(0,Math.min(innerHeight-1,r.top+r.height/2));
        const top=document.elementFromPoint(x,y);
        return r.width>0&&r.height>0&&top&&(el.contains(top)||top===el);
    }""")


def human_required(page, tracker):
    responses = page.locator('textarea[name="g-recaptcha-response"],textarea[name="h-captcha-response"],input[name="cf-turnstile-response"]')
    if responses.count() and responses.evaluate_all("els => els.every(el => Boolean(el.value))"):
        return False
    frames = []
    for frame in page.frames:
        if (frame != page.main_frame and verification_resource(frame.url, tracker)
            and frame.frame_element().is_visible()):
            frames.append(frame.url)
    return contains_human_verification(page.locator("body").inner_text(), frames)


class HybridPortalAdapter:
    def __init__(self, manager, settings, *, store=None, planner=None):
        self.manager, self.settings, self.planner = manager, settings, planner
        self.store = store or ApplicationStore(
            settings.workflow_database_path, settings.cv_archive_root.resolve().parent
        )
        self.budget = configured_budget(settings)
        self._guards = {}

    def inspect(self, job, tracker):
        """Public read-only preflight. Uses a separate, disposable owned session."""
        tracker = TrackerName(tracker)
        entry = job.application_url or job.source_url
        if not safe_public_url(entry, tracker):
            return {"route": ApplicationRoute(
                status="needs_input", reason_code="unsafe_url",
                message="A public HTTPS posting is required.",
                source_url=job.source_url, company=job.company, title=job.title,
            ).model_dump(mode="json"), "questions": [], "proposed_answers": {}}
        identifier = "preflight-" + hashlib.sha256(
            (entry + job.company + job.title).encode()
        ).hexdigest()[:24]
        session = self.manager.acquire(identifier, tracker, entry, "0" * 64,
                                       "public-read-only", navigate=False)
        try:
            from backend_scout.yaml_files import load_yaml_mapping

            aliases = Path("config/employer_domains.private.yaml")
            hosts = ()
            if aliases.is_file():
                data = load_yaml_mapping(aliases)
                configured = data.get("companies", {}).get(job.company, {}).get("hosts", [])
                if isinstance(configured, list) and all(
                    isinstance(h, str) and re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", h)
                    for h in configured
                ):
                    hosts = tuple(configured)
            route = resolve_application_route(job, session.page, tracker=tracker,
                                              verified_employer_hosts=hosts)
            view = (find_application_form(session.page, route)
                    if route.status == "verified" and route.reason_code != "greenhouse_embed_manual" else None)
            questions, proposals, fields = [], {}, []
            if view:
                from backend_scout.cv_service import load_career_evidence

                captured = capture_guided_form(view)
                fields = [field.model_dump(mode="json") for field in captured.snapshot.fields]
                radio_questions = {}
                checkbox_groups = lever_choice_groups(captured)
                for field in captured.snapshot.fields:
                    if field.kind.value != "file":
                        control = captured.controls[field.field_id]
                        item = question(field, control)
                        if field.kind.value == "radio" or item["key"] in checkbox_groups:
                            group = radio_questions.setdefault(item["key"], item | {"options": []})
                            group["options"].append(field.label)
                            group["required"] |= item["required"]
                            group["sensitive"] |= item["sensitive"]
                        else:
                            questions.append(item)
                questions.extend(radio_questions.values())
                with suppress(OSError, ValueError):
                    proposals = proposed_notes(load_career_evidence(), job, view)
                proposals["review.form_schema_sha256"] = form_review_signature(view)
            return {"route": route.model_dump(mode="json"), "questions": questions,
                    "fields": fields, "proposed_answers": proposals,
                    "form_identified": view is not None,
                    "posting_text": route.posting_text}
        finally:
            self.manager.close_session(identifier, tracker)

    def _guard(self, session, route):
        """Keep navigation and any candidate-bearing request on observed origins."""
        page, context = session.page, session.context
        prior = getattr(context, "_backendscout_guard", None)
        if prior is not None:
            context.unroute("**/*", prior)
        policy = getattr(context, "_backendscout_policy", None)
        if policy is None:
            policy = {"stage": "read", "assets": set(), "writes": 0}
            context._backendscout_policy = policy
        if getattr(page, "url", "about:blank") != "about:blank":
            policy["stage"] = "fill"

        def guard(intercept):
            request = intercept.request
            url = request.url
            origin = "https://" + (urlsplit(url).hostname or "")
            read = request.method in {"GET", "HEAD"}
            navigation = request.is_navigation_request()
            official = (origin in route.allowed_origins and application_endpoint(url)
                        and route_allows_url(route, url, confirmation=True))
            verification = verification_resource(url, route._tracker)
            allowed = official and read
            if not read:
                allowance = 2 if policy["stage"] == "upload" else 1
                allowed = (official and policy["stage"] in {"upload", "send"}
                           and policy["writes"] < allowance)
                if allowed:
                    policy["writes"] += 1
            if verification:
                allowed = read or policy["stage"] == "human"
            if not allowed and read and not navigation:
                static = getattr(request, "resource_type", "") in {"script", "stylesheet", "image", "font", "media"}
                if static and policy["stage"] == "read" and safe_public_url(url, route._tracker):
                    policy["assets"].add(url)
                allowed = static and url in policy["assets"]
            if allowed:
                intercept.fallback()
            else:
                intercept.abort()

        context.route("**/*", guard)
        context._backendscout_guard = guard
        self._guards[id(context)] = guard
        return page

    def _map(self, captured, package):
        """Only field IDs and fact descriptors reach the optional paid mapper."""
        facts = {}
        for answer in package.answers:
            if answer.key.startswith("identity."):
                facts[answer.key] = GuidedCandidateFact(GuidedFactDescriptor(
                    key=answer.key, description=answer.key, kind=GuidedFactKind.TEXT,
                ), answer.value)
        payload = guided_planner_payload(captured.snapshot, facts)
        content = json.dumps(payload, ensure_ascii=False)
        if self.planner is None and not self.settings.openai_api_key:
            raise BudgetExceeded("model_key_missing")
        # UTF-8 bytes upper-bound ordinary token count; include schema/instructions.
        input_cap = len(content.encode()) + len(GUIDED_FORM_INSTRUCTIONS.encode()) + 4096
        if input_cap > 40_000:
            raise BudgetExceeded("model_input_limit")
        reservation = self.budget.reserve(
            package.application_id, package.tracker, self.settings.openai_model_fast,
            input_cap, 1000,
        )
        if self.planner is not None:
            plan, usage = self.planner(payload)
        else:
            from openai import OpenAI

            from backend_scout.cv_tailoring import openai_json_schema

            with OpenAI(api_key=self.settings.openai_api_key, max_retries=0, timeout=30) as client:
                response = client.responses.create(
                    model=self.settings.openai_model_fast, store=False,
                    instructions=GUIDED_FORM_INSTRUCTIONS, input=content,
                    max_output_tokens=1000,
                    text={"format": {"type": "json_schema", "name": "form_mapping",
                                     "strict": True, "schema": openai_json_schema(GuidedFormPlan)}},
                )
            plan = GuidedFormPlan.model_validate_json(response.output_text)
            usage = (response.usage.input_tokens, response.usage.output_tokens)
        self.budget.settle(reservation.reservation_id, *usage)
        validate_guided_form_plan(plan, captured.snapshot, facts)
        return plan, facts

    def __call__(self, package, cv_path: Path, attempt_id: str):
        page, clicked, view, policy = None, False, None, None
        last_clock = time.monotonic()
        filled = []

        def action():
            nonlocal last_clock
            now = time.monotonic()
            self.budget.record_action(package.application_id, package.tracker, now - last_clock)
            last_clock = now

        def observe(phase, **details):
            self.store.record_browser_observation(package.application_id, package.tracker,
                                                  package.fingerprint(), phase, details)

        def result(state, code, message, questions=(), **proof):
            if policy is not None:
                policy["stage"] = "human" if state == "awaiting_human_verification" or code == "human_verification_after_submit" else "paused"
            observe(state, reason_code=code, required_action=message,
                    pending_keys=[q["key"] for q in questions])
            return BrowserPreparationResult(
                state, tuple(filled), message, tuple(q["key"] for q in questions),
                page.url if page else None, adapter_name="employer-hybrid",
                review_questions=tuple(questions), reason_code=code, **proof,
            )

        try:
            if self.settings.portal_delivery_mode != "hybrid":
                return result("needs_input", "feature_disabled", "Hybrid delivery is disabled.")
            from backend_scout.portal_rollout import hybrid_delivery_allowed

            if not hybrid_delivery_allowed(self.settings, self.store, package.application_id, package.tracker):
                return result("needs_input", "rollout_gate", "Production hybrid delivery awaits approved canary rollout.")
            current = self.store.get_current(package.application_id, package.tracker)
            attempt = self.store.latest_delivery(package.application_id, package.tracker)
            if (current.package.fingerprint() != package.fingerprint()
                or not self.store.package_approved(package.application_id, package.tracker, package.fingerprint())
                or not attempt or attempt["attempt_id"] != attempt_id or attempt["state"] != "sending"):
                return result("needs_input", "approval_missing", "An exact approved, claimed package is required.")
            raw_route = self.store.latest_application_route(package.application_id, package.tracker)
            route = ApplicationRoute.model_validate(raw_route) if raw_route else None
            if route:
                route._tracker = package.tracker.value
            if (not route or route.status != "verified"
                or package.official_destination != route.application_url
                or package.company != route.company or package.role != route.title):
                return result("needs_input", "verified_route_required", "Inspect and review the exact employer destination first.")
            legacy = PackagePortalAdapter(self.manager, self.settings)
            proof_path = legacy._proof_path(attempt_id)
            if proof_path.exists() or proof_path.is_symlink():
                return result("submission_unknown", "attempt_has_proof", "This attempt already has proof; reconcile it.")
            cv_path = Path(cv_path).resolve(strict=True)
            raw_cv = legacy._cv_bytes(cv_path, package.cv_sha256)
            binding = (package.application_id, package.tracker, package.official_destination,
                       package.cv_sha256, package.fingerprint())
            try:
                session = self.manager.acquire(*binding, navigate=False)
            except PortalSessionError as exc:
                if exc.reason != "binding_changed":
                    raise
                self.manager.close_session(package.application_id, package.tracker)
                session = self.manager.acquire(*binding, navigate=False)
            page = self._guard(session, route)
            policy = session.context._backendscout_policy
            if page.url == "about:blank":
                action()
                page.goto(package.official_destination, wait_until="domcontentloaded", timeout=20_000)
            if not route_allows_url(route, page.url):
                return result("needs_input", "destination_changed", "The browser left the exact verified route.")
            if human_required(page, package.tracker):
                return result("awaiting_human_verification", "human_verification", "Complete verification in the preserved browser.")
            if visible_receipts(page):
                return result("submission_unknown", "existing_receipt", "A receipt already exists; reconcile before another attempt.")
            for cycle in range(3):
                view = find_application_form(page, route)
                if view:
                    break
                if cycle == 1:
                    action()
                    page.reload(wait_until="domcontentloaded", timeout=20_000)
                else:
                    page.wait_for_timeout(500)
            if view is None:
                return result("needs_input", "application_form_missing", "No unique CV application form is ready; the session remains open.")
            expected_schema = next((a.value for a in package.answers if a.key == "review.form_schema_sha256"), None)
            if expected_schema != form_review_signature(view):
                return result("needs_input", "form_review_changed", "The questions or consent links changed; inspect and review the updated package.")
            policy["stage"] = "fill"
            observe("filling", destination=route.application_url)
            captured, pending = prepare_reviewed_fields(view, package, fill=True, action=action)
            for _ in range(2):
                # Re-observe each cycle. A planner cannot approve new text or choices.
                if not pending:
                    break
                try:
                    plan, facts = self._map(captured, package)
                    observe("mapped", fields=[m.field_id for m in plan.mappings],
                            usage=self.budget.snapshot(package.application_id, package.tracker))
                except BudgetExceeded as exc:
                    observe("deterministic_only", reason_code=exc.reason_code)
                    break
                except Exception:  # noqa: BLE001 - optional provider failures must not authorize actions.
                    observe("deterministic_only", reason_code="model_mapping_unavailable")
                    break
                fresh = capture_guided_form(view)
                if fresh.snapshot != captured.snapshot:
                    captured, pending = prepare_reviewed_fields(view, package, fill=True, action=action)
                    continue
                aliases = []
                for mapping in plan.mappings:
                    field = next(f for f in fresh.snapshot.fields if f.field_id == mapping.field_id)
                    # Uploaded files, sensitive choices and submitting remain code-only.
                    if field.sensitive or field.kind.value not in {"text", "email", "tel", "url", "textarea"}:
                        continue
                    key = question_key(field, fresh.controls[field.field_id])
                    aliases.append(ReviewedAnswer(key=key, value=facts[mapping.fact_key].value))
                if not aliases:
                    break
                temporary = package.model_copy(update={"answers": tuple(package.answers) + tuple(aliases)})
                captured, pending = prepare_reviewed_fields(view, temporary, fill=True, action=action)
                # Mapping aliases never change the fingerprint, approval or persisted answers.
                package_for_audit = temporary
                break
            else:
                package_for_audit = package
            package_for_audit = locals().get("package_for_audit", package)
            if pending:
                return result("needs_input", "portal_input_required", "Review the new or ambiguous fields; nothing was sent.", pending)
            action()
            policy.update(stage="upload", writes=0)
            view.file_control.set_input_files({"name": cv_path.name, "mimeType": "application/pdf", "buffer": raw_cv}, timeout=5000)
            page.wait_for_timeout(250)
            policy["stage"] = "fill"
            filled.append("approved_cv")
            view = find_application_form(page, route)
            if not view or view.file_control.evaluate(_FILE_HASHES) != [package.cv_sha256]:
                return result("needs_input", "cv_attachment", "The exact approved PDF was not retained by the form.")
            if expected_schema != form_review_signature(view):
                return result("needs_input", "form_review_changed", "Questions changed after upload; inspect and review them before submitting.")
            _, pending = prepare_reviewed_fields(view, package_for_audit, fill=False)
            if pending:
                return result("needs_input", "form_changed", "The form changed after upload; review its fields.", pending)
            button = unique_submit(view)
            if human_required(page, package.tracker):
                return result("awaiting_human_verification", "human_verification", "Complete verification in the preserved browser, then resume.")
            if button is None:
                return result("needs_input", "submit_control_ambiguous", "No unique final application submit control is visible.")
            metadata = button.evaluate(_BUTTON_METADATA)
            target = urljoin(view.url, metadata["action"] or view.url)
            if (not route_allows_url(route, target, confirmation=True) or not application_endpoint(target)
                or metadata["target"] not in {"", "_self"} or metadata["invalid"]):
                return result("needs_input", "form_action_changed", "The form action or required fields need review.")
            # Recheck canonical approval and bytes at the irreversible boundary.
            if self.store.get_current(package.application_id, package.tracker).package.fingerprint() != package.fingerprint():
                return result("needs_input", "package_changed", "The package changed; approve its current version.")
            legacy._cv_bytes(cv_path, package.cv_sha256)
            if (not route_allows_url(route, page.url)
                or not route_allows_url(route, view.frame.url)
                or view.file_control.evaluate(_FILE_HASHES) != [package.cv_sha256]):
                return result("needs_input", "form_changed", "The destination or attachment changed before submission.")
            observe("ready_to_submit", destination=route.application_url, cv_sha256=package.cv_sha256)
            action()
            clicked = True  # Even a transport timeout after this line is uncertain.
            policy.update(stage="send", writes=0)
            button.click(timeout=10_000, no_wait_after=True)
            deadline = time.monotonic() + self.settings.portal_receipt_timeout_seconds
            while True:
                if human_required(page, package.tracker):
                    return result("submission_unknown", "human_verification_after_submit", "Verification appeared after Submit; complete it in the preserved browser and reconcile the receipt without resubmitting.")
                if not route_allows_url(route, page.url, confirmation=True):
                    return result("submission_unknown", "unverified_confirmation_route", "Submit may have occurred; the confirmation destination changed.")
                receipts = visible_receipts(page)
                if receipts:
                    frame, marker = receipts[0]
                    if not route_allows_url(route, frame.url, confirmation=True):
                        return result("submission_unknown", "unverified_receipt", "A receipt appeared outside the verified route.")
                    receipt = frame.get_by_text(marker, exact=False).last
                    if not receipt.is_visible():
                        return result("submission_unknown", "receipt_hidden", "No visible receipt can be proved.")
                    receipt.scroll_into_view_if_needed(timeout=3000)
                    if not painted_receipt(receipt):
                        return result("submission_unknown", "receipt_not_painted", "The receipt is obscured or transparent; reconcile before retrying.")
                    raw = page.screenshot(full_page=False, type="png")
                    _validate_png(raw)
                    _save_proof(proof_path, raw)
                    full_path = proof_path.with_name(proof_path.stem + "-full.png")
                    full = page.screenshot(full_page=True, type="png")
                    _validate_png(full)
                    _save_proof(full_path, full)
                    details = {"engine": "employer-hybrid", "attempt_id": attempt_id,
                               "receipt": marker, "portal_url": page.url,
                               "confirmed_at": datetime.now(UTC).isoformat(),
                               "cv_version": package.cv_version,
                               "fingerprint": package.fingerprint(), "cv_sha256": package.cv_sha256,
                               "proof_sha256": hashlib.sha256(raw).hexdigest(),
                               "full_proof_path": str(full_path),
                               "full_proof_sha256": hashlib.sha256(full).hexdigest()}
                    return result("submitted", "visible_receipt", "The employer visibly confirmed receipt; proof is saved.",
                                  screenshot_path=str(proof_path), screenshot_sha256=details["proof_sha256"],
                                  receipt_details=details)
                if time.monotonic() >= deadline:
                    return result("submission_unknown", "receipt_timeout", "Submit was clicked but no new visible receipt arrived. Reconcile before retrying.")
                page.wait_for_timeout(250)
        except BudgetExceeded as exc:
            return result("submission_unknown" if clicked else "needs_input", exc.reason_code,
                          "The persistent browser budget is exhausted; no action was replayed.")
        except Exception as exc:  # noqa: BLE001 - all failures after clicking are uncertain, never replayable.
            location = traceback.extract_tb(exc.__traceback__)[-1]
            return result("submission_unknown" if clicked else "needs_input",
                          getattr(exc, "reason", f"browser_{type(exc).__name__}_{Path(location.filename).stem}_{location.lineno}"),
                          "Submit may have occurred; reconcile it." if clicked else "Browser preparation paused; the session remains open.")
        finally:
            with suppress(BudgetExceeded):
                used = self.budget.snapshot(package.application_id, package.tracker)
                remaining = max(0, self.settings.portal_max_active_seconds - float(used["active_seconds"]))
                self.budget.record_active(package.application_id, package.tracker,
                                          min(remaining, time.monotonic() - last_clock))
