"""Opt-in, isolated browser acceptance: BACKENDSCOUT_BROWSER_SMOKE=1 pytest this file."""

import os
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from playwright.sync_api import expect, sync_playwright

from backend_scout.application_routes import ApplicationRoute
from backend_scout.config import TrackerName
from backend_scout.cv_artifacts import write_manifest
from backend_scout.task_queue import QueuedTaskKind, list_tasks
from backend_scout.web.app import create_app
from backend_scout.web_sessions import OwnerSessionStore

pytestmark = pytest.mark.skipif(
    os.getenv("BACKENDSCOUT_BROWSER_SMOKE") != "1", reason="Opt-in isolated Chromium smoke"
)


def fixture_pdf():
    """A complete one-page PDF, not the deliberately minimal unit-test stub."""
    stream = b"BT /F1 22 Tf 72 720 Td (Synthetic Candidate CV) Tj 0 -36 Td /F1 12 Tf (Python backend engineering - fixture only) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    content = b"%PDF-1.4\n"
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(content))
        content += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(content)
    content += b"xref\n0 6\n0000000000 65535 f \n"
    content += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return content + f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()


def test_phone_and_desktop_review_end_to_end(review_service, tmp_path):
    service = review_service
    pdf = service.cv_artifact("example-job", TrackerName.TEST)
    pdf.write_bytes(fixture_pdf())
    write_manifest(
        pdf.parent,
        "example-job",
        "Example",
        "Backend Engineer",
        next(pdf.parent.glob("*.docx")),
        pdf,
        "test",
    )
    settings = service.settings
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    settings.web_public_origin = origin
    sessions = OwnerSessionStore(settings.workflow_database_path, {"12345"})
    app = create_app(settings, service=service, session_store=sessions, tracker=TrackerName.TEST)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", access_log=False)
    )
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started
    output = Path(os.getenv("BACKENDSCOUT_QA_DIR", str(tmp_path)))
    output.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for width, height in [(360, 800), (390, 844), (1280, 900)]:
                context = browser.new_context(viewport={"width": width, "height": height})
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                page.goto(origin)
                page.get_by_role("heading", name="Pair this browser").wait_for()
                assert context.request.get(origin + "/api/overview").status == 401
                page.get_by_label("Pairing code", exact=True).fill(
                    sessions.issue_pairing_code("12345")
                )
                page.get_by_role("button", name="Pair browser", exact=True).click()
                page.get_by_role("heading", name="Today", exact=True).wait_for()
                assert page.get_by_text("2+ years", exact=True).first.is_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"queue-{width}.png"), full_page=True)
                page.locator("#tab-agent").click()
                page.get_by_role("heading", name="Agent Apply", exact=True).wait_for()
                assert page.locator("[data-job-id='example-job']").is_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"agent-apply-{width}.png"), full_page=True)
                page.get_by_role("tab", name="Needs My Input", exact=False).click()
                page.get_by_role("heading", name="Needs My Input", exact=True).wait_for()
                page.locator("#tab-applications").click()
                page.get_by_role("heading", name="Applications", exact=True).wait_for()
                page.get_by_role("tab", name="Today", exact=False).click()
                page.locator("[data-job-id='example-job']").click()
                page.locator("#detail-title").wait_for()
                assert page.get_by_role("button", name="Create CV", exact=True).is_visible()
                assert page.get_by_role("link", name="View CV", exact=False).is_visible()
                assert not page.locator("#details-editor").evaluate("element => element.open")
                page.get_by_role("button", name="Request changes", exact=True).click()
                expect(page.get_by_label("Requested changes", exact=True)).to_be_visible()
                page.locator("#revision-panel > summary").click()
                page.locator(".pdf-preview").wait_for()
                page.locator(".pdf-preview").scroll_into_view_if_needed()
                expect(page.locator(".pdf-preview")).to_have_js_property("complete", True)
                assert page.locator(".pdf-preview").evaluate("image => image.naturalWidth") > 100
                assert (
                    context.request.get(origin + "/api/applications/example-job/cv").status == 200
                )
                assert page.get_by_role("button", name="Approve & apply", exact=True).is_enabled()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"review-{width}.png"), full_page=True)
                job = service.store.get_application("example-job", TrackerName.TEST).job
                service.store.save_form_inspection("example-job", TrackerName.TEST, job.application_url,
                                                   {"status": "unavailable", "reason_code": "closed"})
                page.locator("#tab-agent").click()
                page.locator("#refresh-button").click()
                expect(page.locator("[data-job-id='example-job']")).to_have_count(0)
                page.locator("#tab-applications").click()
                page.locator("[data-job-id='example-job']").click()
                expect(page.get_by_role("button", name="Approve & apply", exact=True)).to_be_disabled()
                expect(page.get_by_role("button", name="Create CV", exact=True)).to_be_disabled()
                expect(page.get_by_role("link", name="View CV", exact=False)).to_be_visible()
                assert "saved application link" in page.locator("#review-action-context").inner_text().lower()
                page.get_by_role("button", name="Update application link", exact=True).click()
                expect(page.get_by_label("Official application URL", exact=False)).to_be_visible()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"unavailable-posting-{width}.png"), full_page=True)
                service.store.save_form_inspection("example-job", TrackerName.TEST, job.application_url,
                                                   {"status": "inspected", "reason_code": "public_schema_inspected"})
                page.get_by_role("tab", name="Health", exact=False).click()
                page.get_by_role("heading", name="Runtime checks", exact=True).wait_for()
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / f"health-{width}.png"), full_page=True)
                page.goto(origin + "?application=example-job")
                expect(page.locator("#detail-title")).to_have_text("Backend Engineer")
                page.goto(origin + "?application=unrelated-private-record")
                expect(page.get_by_role("heading", name="Today", exact=True)).to_be_visible()
                page.get_by_role("button", name="Sign out", exact=True).click()
                page.get_by_role("heading", name="Pair this browser").wait_for()
                assert (
                    context.request.get(origin + "/api/applications/example-job/cv").status == 401
                )
                assert not errors
                context.close()
            # A partial posting still exposes the existing PDF. Create CV first
            # fetches details, then the page notices worker completion itself.
            job = service.store.get_application("example-job", TrackerName.TEST).job
            service.store.revise_job(
                "example-job",
                TrackerName.TEST,
                job.model_copy(
                    update={
                        "description": "Published Lin-Srael job listing.",
                        "application_url": None,
                    }
                ),
            )
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(origin + "?application=example-job")
            page.get_by_label("Pairing code", exact=True).fill(sessions.issue_pairing_code("12345"))
            page.get_by_role("button", name="Pair browser", exact=True).click()
            page.locator("#workflow-heading").wait_for()
            expect(page.get_by_role("link", name="View CV", exact=False)).to_be_visible()
            expect(page.get_by_role("button", name="Fetch job details", exact=True)).to_be_enabled()
            expect(page.get_by_role("button", name="Approve & apply", exact=True)).to_be_disabled()
            assert not page.locator("#details-editor").evaluate("element => element.open")
            page.screenshot(path=str(output / "partial-posting-390.png"), full_page=True)
            page.get_by_role("button", name="Create CV", exact=True).click()
            expect(page.locator(".detail-status .stage")).to_have_text("Fetching job details")
            full_job = job.model_copy(update={"application_url": None})
            result = SimpleNamespace(
                status="enriched",
                job=full_job,
                reason_code="full_description_found",
                message="Synthetic source retrieval",
            )
            service.refresh_details(
                "example-job",
                TrackerName.TEST,
                "12345",
                tailor_after=True,
                enricher=lambda _: result,
            )
            service.drafter = lambda *_: None  # Keep the isolated, valid fixture PDF.
            assert (
                service.draft("example-job", TrackerName.TEST).reason_code
                == "cv_ready_route_missing"
            )
            expect(page.locator(".detail-status .stage")).to_have_text(
                "Ready for review", timeout=10000
            )
            expect(page.get_by_role("link", name="View CV", exact=False)).to_be_visible()
            expect(page.get_by_role("button", name="Approve & apply", exact=True)).to_be_disabled()
            page.screenshot(path=str(output / "cv-ready-route-missing-390.png"), full_page=True)
            context.close()

            tracker = TrackerName.TEST
            service.store.revise_job("example-job", tracker, job)
            settings.web_ready_to_apply = True
            settings.portal_delivery_mode = "hybrid"
            route = ApplicationRoute(
                status="verified", reason_code="route_verified", message="Synthetic application form",
                source_url=job.source_url, company=job.company, title=job.title,
                employer_posting_url=job.application_url, application_url=job.application_url,
                adapter_name="ashby", evidence_urls=[job.application_url],
            )
            service.browser_inspector = lambda *_: {
                "route": route.model_dump(mode="json"), "form_identified": True,
                "questions": [{"key": "email", "label": "Email", "required": True}],
                "proposed_answers": {"review.form_schema_sha256": "1" * 64},
            }
            assert service.inspect_form("example-job", tracker, "12345").status == "succeeded"
            service.store.import_snapshot("unverified-job", tracker, job, "found")
            context = browser.new_context(viewport={"width": 390, "height": 844})
            page = context.new_page()
            page.goto(origin)
            page.get_by_label("Pairing code", exact=True).fill(sessions.issue_pairing_code("12345"))
            page.get_by_role("button", name="Pair browser", exact=True).click()
            expect(page.get_by_role("heading", name="Ready to apply", exact=True)).to_be_visible()
            expect(page.locator("[data-job-id='unverified-job']")).to_have_count(0)
            page.locator("[data-job-id='example-job']").click()
            expect(page.get_by_role("button", name="Apply with this CV", exact=True)).to_be_enabled()
            expect(page.get_by_role("button", name="Request CV changes", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="Check form", exact=True)).to_have_count(0)
            expect(page.locator(".pdf-preview")).to_be_visible()
            page.screenshot(path=str(output / "ready-to-apply-390.png"), full_page=True)
            fingerprint = service.preview_package("example-job", tracker)[0].fingerprint()
            page.get_by_role("button", name="Apply with this CV", exact=True).click()
            expect(page.get_by_text("queued", exact=False).first).to_be_visible()
            queued = [task for task in list_tasks(settings.workflow_database_path)
                      if task.kind == QueuedTaskKind.PACKAGE_DELIVER]
            assert len(queued) == 1
            assert queued[0].payload["fingerprint"] == fingerprint
            assert service.store.package_approved("example-job", tracker, fingerprint)
            context.close()
            browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
