"""Isolated synthetic employer submissions; every network request is intercepted."""

import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

from backend_scout.application_package import ReviewedAnswer
from backend_scout.application_routes import ApplicationRoute
from backend_scout.config import TrackerName
from backend_scout.employer_forms import find_application_form, form_review_signature
from backend_scout.guided_submission import GuidedFormField, GuidedFormPlan, GuidedFormSnapshot
from backend_scout.hybrid_portal import HybridPortalAdapter
from backend_scout.portal_sessions import PortalSessionManager

FIXTURES = Path(__file__).parent / "fixtures" / "employer_forms"
WIDGET = "https://www.comeet.co/jobs-embed/example-solaredge/EX.AMPLE/EX.AMPLE?source=fixture"
VARIANTS = ["skyhawk", "ibi", "richkid", "raft", "solaredge"]


def test_hybrid_is_default_off_and_preserves_approved_limits():
    from backend_scout.config import Settings

    settings = Settings(_env_file=None)
    assert settings.portal_delivery_mode == "legacy"
    assert settings.portal_hybrid_rollout == "test"
    assert settings.portal_ai_monthly_limit_nis == "40"
    assert settings.portal_ai_application_limit_nis == "2"
    assert settings.portal_receipt_timeout_seconds == 90


@pytest.mark.parametrize("change", [
    {"portal_ai_monthly_limit_nis": "41"}, {"project_monthly_limit_nis": "101"},
    {"portal_max_model_calls": 7}, {"portal_max_actions": 41},
    {"portal_max_active_seconds": 601}, {"portal_ai_monthly_limit_nis": "NaN"},
])
def test_settings_cannot_silently_raise_owner_approved_caps(change):
    from backend_scout.config import Settings

    with pytest.raises(ValueError):
        Settings(_env_file=None, **change)


def test_missing_pricing_blocks_planner_before_call(review_service):
    from unittest.mock import Mock

    from backend_scout.browser_budget import BudgetExceeded

    package, _, _ = configure(review_service)
    planner = Mock()
    adapter = HybridPortalAdapter(Mock(), review_service.settings, planner=planner)
    with pytest.raises(BudgetExceeded, match="missing_pricing"):
        adapter._map(SimpleNamespace(snapshot=GuidedFormSnapshot(page_url="https://example.test/")), package)
    planner.assert_not_called()


@pytest.mark.parametrize("malicious", [False, True])
def test_model_only_maps_existing_approved_facts_and_books_usage(review_service, tmp_path, malicious):
    from unittest.mock import Mock

    package, _, _ = configure(review_service)
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps({review_service.settings.openai_model_fast: {
        "input_nis_per_million": "1", "output_nis_per_million": "2"}}))
    review_service.settings.portal_ai_prices_path = prices
    snapshot = GuidedFormSnapshot(page_url="https://example.test/job", fields=[
        GuidedFormField(field_id="field-1", label="Your contact email", kind="email", required=True)])
    payloads = []

    def planner(payload):
        payloads.append(payload)
        return GuidedFormPlan(summary="Map email only", mappings=[{
            "field_id": "field-1", "fact_key": "shell.execute" if malicious else "identity.email",
            "reason": "Exact email field"}]), (100, 50)

    adapter = HybridPortalAdapter(Mock(), review_service.settings, planner=planner)
    if malicious:
        with pytest.raises(ValueError, match="unknown fact"):
            adapter._map(SimpleNamespace(snapshot=snapshot), package)
    else:
        plan, facts = adapter._map(SimpleNamespace(snapshot=snapshot), package)
        assert facts[plan.mappings[0].fact_key].value == "fixture@example.test"
    encoded = json.dumps(payloads)
    assert "fixture@example.test" not in encoded
    assert "0500000000" not in encoded
    assert adapter.budget.snapshot("example-job", "test")["model_calls"] == 1


def test_feature_off_does_not_acquire_browser(review_service):
    from unittest.mock import Mock

    package, path, _ = configure(review_service)
    review_service.settings.portal_delivery_mode = "legacy"
    manager = Mock()
    result = HybridPortalAdapter(manager, review_service.settings)(package, path, "test-attempt")
    assert result.reason_code == "feature_disabled"
    manager.acquire.assert_not_called()


def test_network_mutations_require_controlled_step_and_one_submit_request():
    adapter = HybridPortalAdapter.__new__(HybridPortalAdapter)
    adapter._guards = {}
    context = SimpleNamespace(route=lambda _, fn: setattr(context, "handler", fn))
    page = SimpleNamespace(url="about:blank")
    route = ApplicationRoute(status="verified", reason_code="fixture", message="Synthetic",
        source_url="https://example.test/jobs/1", employer_posting_url="https://example.test/jobs/1",
        application_url="https://example.test/jobs/1", company="Example", title="Backend Engineer",
        evidence_urls=["https://example.test/jobs/1"], allowed_origins=["https://example.test"])
    route._tracker = "test"
    adapter._guard(SimpleNamespace(page=page, context=context), route)

    def send(url, method="GET", resource_type="script"):
        outcomes = []
        context.handler(SimpleNamespace(
            request=SimpleNamespace(url=url, method=method, resource_type=resource_type,
                                    is_navigation_request=lambda: False),
            fallback=lambda: outcomes.append(True), abort=lambda: outcomes.append(False)))
        return outcomes == [True]

    assert not send(route.application_url, "POST")
    assert send("https://cdn.test/script.js")
    context._backendscout_policy["stage"] = "fill"
    assert send("https://cdn.test/script.js")
    assert not send("https://cdn.test/other.js")
    assert not send(route.application_url, "POST")
    context._backendscout_policy.update(stage="send", writes=0)
    assert not send("https://example.test/jobs/2/applications", "POST")
    assert send(route.application_url, "POST")
    assert not send(route.application_url, "POST")


def configure(service, variant="raft", *, answers=()):
    company = {"skyhawk": "Example Skyhawk", "ibi": "Example IBI",
               "richkid": "Example Richkid", "raft": "Example Raft",
               "solaredge": "Example SolarEdge"}[variant]
    entry = f"https://{company.replace(' ', '').lower()}.test/jobs/backend"
    job = service.store.get_application("example-job", TrackerName.TEST).job.model_copy(update={
        "source_url": entry, "application_url": entry,
        "company": company, "title": "Junior Backend Engineer",
    })
    from backend_scout.application_store import ApplicationStore

    service.settings.workflow_database_path = service.store.database_path.with_name("hybrid.sqlite3")
    service.store = ApplicationStore(service.settings.workflow_database_path, service.store.private_root)
    service.store.register_application("example-job", TrackerName.TEST, job)
    # Fixture manifest belongs to the original role. Bind test bytes directly,
    # independently of the real CV generator and remote APIs.
    path = next(service.settings.cv_archive_root.rglob("*.pdf"))
    from backend_scout.application_package import ApplicationPackage

    base = [ReviewedAnswer(key=key, value=value) for key, value in {
        "identity.full_name": "Test Candidate", "identity.first_name": "Test",
        "identity.last_name": "Candidate", "identity.email": "fixture@example.test",
        "identity.phone": "0500000000", "identity.country": "Israel",
        "cover_letter": "Synthetic evidence-backed application note.",
        "about_you": "Synthetic backend project experience.",
        "motivation": "I enjoy building tested backend systems.",
        "project": "Synthetic backend API project.",
        "Privacy_Policy_Approval__c": "yes",
        "source": "אתר החברה" if variant == "ibi" else "Company website",
    }.items()]
    package = ApplicationPackage(
        application_id="example-job", tracker="test", company=company,
        role=job.title, official_destination=entry, delivery_route="portal",
        cv_version="v1", cv_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        answers=tuple(base) + tuple(answers),
    )
    service.store.save_package(package, str(path.relative_to(service.store.private_root)))
    service.store.approve("example-job", TrackerName.TEST, package.fingerprint(), "12345")
    route = ApplicationRoute(
        status="verified", reason_code="verified_form", message="Synthetic verified form",
        source_url=entry, employer_posting_url=entry, application_url=entry,
        company=company, title=job.title, allowed_origins=[entry.split("/jobs")[0]],
        evidence_urls=[entry],
    )
    if variant == "solaredge":
        service.store.revise_job("example-job", TrackerName.TEST,
                                 job.model_copy(update={"application_url": WIDGET}))
        package = package.model_copy(update={"official_destination": WIDGET})
        service.store.save_package(package, str(path.relative_to(service.store.private_root)))
        service.store.approve("example-job", TrackerName.TEST, package.fingerprint(), "12345")
        route.application_url, route.job_id = WIDGET, "ex.ample"
        route.allowed_origins.append("https://www.comeet.co")
        route.evidence_urls.append(WIDGET)
    service.store.save_application_route("example-job", TrackerName.TEST, route)
    service.settings.portal_delivery_mode = "hybrid"
    service.settings.portal_receipt_timeout_seconds = 1.5
    return package, path, route


def browser(tmp_path, pages):
    driver = sync_playwright().start()
    requests = []

    def launch(profile, **kwargs):
        context = driver.chromium.launch_persistent_context(profile, **(kwargs | {"headless": True}))

        def intercept(route):
            requests.append(route.request.url)
            body = pages.get(route.request.url)
            if body is None:
                route.abort()
            else:
                route.fulfill(status=200, content_type="text/html", body=body)

        context.route("**/*", intercept)
        return context

    owned = SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch), stop=lambda: None)
    manager = PortalSessionManager(tmp_path / "profiles", playwright_factory=lambda: SimpleNamespace(start=lambda: owned))
    return manager, driver, requests


def approve_observed_schema(service, manager, package, path, route):
    session = manager.acquire("fixture-preflight", "test", package.official_destination,
                              "0" * 64, "inspection", navigate=False)
    session.page.goto(package.official_destination)
    route._tracker = "test"
    view = find_application_form(session.page, route)
    signature = form_review_signature(view) if view else "0" * 64
    manager.close_session("fixture-preflight", "test")
    package = package.model_copy(update={"answers": package.answers + (
        ReviewedAnswer(key="review.form_schema_sha256", value=signature),)})
    service.store.save_package(package, str(path.relative_to(service.store.private_root)))
    service.store.approve("example-job", TrackerName.TEST, package.fingerprint(), "12345")
    return package


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.skipif(os.getenv("BACKENDSCOUT_BROWSER_SMOKE") != "1", reason="Opt-in isolated Chromium")
def test_browser_preflight_resolves_observed_route_without_entering_candidate_data(review_service, tmp_path, monkeypatch, variant):
    _, _, route = configure(review_service, variant)
    job = review_service.store.get_application("example-job", TrackerName.TEST).job
    # Resolution starts from the actual company page, not a guessed ATS URL.
    job = job.model_copy(update={"application_url": route.employer_posting_url})
    pages = {route.employer_posting_url: (FIXTURES / f"{variant}.html").read_text(),
             WIDGET: (FIXTURES / "comeet-form.html").read_text()}
    manager, driver, requests = browser(tmp_path, pages)
    monkeypatch.setattr("backend_scout.cv_service.load_career_evidence",
                        lambda: SimpleNamespace(experience=[]))
    adapter = HybridPortalAdapter(manager, review_service.settings, store=review_service.store)
    try:
        inspection = adapter.inspect(job, TrackerName.TEST)
        assert inspection["route"]["status"] == "verified", inspection["route"]
        assert inspection["proposed_answers"]["review.form_schema_sha256"]
        assert inspection["fields"]
        assert set(requests) <= set(pages)
    finally:
        manager.close()
        driver.stop()


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.skipif(os.getenv("BACKENDSCOUT_BROWSER_SMOKE") != "1", reason="Opt-in isolated Chromium")
def test_full_service_review_delivery_and_acknowledged_cleanup(review_service, tmp_path, monkeypatch, variant):
    from backend_scout import cli
    from backend_scout.cv_artifacts import draft_directory, write_manifest
    from backend_scout.submission_notifications import dispatch_submission_notifications

    service = review_service
    prior, original_pdf, route = configure(service, variant)
    job = service.store.get_application("example-job", TrackerName.TEST).job
    directory = draft_directory(service.settings.cv_archive_root, job.company, "example-job") / "v1"
    directory.mkdir(parents=True)
    docx, pdf = directory / "Test Candidate CV.docx", directory / "Test Candidate CV.pdf"
    docx.write_bytes(b"Synthetic DOCX only")
    pdf.write_bytes(original_pdf.read_bytes())
    write_manifest(directory, "example-job", job.company, job.title, docx, pdf, "test")
    pages = {route.employer_posting_url: (FIXTURES / f"{variant}.html").read_text(),
             WIDGET: (FIXTURES / "comeet-form.html").read_text()}
    manager, driver, _ = browser(tmp_path, pages)
    adapter = HybridPortalAdapter(manager, service.settings, store=service.store)
    service.browser_inspector, service.delivery_adapter = adapter.inspect, adapter
    monkeypatch.setattr("backend_scout.cv_service.load_career_evidence", lambda: SimpleNamespace(experience=[]))
    service.settings.telegram_bot_token = "synthetic-token"
    delivered = []

    class Telegram:
        def __init__(self, token):
            assert token == "synthetic-token"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def send_photo(self, chat_id, path, caption):
            delivered.append(path.read_bytes())
            return {"ok": True, "result": {"message_id": 1, "chat": {"id": chat_id, "type": "private"}}}

    try:
        service.inspect_form("example-job", TrackerName.TEST, "12345")
        service.update_answers("example-job", TrackerName.TEST, "12345",
                               {a.key: a.value for a in prior.answers if not a.key.startswith("identity.")})
        package, _ = service.preview_package("example-job", TrackerName.TEST)
        assert "review.form_schema_sha256" in {a.key for a in package.answers}
        outcome = service.approve_and_queue("example-job", TrackerName.TEST, "12345", package.fingerprint())
        assert outcome.reason_code == "queued"
        outcome = service.deliver("example-job", TrackerName.TEST, package.fingerprint(), "service-fixture")
        assert outcome.status == "succeeded", outcome
        assert service.store.get_application("example-job", TrackerName.TEST).status == "submitted"
        assert json.loads(service.store.latest_delivery("example-job", TrackerName.TEST)["receipt"])["engine"] == "employer-hybrid"
        assert dispatch_submission_notifications(service.store, service.settings, client_factory=Telegram).acknowledged == 1
        assert len(delivered) == 1
        monkeypatch.setattr(cli, "Settings", lambda: service.settings)
        cli._service_submission_notifications(service.store.database_path, portal_sessions=manager, send=False)
        assert manager._sessions == {}
    finally:
        manager.close()
        driver.stop()


@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.skipif(os.getenv("BACKENDSCOUT_BROWSER_SMOKE") != "1", reason="Opt-in isolated Chromium")
def test_five_employer_forms_send_exact_file_once_and_save_receipt(review_service, tmp_path, variant):
    package, path, route = configure(review_service, variant)
    pages = {route.employer_posting_url: (FIXTURES / f"{variant}.html").read_text(),
             WIDGET: (FIXTURES / "comeet-form.html").read_text()}
    manager, driver, requests = browser(tmp_path, pages)
    package = approve_observed_schema(review_service, manager, package, path, route)
    attempt = review_service.store.begin_attempt("example-job", TrackerName.TEST, package.fingerprint(), "synthetic-1")
    adapter = HybridPortalAdapter(manager, review_service.settings, store=review_service.store)
    try:
        result = adapter(package, path, attempt.attempt_id)
        assert result.state == "submitted", (result.reason_code, result.unresolved_required_fields)
        session = manager.acquire("example-job", "test", package.official_destination, package.cv_sha256, package.fingerprint())
        assert session.page.evaluate("window.sent") == 1
        received = session.page.evaluate("window.received")
        assert received["filename"] == path.name
        assert received["size"] == path.stat().st_size
        assert received["marketing"] is False
        proof = Path(result.screenshot_path)
        assert hashlib.sha256(proof.read_bytes()).hexdigest() == result.screenshot_sha256
        assert Path(result.receipt_details["full_proof_path"]).is_file()
        review_service.store.finish_attempt(attempt.attempt_id, "submitted", json.dumps(result.receipt_details),
                                           str(proof.relative_to(review_service.store.private_root)))
        # Restart cannot claim a second external submission for this application.
        with pytest.raises(ValueError):
            review_service.store.begin_attempt("example-job", TrackerName.TEST, package.fingerprint(), "synthetic-2")
        assert set(requests) <= set(pages)
    finally:
        manager.close()
        driver.stop()


@pytest.mark.skipif(os.getenv("BACKENDSCOUT_BROWSER_SMOKE") != "1", reason="Opt-in isolated Chromium")
@pytest.mark.parametrize("scenario", ["hidden_receipt", "new_question", "disconnect", "ambiguous_form", "changed_file"])
def test_uncertain_or_changed_forms_never_replay(review_service, tmp_path, scenario):
    package, path, route = configure(review_service)
    html = (FIXTURES / "raft.html").read_text()
    if scenario == "hidden_receipt":
        html = html.replace('receipt.id = "receipt";', 'receipt.id = "receipt"; receipt.hidden=true;')
    elif scenario == "new_question":
        html = html.replace('<button type="submit">', '<label>Salary<input name="salary" required></label><button type="submit">')
    elif scenario == "disconnect":
        html = html.replace("window.setTimeout(() => {", "window.setTimeout(() => { location.href='https://evil.test/receipt'; return;")
    elif scenario == "ambiguous_form":
        html = html.replace("</main>", '<form><label>Email<input type="email"></label><label>CV<input type="file"></label><button>Submit application</button></form></main>')
    manager, driver, _ = browser(tmp_path, {route.application_url: html})
    package = approve_observed_schema(review_service, manager, package, path, route)
    attempt = review_service.store.begin_attempt("example-job", TrackerName.TEST, package.fingerprint(), "scenario")
    if scenario == "changed_file":
        path.write_bytes(b"%PDF-1.4 changed file")
    adapter = HybridPortalAdapter(manager, review_service.settings, store=review_service.store)
    try:
        result = adapter(package, path, attempt.attempt_id)
        assert result.state == ("submission_unknown" if scenario in {"hidden_receipt", "disconnect"} else "needs_input")
        # A sending/unknown attempt is not permission for a second worker to click.
        with pytest.raises(ValueError):
            review_service.store.begin_attempt("example-job", TrackerName.TEST, package.fingerprint(), "retry")
    finally:
        manager.close()
        driver.stop()
