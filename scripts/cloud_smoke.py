"""Offline Linux integration check. Run inside the cloud image under Xvfb."""

import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import pdfplumber
from docx import Document
from PIL import Image
from playwright.sync_api import sync_playwright

from backend_scout.application_package import ApplicationPackage
from backend_scout.application_store import ApplicationConflict, ApplicationStore
from backend_scout.cv_documents import convert_docx_to_pdf
from backend_scout.secrets import FileCredentialStore


def main():
    assert os.geteuid() == 10001, "Expected the unprivileged cloud runtime user"
    with tempfile.TemporaryDirectory(prefix="backendscout-offline-") as directory:
        root = Path(directory)
        document = Document()
        document.add_paragraph("Synthetic backend CV smoke test")
        document.save(root / "cv.docx")
        convert_docx_to_pdf(root / "cv.docx", root / "cv.pdf")
        with pdfplumber.open(root / "cv.pdf") as pdf:
            assert len(pdf.pages) == 1
            assert "Synthetic backend CV" in pdf.pages[0].extract_text()

        credentials = FileCredentialStore(root / "secrets" / "gmail.json")
        credentials.save(json.dumps({"refresh_token": "synthetic-not-an-oauth-token"}))
        assert json.loads(credentials.load())["refresh_token"] == "synthetic-not-an-oauth-token"

        content = ApplicationPackage(
            application_id="offline-example",
            tracker="test",
            company="Synthetic Example",
            role="Backend Engineer",
            official_destination="https://example.invalid/apply",
            delivery_route="portal",
            cv_version="v1",
            cv_sha256=hashlib.sha256((root / "cv.pdf").read_bytes()).hexdigest(),
        )
        store = ApplicationStore(root / "workflow.sqlite3", root)
        store.save_package(content, "cv.pdf")
        store.approve(
            content.application_id, content.tracker, content.fingerprint(), "synthetic-owner"
        )
        attempt = store.begin_attempt(
            content.application_id, content.tracker, content.fingerprint(), "offline-1"
        )
        assert attempt.created
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page(viewport={"width": 800, "height": 600})
            page.set_content(
                "<html><body style='background:white'><h1>Offline receipt fixture</h1></body></html>"
            )
            assert page.locator("h1").inner_text() == "Offline receipt fixture"
            page.bring_to_front()
            page.evaluate("new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))")
            page.screenshot(path=str(root / "receipt.png"), full_page=True)
            browser.close()
        with Image.open(root / "receipt.png") as screenshot:
            assert screenshot.getbbox() is not None
            assert len(screenshot.convert("RGB").getcolors(800 * 600) or []) > 1
        store.finish_attempt(
            attempt.attempt_id, "submitted", "Synthetic fixture only", "receipt.png"
        )
        replay = store.begin_attempt(
            content.application_id, content.tracker, content.fingerprint(), "offline-1"
        )
        assert not replay.created and replay.state == "submitted"
        reopened = ApplicationStore(root / "workflow.sqlite3", root)
        assert reopened.get_current(content.application_id, content.tracker).package == content
        with sqlite3.connect(root / "workflow.sqlite3") as db:
            assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        try:
            reopened.begin_attempt(
                content.application_id, content.tracker, content.fingerprint(), "offline-2"
            )
        except ApplicationConflict:
            pass
        else:
            raise AssertionError("A completed fixture application must not be sent again")
    print(
        "Offline Linux checks passed: PDF, file credentials, ledger restart, headed Chromium, saved proof, replay guard"
    )


if __name__ == "__main__":
    main()
