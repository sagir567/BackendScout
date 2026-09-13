#!/usr/bin/env python3
"""Exercise the guided form boundary in an isolated local browser page."""

from __future__ import annotations

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from playwright.sync_api import sync_playwright

from backend_scout.config import Settings
from backend_scout.guided_submission import (
    GuidedFieldMapping,
    GuidedFormPlan,
    build_guided_candidate_facts,
    generate_guided_form_plan,
)
from backend_scout.models import CareerEvidence
from backend_scout.submission import _fill_safe_fields, capture_guided_form


def _evidence() -> CareerEvidence:
    return CareerEvidence.model_validate(
        {
            "identity": {
                "full_name": "Test Candidate",
                "email": "candidate@example.com",
            },
            "skills": [{"category": "Backend", "items": ["Python"]}],
        }
    )


def _fixture_plan(snapshot) -> GuidedFormPlan:
    fact_by_label = {
        "Candidate legal name": "identity.full_name",
        "Contact inbox": "identity.email",
        "Resume document": "approved.cv_pdf",
    }
    return GuidedFormPlan(
        mappings=[
            GuidedFieldMapping(
                field_id=field.field_id,
                fact_key=fact_by_label[field.label],
                reason="Fixture mapping for the isolated black-box page",
            )
            for field in snapshot.fields
            if not field.current_value_present
        ],
        summary="Mapped unfamiliar fixture labels to approved facts.",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live-openai",
        action="store_true",
        help="Use the configured OpenAI planner instead of the deterministic fixture plan.",
    )
    args = parser.parse_args()

    with TemporaryDirectory(prefix="backend-scout-guided-") as temporary_directory:
        cv_path = Path(temporary_directory) / "approved-cv.pdf"
        cv_path.write_bytes(b"isolated fixture only")
        evidence = _evidence()
        facts = build_guided_candidate_facts(evidence, cv_path, None)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(
                """
                <form>
                  <label>Candidate legal name <input name="applicant_identity" required></label>
                  <label>Contact inbox <input name="reply_address" required></label>
                  <label>Resume document <input name="materials" type="file" required></label>
                  <label>Security code <input name="verification_code"></label>
                  <button type="submit">Submit application</button>
                </form>
                <script>
                  document.querySelector('form').addEventListener('submit', event => {
                    event.preventDefault();
                    document.body.dataset.submitted = 'yes';
                  });
                </script>
                """
            )
            captured = capture_guided_form(page)
            labels = {field.label for field in captured.snapshot.fields}
            assert labels == {"Candidate legal name", "Contact inbox", "Resume document"}

            if args.live_openai:
                settings = Settings()
                if not settings.openai_api_key:
                    raise SystemExit("OPENAI_API_KEY is required for --live-openai")

                def build_plan(snapshot, candidate_facts):
                    return generate_guided_form_plan(
                        snapshot,
                        candidate_facts,
                        settings.openai_api_key,
                        settings.openai_model_fast,
                    )
            else:
                def build_plan(snapshot, candidate_facts):
                    assert candidate_facts.keys() == facts.keys()
                    return _fixture_plan(snapshot)

            result = _fill_safe_fields(
                page,
                evidence,
                cv_path,
                guided_plan_builder=build_plan,
            )
            assert page.locator('[name="applicant_identity"]').input_value() == "Test Candidate"
            assert page.locator('[name="reply_address"]').input_value() == "candidate@example.com"
            assert page.locator('[name="materials"]').input_value().endswith("approved-cv.pdf")
            assert page.locator('[name="verification_code"]').input_value() == ""
            assert page.locator("body").get_attribute("data-submitted") is None
            assert result.guided_plan_used
            assert not result.unresolved_required_fields

            page.set_content(
                """
                <form>
                  <input name="first_name" required>
                  <input name="last_name" required>
                  <input type="email" required>
                  <input type="file" required>
                </form>
                """
            )

            def reject_unnecessary_call(snapshot, candidate_facts):
                raise AssertionError("guided planner should not run for a deterministic form")

            deterministic_result = _fill_safe_fields(
                page,
                evidence,
                cv_path,
                guided_plan_builder=reject_unnecessary_call,
            )
            assert not deterministic_result.guided_plan_used
            assert not deterministic_result.unresolved_required_fields
            browser.close()

    planner = "OpenAI" if args.live_openai else "fixture"
    print(
        f"Guided submission black-box passed with {planner} planner: "
        f"{len(result.filled_fields)} fields, zero model calls for the deterministic form."
    )


if __name__ == "__main__":
    main()
