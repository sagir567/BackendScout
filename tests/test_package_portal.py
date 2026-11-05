"""Known portal contracts; optional Chromium tests intercept every request."""

import hashlib
import json
import os
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from playwright.sync_api import sync_playwright

from backend_scout.application_package import ReviewedAnswer
from backend_scout.config import TrackerName
from backend_scout.package_portal import PackagePortalAdapter, _same_job, _validate_png
from backend_scout.portal_adapters import adapter_for_url
from backend_scout.portal_sessions import PortalSessionManager


@pytest.mark.parametrize(
    "host",
    ["evilgreenhouse.io", "greenhouse.io.evil.test", "ashbyhq.com.evil.test", "evilashbyhq.com"],
)
def test_registry_requires_real_host_boundary(host):
    assert adapter_for_url(f"https://{host}/application") is None


def test_fixture_is_never_a_production_adapter():
    assert adapter_for_url("https://example.com/application", tracker=TrackerName.TEST)
    assert (
        adapter_for_url("https://example.com/application", tracker=TrackerName.PRODUCTION) is None
    )


def test_same_vendor_redirect_cannot_change_the_approved_job():
    approved = "https://jobs.ashbyhq.com/fixture/role123/application"
    assert _same_job(approved, approved)
    assert _same_job("https://jobs.ashbyhq.com/fixture/role123/confirmation", approved)
    assert not _same_job("https://jobs.ashbyhq.com/other/role123/application", approved)
    assert not _same_job("https://jobs.ashbyhq.com/fixture/role1234/application", approved)


def test_changed_cv_stops_before_any_browser_io(review_service):
    package, path = review_service.preview_package("example-job", TrackerName.TEST)
    manager = Mock()
    path.write_bytes(b"%PDF-changed")
    result = PackagePortalAdapter(manager, review_service.settings)(package, path, "fixture")
    assert result.state == "needs_input"
    manager.acquire.assert_not_called()


def test_blank_receipt_png_is_rejected():
    from io import BytesIO

    from PIL import Image

    stream = BytesIO()
    Image.new("RGB", (100, 100), "white").save(stream, format="PNG")
    with pytest.raises(ValueError, match="blank"):
        _validate_png(stream.getvalue())


def _form(extra="", *, confirmation=True, two_buttons=False):
    action = (
        "document.body.innerHTML='<h1>Application received</h1><p>Fixture receipt only.</p>'"
        if confirmation
        else "document.querySelector('h1').textContent='Processing'"
    )
    second = '<button type="submit">Submit application</button>' if two_buttons else ""
    return f"""<!doctype html><html><body><h1>Synthetic Backend Engineer</h1>
    <form onsubmit="event.preventDefault();window.sent=(window.sent||0)+1;{action}">
    <label>Name<input name="name" required></label>
    <label>Email<input name="email" type="email" required></label>
    <label>Resume<input name="resume" type="file" required></label>
    {extra}<button type="submit">Submit application</button>{second}</form>
    </body></html>"""


@pytest.mark.skipif(
    os.getenv("BACKENDSCOUT_BROWSER_SMOKE") != "1", reason="Opt-in isolated Chromium smoke"
)
@pytest.mark.parametrize(
    "scenario", ["success", "question", "human_check", "ambiguous", "uncertain", "existing_receipt"]
)
def test_isolated_ashby_delivery_and_persistent_input(review_service, tmp_path, scenario):
    service = review_service
    package, path = service.preview_package("example-job", TrackerName.TEST)
    extra = (
        '<label>Work authorization<input name="authorization" required></label>'
        if scenario == "question"
        else ""
    )
    html = _form(extra, confirmation=scenario != "uncertain", two_buttons=scenario == "ambiguous")
    if scenario == "human_check":
        html = "<html><body><h1>Please verify you are human</h1></body></html>"
    elif scenario == "existing_receipt":
        html = "<html><body><h1>Application received</h1></body></html>"
    playwright = sync_playwright().start()
    requests = []

    def launch(profile, **kwargs):
        context = playwright.chromium.launch_persistent_context(
            profile, **(kwargs | {"headless": True})
        )

        def intercept(route):
            requests.append(route.request.url)
            if route.request.url == package.official_destination:
                route.fulfill(status=200, content_type="text/html", body=html)
            else:
                route.abort()

        context.route("**/*", intercept)
        return context

    owned = SimpleNamespace(
        chromium=SimpleNamespace(launch_persistent_context=launch), stop=lambda: None
    )
    factory = lambda: SimpleNamespace(start=lambda: owned)
    manager = PortalSessionManager(tmp_path / "profiles", playwright_factory=factory)
    adapter = PackagePortalAdapter(manager, service.settings)
    try:
        result = adapter(package, path, "fixture-attempt")
        expected = {
            "success": "submitted",
            "question": "needs_input",
            "human_check": "awaiting_human_verification",
            "ambiguous": "needs_input",
            "uncertain": "submission_unknown",
            "existing_receipt": "submission_unknown",
        }
        assert result.state == expected[scenario], result.message + str(
            result.unresolved_required_fields
        )
        assert requests and all(url == package.official_destination for url in requests)
        answers_hash = hashlib.sha256(
            json.dumps(
                [answer.model_dump() for answer in package.answers],
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        live = manager.acquire(
            package.application_id,
            package.tracker,
            package.official_destination,
            package.cv_sha256,
            answers_hash,
        )
        assert not live.page.is_closed()
        sent = live.page.evaluate("() => window.sent || 0")
        assert sent == (1 if scenario in {"success", "uncertain"} else 0)
        if scenario == "success":
            proof = service.store.private_root / "submission_proofs/fixture-attempt.png"
            assert proof.is_file()
            assert result.screenshot_sha256 == hashlib.sha256(proof.read_bytes()).hexdigest()
            assert adapter(package, path, "second-attempt").state == "submission_unknown"
        if scenario == "question":
            assert "Work authorization" in result.unresolved_required_fields
            assert live.page.get_by_label("Name", exact=True).input_value() == "Test Candidate"
            assert adapter(package, path, "second-attempt").state == "needs_input"
            assert len(requests) == 1, "An unchanged input pause must not reload the form"
            revised = package.model_copy(
                update={
                    "answers": (
                        *package.answers,
                        ReviewedAnswer(key="Work authorization", value="Israel"),
                    )
                }
            )
            assert adapter(revised, path, "answered-attempt").state == "submitted"
    finally:
        manager.close()
        playwright.stop()
