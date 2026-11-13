"""Authorized application operations shared by the web, CLI and durable worker."""

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from threading import Lock
from typing import Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from backend_scout.application_package import ApplicationPackage, ReviewedAnswer
from backend_scout.application_routes import ApplicationRoute
from backend_scout.application_store import ApplicationConflict, ApplicationStore
from backend_scout.application_urls import application_entry_url
from backend_scout.browser_budget import BudgetExceeded
from backend_scout.career_evidence import load_career_evidence
from backend_scout.config import Settings, TrackerName
from backend_scout.cv_artifacts import load_manifest, verify_manifest
from backend_scout.cv_coverage import CvEvidenceGap
from backend_scout.cv_service import draft_job_cv
from backend_scout.delivery_readiness import (
    POSTING_BLOCK_CODES,
    assess_delivery_readiness,
    posting_observation_blocker,
)
from backend_scout.guided_submission import build_guided_candidate_facts
from backend_scout.models import Job
from backend_scout.portal_rollout import hybrid_delivery_allowed
from backend_scout.runtime_health import workflow_health
from backend_scout.task_queue import (
    QueuedTaskKind,
    QueuedTaskStatus,
    TaskQuotaReached,
    enqueue_task,
)

HISTORICAL = {
    "submitted",
    "closed",
    "rejected",
    "offer",
    "interview",
    "assessment",
    "recruiter_reply",
    "submission_unknown",
    "sending",
}
PLACEHOLDERS = {
    "published lin-srael job listing.",
    "published public ats job posting.",
    "indeed email recommendation.",
}
_PDF_RENDER_LOCK = Lock()
_PREFLIGHT_RECHECK_CODES = {"form_review_changed", "form_schema_changed", "destination_changed"}
_FORM_SCHEMA_ANSWER = "review.form_schema_sha256"
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")


@lru_cache(maxsize=8)
def _preview_png(content: bytes) -> bytes:
    import pdfplumber

    # PDFium is not thread-safe; HTTP request workers share one rendering lock.
    with _PDF_RENDER_LOCK, pdfplumber.open(BytesIO(content)) as document:
        if len(document.pages) != 1:
            raise ValueError("Review requires a one-page CV")
        output = BytesIO()
        document.pages[0].to_image(resolution=120).original.save(output, format="PNG")
        return output.getvalue()


class OperationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["succeeded", "blocked", "retryable_failure", "permanent_failure", "uncertain"]
    reason_code: str
    message: str
    correlation_id: str
    application_id: str
    task_id: str | None = None
    fingerprint: str | None = None


class ApplicationService:
    def __init__(
        self,
        store: ApplicationStore,
        settings: Settings,
        *,
        allowed_actor_ids: set[str] | None = None,
        drafter: Callable = draft_job_cv,
        delivery_adapter: Callable | None = None,
        identity_loader: Callable = load_career_evidence,
        job_scorer: Callable | None = None,
        route_checker: Callable | None = None,
        browser_inspector: Callable | None = None,
    ):
        self.store = store
        self.settings = settings
        self.allowed_actors = (
            allowed_actor_ids
            if allowed_actor_ids is not None
            else {str(i) for i in settings.telegram_allowed_user_id_set}
        )
        self.drafter = drafter
        self.delivery_adapter = delivery_adapter
        self.identity_loader = identity_loader
        self.job_scorer = job_scorer
        self.route_checker = route_checker
        self.browser_inspector = browser_inspector

    @property
    def _hybrid(self):
        return self.settings.portal_delivery_mode == "hybrid"

    @staticmethod
    def _form_identified(observation):
        return (observation.get("form_identified") is True
                or (observation.get("kind") == "browser_form" and observation.get("passed") is True))

    def _verified_route(self, application_id, tracker):
        record = self._record(application_id, tracker)
        meta = self.store.metadata(application_id, tracker)
        if meta.get("needs_input_code") in _PREFLIGHT_RECHECK_CODES:
            return None
        raw = self.store.latest_application_route(application_id, tracker)
        try:
            route = ApplicationRoute.model_validate(raw)
            age = datetime.now(UTC) - datetime.fromisoformat(route.checked_at)
            destination = application_entry_url(route.application_url)
            if (route.status != "verified" or route.source_url != record.job.source_url
                    or (route.company, route.title) != (record.job.company, record.job.title)
                    or not destination or not destination.startswith("https://")
                    or destination != route.application_url
                    or not timedelta(0) <= age <= timedelta(hours=24)):
                return None
        except (TypeError, ValueError):
            return None
        observation = self.store.latest_form_inspection(application_id, tracker)
        if not observation:
            browser = self.store.latest_browser_observation(application_id, tracker)
            observation = browser["details"] if browser and browser["phase"] == "preflight" else None
        if (not isinstance(observation, dict) or observation.get("mode") != "hybrid"
                or observation.get("status") != "inspected" or not self._form_identified(observation)
                or observation.get("job_application_url") != record.job.application_url
                or observation.get("application_url") != route.application_url):
            return None
        proposed = json.loads(meta.get("proposed_answers_json") or "{}")
        schema_hash = proposed.get(_FORM_SCHEMA_ANSWER)
        context = observation.get("proposed_answers")
        if (not isinstance(schema_hash, str) or not _SHA256.fullmatch(schema_hash)
                or not isinstance(context, dict) or context.get(_FORM_SCHEMA_ANSWER) != schema_hash):
            return None
        return route

    @staticmethod
    def _manual_route(job, raw, inspection):
        if not raw or not inspection or inspection.get("mode") != "hybrid":
            return None
        try:
            route = ApplicationRoute.model_validate(raw)
        except ValueError:
            return None
        if (route.status == "verified"
                and (route.company, route.title, route.source_url) == (job.company, job.title, job.source_url)
                and route.application_url == job.application_url
                and route.application_url in route.evidence_urls
                and route.application_url.startswith("https://")
                and inspection.get("status") == "unavailable"
                and inspection.get("job_application_url") == route.application_url
                and inspection.get("reason_code") in {
                    "browser_form_unidentified", "form_schema_unverified", "additional_attachment_required",
                    "form_choices_unverified",
                    "comeet_reveal_manual",
                }
                and timedelta(0) <= datetime.now(UTC) - datetime.fromisoformat(route.checked_at)
                <= timedelta(hours=24)):
            return route
        return None

    def _pending_questions(self, application_id, tracker):
        from backend_scout.employer_forms import IDENTITY
        from backend_scout.package_portal import _identifier

        meta = self.store.metadata(application_id, tracker)
        confirmed = json.loads(meta.get("answers_json") or "{}")
        answers = json.loads(meta.get("proposed_answers_json") or "{}") | confirmed
        pending, identity = [], None
        for question in json.loads(meta.get("questions_json") or "[]"):
            key = question["key"] if isinstance(question, dict) else question
            if isinstance(question, dict) and not question.get("required", True):
                continue
            available = confirmed if isinstance(question, dict) and question.get("sensitive") else answers
            answer = available.get(key, "")
            if not answer and isinstance(question, dict) and not question.get("sensitive"):
                matches = {IDENTITY[_identifier(value)] for value in (key, question["label"])
                           if _identifier(value) in IDENTITY}
                if len(matches) == 1:
                    if identity is None:
                        identity = build_guided_candidate_facts(self.identity_loader(), Path(), None)
                    fact = identity.get("identity." + next(iter(matches)))
                    answer = fact.value if fact else ""
            options = question.get("options", []) if isinstance(question, dict) else []
            if not answer.strip() or (options and answer not in options):
                pending.append(question)
        return pending

    def _hybrid_blocked_outcome(self, application_id, tracker, *, check_pending=True):
        if not self._hybrid:
            return None
        if check_pending and self.store.form_check_pending(application_id, tracker):
            return self._outcome(application_id, "browser_preflight_pending",
                                 "Wait for the queued browser preflight before approving.", status="blocked")
        if self._verified_route(application_id, tracker) is None:
            route = self.store.latest_application_route(application_id, tracker) or {}
            observation = self.store.latest_form_inspection(application_id, tracker) or {}
            if observation.get("mode") == "hybrid" and observation.get("status") == "unavailable":
                return self._outcome(application_id, observation["reason_code"], observation["message"], status="blocked")
            return self._outcome(
                application_id, route.get("reason_code") if route.get("status") != "verified"
                and route.get("reason_code") else "browser_preflight_required",
                route.get("message") if route.get("status") in {"needs_input", "unavailable"}
                else "Check the official application route in the browser before applying.",
                status="blocked",
            )
        if self._pending_questions(application_id, tracker):
            return self._outcome(application_id, "portal_input_required",
                                 "Answer the required portal questions before reviewing approval.", status="blocked")
        return None

    def _authorize(self, actor_id: str):
        if actor_id not in self.allowed_actors:
            raise PermissionError("Owner authorization is required")

    @staticmethod
    def _task_context(record, actor_id):
        return {
            "page_id": record.application_id,
            "company": record.job.company,
            "title": record.job.title,
            "chat_id": int(actor_id) if actor_id.isdecimal() else None,
        }

    def sync_projection(self, tracker: TrackerName, actor_id: str) -> OperationOutcome:
        from backend_scout.notion import (
            NotionClient,
            application_digest_item_from_page,
            job_from_application_page,
            query_all_pages,
        )

        self._authorize(actor_id)
        source_id = self.settings.applications_data_source_id_for(tracker)
        if not self.settings.notion_api_key or not source_id:
            return self._outcome(
                "tracker",
                "projection_unavailable",
                "Notion is not configured; local applications remain available.",
                status="blocked",
            )
        count = 0
        with NotionClient(self.settings.notion_api_key, self.settings.notion_api_version) as client:
            for page in query_all_pages(client, source_id):
                item = application_digest_item_from_page(page)
                job = job_from_application_page(page)
                date_value = page.get("properties", {}).get("Discovered At", {}).get("date") or {}
                if date_value.get("start"):
                    discovered = datetime.fromisoformat(date_value["start"])
                    job = job.model_copy(
                        update={
                            "discovered_at": discovered.replace(tzinfo=UTC)
                            if discovered.tzinfo is None
                            else discovered
                        }
                    )
                self.store.import_snapshot(item.notion_page_id, tracker, job, item.status.value)
                count += 1
        self.queue_daily_preparation(tracker, actor_id)
        self.queue_expired_review_checks(tracker, actor_id)
        return self._outcome(
            "tracker",
            "projection_imported",
            f"Imported {count} job snapshots without transferring approval.",
        )

    def queue_daily_preparation(self, tracker, actor_id, *, now=None) -> list[str]:
        """Reserve bounded, read-only preflights; a verified form may chain a draft."""
        self._authorize(actor_id)
        if not (self.settings.web_ready_to_apply and self._hybrid and tracker == TrackerName.PRODUCTION):
            return []
        day = (now or datetime.now(UTC)).astimezone(ZoneInfo("Asia/Hebron")).date()
        prefix = f"background-prepare:{tracker.value}:{day}:"
        candidates = []
        for row in self.store.list_records(tracker):
            if (not row["job_json"] or row["status"] not in {"discovered", "shortlisted"}
                    or row["current_fingerprint"] or row["needs_input_code"]):
                continue
            job = Job.model_validate_json(row["job_json"])
            url = application_entry_url(job.application_url, job.source_url)
            if job.match_score is None or job.match_score < 55 or not url or not url.startswith("https://"):
                continue
            candidates.append((job.match_score, row["application_id"]))
        task_ids = []
        for _, application_id in sorted(candidates, key=lambda pair: (-pair[0], pair[1])):
            if (self.store.form_check_pending(application_id, tracker)
                    or self.store.latest_form_inspection(application_id, tracker)):
                continue
            try:
                self._manifest(application_id, tracker)
            except (OSError, ValueError):
                pass
            else:
                continue
            record = self._record(application_id, tracker)
            try:
                task = enqueue_task(
                    QueuedTaskKind.PACKAGE_FORM_INSPECT, tracker,
                    self._task_context(record, actor_id) | {
                        "actor_id": actor_id, "tailor_after": True, "background_preparation": True,
                    },
                    self.store.database_path, idempotency_key=prefix + application_id,
                    quota=(prefix, 5),
                )
            except TaskQuotaReached:
                break
            task_ids.append(task.task_id)
        return task_ids

    def queue_expired_review_checks(self, tracker, actor_id, *, now=None) -> list[str]:
        """Refresh aged public observations without redrafting or reusing approvals."""
        self._authorize(actor_id)
        if not (self.settings.web_ready_to_apply and self._hybrid and tracker == TrackerName.PRODUCTION):
            return []
        now = now or datetime.now(UTC)
        day = now.astimezone(ZoneInfo("Asia/Hebron")).date()
        prefix = f"background-recheck:{tracker.value}:{day}:"
        task_ids = []
        for row in self.store.list_records(tracker):
            if row["status"] != "review_ready" or not row["job_json"]:
                continue
            application_id = row["application_id"]
            fingerprint = row["current_fingerprint"]
            if ((fingerprint and self.store.package_approved(application_id, tracker, fingerprint))
                    or self.store.form_check_pending(application_id, tracker)):
                continue
            route = self.store.latest_application_route(application_id, tracker)
            if not route or route.get("status") != "verified":
                continue
            try:
                age = now - datetime.fromisoformat(route["checked_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if age <= timedelta(hours=24):
                continue
            record = self._record(application_id, tracker)
            if (route.get("company"), route.get("title"), route.get("source_url")) != (
                record.job.company, record.job.title, record.job.source_url,
            ):
                continue
            try:
                task = enqueue_task(
                    QueuedTaskKind.PACKAGE_FORM_INSPECT, tracker,
                    self._task_context(record, actor_id) | {
                        "actor_id": actor_id, "tailor_after": False, "background_preparation": True,
                        "expired_review_recheck": True,
                    },
                    self.store.database_path, idempotency_key=prefix + application_id,
                    quota=(prefix, 5),
                )
            except TaskQuotaReached:
                break
            if task.status in {QueuedTaskStatus.QUEUED, QueuedTaskStatus.RUNNING, QueuedTaskStatus.RETRYING}:
                task_ids.append(task.task_id)
        return task_ids

    @staticmethod
    def _outcome(application_id, reason_code, message, *, status="succeeded", **values):
        return OperationOutcome(
            application_id=application_id,
            reason_code=reason_code,
            message=message,
            status=status,
            correlation_id=uuid4().hex[:16],
            **values,
        )

    def _record(self, application_id, tracker):
        record = self.store.get_application(application_id, tracker)
        if record.job is None:
            raise ApplicationConflict("Application details have not been imported")
        return record

    def _posting_blocker(self, application_id, tracker):
        return posting_observation_blocker(self.store.latest_decisive_form_inspection(application_id, tracker))

    def _refresh_route_observation(self, application_id, tracker):
        if self._hybrid:
            return
        record = self._record(application_id, tracker)
        if self.route_checker is not None and record.job.application_url:
            observation = self.route_checker(record.job)
            if observation is not None:
                self._save_route_observation(application_id, tracker, record.job, observation)

    def _save_route_observation(self, application_id, tracker, job, observation):
        payload = observation if isinstance(observation, dict) else asdict(observation)
        if job.application_url:
            self.store.save_form_inspection(application_id, tracker, job.application_url, payload)
        record = self._record(application_id, tracker)
        if self._hybrid:
            self.store.record_browser_observation(
                application_id, tracker, record.current_fingerprint, "preflight", payload
            )
        meta = self.store.metadata(application_id, tracker)
        if (payload.get("status") == "inspected" and record.status == "needs_input"
                and meta.get("needs_input_code") in POSTING_BLOCK_CODES | _PREFLIGHT_RECHECK_CODES):
            approved = bool(record.current_fingerprint and self.store.package_approved(
                application_id, tracker, record.current_fingerprint))
            try:
                self._manifest(application_id, tracker)
                status = "needs_input" if approved else "review_ready"
            except (OSError, ValueError):
                status = "discovered"
            self.store.set_state(application_id, tracker, status)

    def _posting_blocked_outcome(self, application_id, tracker):
        blocker = self._posting_blocker(application_id, tracker)
        return self._outcome(application_id, **blocker, status="blocked") if blocker else None

    @staticmethod
    def _has_requirements(job: Job) -> bool:
        text = job.description.strip().casefold()
        return len(text) >= 60 and text not in PLACEHOLDERS and text != job.title.casefold()

    def _manifest(self, application_id, tracker):
        record = self._record(application_id, tracker)
        manifest = load_manifest(self.settings.cv_archive_root, record.job.company, application_id)
        verify_manifest(manifest, manifest.draft_id)
        if manifest.notion_page_id != application_id or manifest.tracker != tracker.value:
            raise ApplicationConflict("CV belongs to a different application or tracker")
        path = Path(manifest.pdf_path).resolve()
        if not path.is_relative_to(self.store.private_root) or path.suffix.casefold() != ".pdf":
            raise ApplicationConflict("CV must be a private PDF artifact")
        if not path.read_bytes().startswith(b"%PDF"):
            raise ApplicationConflict("CV artifact is not a PDF")
        return manifest, path

    def preview_package(self, application_id: str, tracker: TrackerName, *, allow_drafting=False):
        record = self._record(application_id, tracker)
        if record.status in HISTORICAL:
            raise ApplicationConflict("Historical or unresolved delivery cannot be approved again")
        if record.status in {"drafting", "enriching"} and not allow_drafting:
            raise ApplicationConflict("Wait for the current CV draft before reviewing")
        job = record.job
        route = self._verified_route(application_id, tracker) if self._hybrid else None
        if self._hybrid and route is None:
            raise ApplicationConflict("Browser preflight must verify the current application route")
        destination = route.application_url if route else application_entry_url(job.application_url)
        if not destination or not destination.startswith("https://"):
            raise ApplicationConflict("Verify the official HTTPS application URL before approval")
        if not self._has_requirements(job):
            raise ApplicationConflict("Full job requirements must be reviewed before approval")
        manifest, path = self._manifest(application_id, tracker)
        meta = self.store.metadata(application_id, tracker)
        proposed = json.loads(meta.get("proposed_answers_json") or "{}")
        if self._hybrid:
            sensitive = {q["key"] for q in json.loads(meta.get("questions_json") or "[]")
                         if isinstance(q, dict) and q.get("sensitive")}
            proposed = {key: value for key, value in proposed.items() if key not in sensitive}
        answers = proposed | json.loads(meta.get("answers_json") or "{}")
        if self._hybrid:
            answers.update({key: value for key, value in proposed.items() if key.casefold().startswith("review.")})
        identity = build_guided_candidate_facts(self.identity_loader(), path, None)
        answers.update(
            {key: fact.value for key, fact in identity.items() if key.startswith("identity.")}
        )
        package = ApplicationPackage(
            application_id=application_id,
            tracker=tracker,
            company=job.company,
            role=job.title,
            official_destination=destination,
            delivery_route="portal",
            cv_version=manifest.draft_id,
            cv_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            answers=tuple(ReviewedAnswer(key=key, value=value) for key, value in answers.items()),
        )
        return package, path

    def overview(self, tracker: TrackerName) -> dict:
        items = []
        for row in self.store.list_records(tracker):
            if not row["job_json"]:
                continue
            job = Job.model_validate_json(row["job_json"])
            route = self._verified_route(row["application_id"], tracker) if self._hybrid else None
            readiness_job = job.model_copy(update={"application_url": route.application_url}) if route else job
            has_cv = False
            cv_preview_url = None
            try:
                manifest, path = self._manifest(row["application_id"], tracker)
                has_cv = True
                checksum = manifest.pdf_sha256 or hashlib.sha256(path.read_bytes()).hexdigest()
                cv_preview_url = (
                    f"/api/applications/{row['application_id']}/cv-preview?sha256={checksum}"
                )
            except (OSError, ValueError):
                pass
            code, message = row["needs_input_code"], row["needs_input_message"]
            route_blocker = self._posting_blocker(row["application_id"], tracker)
            if route_blocker and row["status"] not in HISTORICAL:
                code, message = route_blocker["reason_code"], route_blocker["message"]
            if code == "full_posting_required":
                message = "The full posting has not been retrieved from its source yet."
            if row["status"] not in HISTORICAL and not code:
                if not self._has_requirements(job):
                    code, message = (
                        "full_posting_required",
                        "The source has not provided the full job description yet.",
                    )
                elif not application_entry_url(job.application_url):
                    code, message = (
                        "official_route_required",
                        "A verified employer application link is not available yet.",
                    )
            package_ready, fingerprint = False, None
            if has_cv and row["status"] not in HISTORICAL | {"drafting", "enriching"}:
                try:
                    package, _ = self.preview_package(row["application_id"], tracker)
                    package_ready, fingerprint = True, package.fingerprint()
                except (OSError, ValueError):
                    pass
            inspection = self.store.latest_form_inspection(row["application_id"], tracker) if self._hybrid else None
            route_record = self.store.latest_application_route(row["application_id"], tracker)
            if self.settings.web_ready_to_apply and route and inspection and fingerprint:
                pending = self._pending_questions(row["application_id"], tracker)
                inspection = inspection | {
                    "passed": not pending,
                    "blockers": [q["key"] if isinstance(q, dict) else q for q in pending],
                    "package_fingerprint": fingerprint,
                    "adapter": route.adapter_name or "employer-form",
                }
            readiness = assess_delivery_readiness(
                readiness_job, lifecycle_status=row["status"], has_cv=has_cv,
                package_ready=package_ready, needs_input_code=code,
                package_fingerprint=fingerprint,
                verified_route=route.model_dump(mode="json") if route else None,
                tracker=tracker,
                inspection=inspection,
            )
            bucket = "internal"
            if row["status"] in HISTORICAL:
                bucket = "history"
            elif (self.settings.web_allow_delivery and route and not code
                  and row["status"] in {"discovered", "shortlisted", "review_ready"}
                  and not self.store.form_check_pending(row["application_id"], tracker)
                  and readiness.state == "supported_after_approval"
                  and hybrid_delivery_allowed(self.settings, self.store, row["application_id"], tracker)):
                bucket = "ready"
            elif (has_cv and self._manual_route(job, route_record, inspection)
                  and row["status"] in {"discovered", "shortlisted", "review_ready", "needs_input"}
                  and self._has_requirements(job) and not route_blocker):
                bucket = "manual"
            items.append(
                {
                    "id": row["application_id"],
                    "workspace_bucket": bucket,
                    "company": job.company,
                    "title": job.title,
                    "location": job.location,
                    "work_mode": job.remote_policy,
                    "years_experience": job.years_experience,
                    "match_score": job.match_score,
                    "status": row["status"],
                    "legacy_status": row["legacy_status"],
                    "match_reason": job.match_reason,
                    "description": job.description,
                    "salary_text": job.salary_text,
                    "official_url": route.application_url if route else application_entry_url(job.application_url),
                    "source_url": job.source_url,
                    "has_cv": has_cv,
                    "cv_url": f"/api/applications/{row['application_id']}/cv" if has_cv else None,
                    "cv_preview_url": cv_preview_url,
                    "created_at": job.discovered_at.isoformat(),
                    "updated_at": row["updated_at"],
                    "needs_input_code": code,
                    "needs_input_message": message,
                    "concerns": [message] if message else [],
                    "claimed": bool(row["claimed"]),
                    "delivery_readiness": readiness.model_dump(mode="json"),
                    "application_route": route_record,
                    "verified_route": route.model_dump(mode="json") if self._hybrid and route else None,
                    "browser_observation": self.store.latest_browser_observation(row["application_id"], tracker),
                }
            )
        active = [item for item in items if item["status"] not in HISTORICAL]
        return {
            "tracker": tracker.value,
            "ready_to_apply": self.settings.web_ready_to_apply,
            "items": items,
            "counts": {
                "today": len(active),
                "needs_input": sum(bool(i["needs_input_code"]) for i in active),
                "applications": len(items),
                "agent_apply": sum(
                    i["delivery_readiness"]["adapter"] in {"ashby", "greenhouse", "comeet", "lever"}
                    and i["delivery_readiness"]["state"] not in {"unavailable", "manual_or_unsupported"}
                    for i in active
                ),
            },
            "health": workflow_health(self.store.database_path),
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def detail(self, application_id: str, tracker: TrackerName) -> dict:
        self._record(application_id, tracker)
        item = next(i for i in self.overview(tracker)["items"] if i["id"] == application_id)
        preview = None
        blocker = None
        try:
            package, _path = self.preview_package(application_id, tracker)
            preview = {
                **package.model_dump(mode="json"),
                "fingerprint": package.fingerprint(),
                "version": package.cv_version,
                "cv_url": item["cv_url"],
                "cv_preview_url": f"/api/applications/{application_id}/cv-preview?sha256={package.cv_sha256}",
                "approved": self.store.package_approved(
                    application_id, tracker, package.fingerprint()
                ),
            }
        except (OSError, ValueError):
            if item["status"] in HISTORICAL:
                blocker = {
                    "reason_code": "historical",
                    "message": "This application is no longer available for a new submission.",
                }
            elif item["status"] in {"drafting", "enriching"}:
                blocker = {
                    "reason_code": item["status"],
                    "message": "CV drafting is in progress."
                    if item["status"] == "drafting"
                    else "Fetching the full posting from its source.",
                }
            elif (item["needs_input_code"] or "").startswith("source_"):
                blocker = {
                    "reason_code": item["needs_input_code"],
                    "message": item["needs_input_message"],
                }
            elif not self._has_requirements(self._record(application_id, tracker).job):
                blocker = {
                    "reason_code": "full_posting_required",
                    "message": "Full job requirements are not available yet. The existing CV remains viewable."
                    if item["has_cv"]
                    else "Full job requirements are needed before creating a tailored CV.",
                }
            elif not item["official_url"]:
                blocker = {
                    "reason_code": "official_route_required",
                    "message": "A verified employer application link is needed before applying. You can still create or view the CV.",
                }
            else:
                blocker = {
                    "reason_code": "cv_unavailable",
                    "message": "Create a tailored CV for this job before applying.",
                }
        hybrid_blocker = self._hybrid_blocked_outcome(application_id, tracker)
        blocker = (({"reason_code": hybrid_blocker.reason_code, "message": hybrid_blocker.message}
                    if hybrid_blocker else None)
                   or self._posting_blocker(application_id, tracker) or blocker)
        meta = self.store.metadata(application_id, tracker)
        readiness_job = self._record(application_id, tracker).job
        if self._hybrid and item["verified_route"]:
            readiness_job = readiness_job.model_copy(update={"application_url": item["verified_route"]["application_url"]})
        if not self.settings.web_ready_to_apply:
            item["delivery_readiness"] = assess_delivery_readiness(
            readiness_job,
            lifecycle_status=item["status"], has_cv=item["has_cv"],
            package_ready=preview is not None, needs_input_code=item["needs_input_code"],
            package_fingerprint=preview["fingerprint"] if preview else None,
            verified_route=item["verified_route"] if self._hybrid else None,
            tracker=tracker,
            inspection=self.store.latest_form_inspection(application_id, tracker) if self._hybrid else None,
            ).model_dump(mode="json")
        attempt = self.store.latest_delivery(application_id, tracker)
        can_resume = bool(
            preview
            and preview["approved"]
            and not blocker
            and self.settings.web_allow_delivery
            and item["status"] in {"approved", "needs_input", "blocked", "review_ready"}
            and (attempt is None or attempt["state"] == "not_sent")
            and (not self.settings.web_ready_to_apply or not self._hybrid
                 or hybrid_delivery_allowed(self.settings, self.store, application_id, tracker))
        )
        submission = None
        if attempt and attempt["state"] == "submitted":
            try:
                self.submission_proof(application_id, tracker)
                sent_package = ApplicationPackage.model_validate_json(attempt["payload_json"])
                submission = {
                    "proof_url": f"/api/applications/{application_id}/proof",
                    "timestamp": attempt["updated_at"],
                    "cv_version": sent_package.cv_version,
                    "destination": sent_package.official_destination,
                    "receipt_details": (item["browser_observation"] or {}).get("details", {}).get("receipt_details"),
                }
            except (OSError, ValueError):
                pass
        return {
            "item": item,
            "package": preview,
            "questions": json.loads(meta.get("questions_json") or "[]"),
            "pending_questions": (
                self._pending_questions(application_id, tracker)
                if self.settings.web_ready_to_apply and self._hybrid and item["status"] not in HISTORICAL
                else None
            ),
            "delivery_enabled": self.settings.web_allow_delivery,
            "delivery_mode": self.settings.portal_delivery_mode,
            "can_resume": can_resume,
            "submission": submission,
            "package_blocker": blocker,
            "form_inspection": self.store.latest_form_inspection(application_id, tracker),
            "form_check_pending": self.store.form_check_pending(application_id, tracker),
            "application_route": item["application_route"],
            "verified_route": item["verified_route"],
            "browser_observation": item["browser_observation"],
        }

    def request_form_inspection(self, application_id, tracker, actor_id, *, tailor_after=False) -> OperationOutcome:
        self._authorize(actor_id)
        record = self._record(application_id, tracker)
        from backend_scout.portal_adapters import adapter_for_url
        adapter = adapter_for_url(record.job.application_url or "", tracker=tracker)
        if self._hybrid:
            url = application_entry_url(record.job.application_url, record.job.source_url)
            allowed = bool(url and url.startswith("https://"))
        else:
            allowed = bool(adapter and adapter.name == "greenhouse")
        if record.status in HISTORICAL or not allowed:
            return self._outcome(application_id, "public_form_unavailable",
                                 "A public HTTPS posting is needed for browser preflight." if self._hybrid
                                 else "Public form inspection currently supports active Greenhouse postings only.",
                                 status="blocked")
        task = enqueue_task(
            QueuedTaskKind.PACKAGE_FORM_INSPECT, tracker,
            self._task_context(record, actor_id) | {"actor_id": actor_id, "tailor_after": tailor_after},
            self.store.database_path,
        )
        return self._outcome(application_id, "queued", "Public form check queued; no personal data will be sent.",
                             task_id=task.task_id)

    def inspect_form(self, application_id, tracker, actor_id, *, tailor_after=False,
                     background_preparation=False, expired_review_recheck=False) -> OperationOutcome:
        """Worker-only inspection; injected browsers must stay on their owner thread."""
        self._authorize(actor_id)
        record = self._record(application_id, tracker)
        if expired_review_recheck and (record.status != "review_ready" or (
            record.current_fingerprint and self.store.package_approved(
                application_id, tracker, record.current_fingerprint,
            )
        )):
            return self._outcome(application_id, "recheck_skipped",
                                 "The application left unapproved review before the check.")
        if record.status in HISTORICAL:
            return self._outcome(application_id, "historical", "This application is not available for a new check.",
                                 status="blocked")
        if self._hybrid:
            return self._inspect_hybrid_form(
                application_id, tracker, actor_id, tailor_after=tailor_after,
                background_preparation=background_preparation,
            )
        if not record.job.application_url:
            return self._outcome(application_id, "official_route_required", "Verify an official posting URL first.",
                                 status="blocked")
        from backend_scout.greenhouse_forms import inspect_greenhouse_form
        result = inspect_greenhouse_form(record.job)
        self._save_route_observation(application_id, tracker, record.job, result)
        return self._outcome(application_id, result.reason_code, result.message,
                             status="succeeded" if result.status == "inspected" else "blocked")

    @staticmethod
    def _review_questions(questions):
        if not isinstance(questions, (list, tuple)) or len(questions) > 50:
            raise ValueError("Portal questions exceed the review limit")
        result, keys = [], set()
        for question in questions:
            if not isinstance(question, dict):
                raise TypeError("Structured portal questions are required")
            key, label = question.get("key"), question.get("label")
            options = question.get("options", [])
            if (not isinstance(key, str) or not key.strip() or len(key) > 200 or key in keys
                    or not isinstance(label, str) or not label.strip() or len(label) > 2000
                    or type(question.get("required", True)) is not bool
                    or type(question.get("sensitive", False)) is not bool
                    or not isinstance(options, list) or len(options) > 100
                    or any(not isinstance(v, str) or len(v) > 1000 for v in options)):
                raise ValueError("Invalid structured portal question")
            keys.add(key)
            result.append({"key": key, "label": label, "required": question.get("required", True),
                           "options": options, "sensitive": question.get("sensitive", False)})
        return result

    def _inspect_hybrid_form(self, application_id, tracker, actor_id, *, tailor_after,
                             background_preparation=False):
        record = self._record(application_id, tracker)
        if record.status in {"drafting", "enriching"}:
            return self._outcome(application_id, "application_busy", "Wait for current application work.",
                                 status="blocked")
        if self.browser_inspector is None:
            return self._outcome(application_id, "browser_inspector_unavailable",
                                 "Browser preflight is not configured on this worker.", status="blocked")
        try:
            inspection_job = record.job
            prior_route = self.store.latest_application_route(application_id, tracker) or {}
            if (prior_route.get("status") == "verified"
                and prior_route.get("application_url") == record.job.application_url
                and (prior_route.get("company"), prior_route.get("title"), prior_route.get("source_url"))
                    == (record.job.company, record.job.title, record.job.source_url)
                and prior_route.get("employer_posting_url")):
                inspection_job = record.job.model_copy(update={"application_url": prior_route["employer_posting_url"]})
            result = self.browser_inspector(inspection_job, tracker)
            if not isinstance(result, dict):
                raise TypeError("Browser preflight returned an invalid result")
            route = ApplicationRoute.model_validate(result.get("route"))
            questions = self._review_questions(result.get("questions"))
            proposed = result.get("proposed_answers")
            if (not isinstance(proposed, dict) or len(proposed) > 100
                    or any(not isinstance(k, str) or not k.strip() or len(k) > 2000
                           or not isinstance(v, str) or not v.strip() or len(v) > 6000
                           or any(word in k.casefold() for word in
                                  ("password", "secret", "token", "captcha", "verification code"))
                           for k, v in proposed.items())):
                raise ValueError("Invalid proposed answers")
            if ((route.company, route.title, route.source_url)
                    != (record.job.company, record.job.title, record.job.source_url)):
                raise ValueError("The inspected route belongs to another job")
            status = result.get("status", "inspected" if route.status == "verified" else route.status)
            route_passed = route.status == "verified" and status in {"inspected", "verified"}
            form_identified = self._form_identified(result)
            schema_hash = proposed.get(_FORM_SCHEMA_ANSWER)
            schema_verified = isinstance(schema_hash, str) and bool(_SHA256.fullmatch(schema_hash))
            passed = route_passed and form_identified and schema_verified
            if passed and (not route.application_url or not route.application_url.startswith("https://")
                           or application_entry_url(route.application_url) != route.application_url):
                raise ValueError("A verified HTTPS application destination is required")
            observation = result | {
                "kind": "browser_form", "mode": "hybrid",
                "status": "inspected" if passed else "unavailable",
                "route": route.model_dump(mode="json"), "questions": questions,
                "job_application_url": record.job.application_url,
                "reason_code": result.get("reason_code") or route.reason_code,
                "message": result.get("message") or route.message,
                "form_identified": form_identified,
            }
            if route_passed and not passed and observation["reason_code"] not in {
                "additional_attachment_required", "form_choices_unverified", "comeet_reveal_manual",
            }:
                observation["reason_code"] = "form_schema_unverified" if form_identified else "browser_form_unidentified"
                observation["message"] = (
                    "Browser preflight must record a valid form schema checksum before approval."
                    if form_identified else "No unique actual application form was identified; repeat browser preflight."
                )
            if len(json.dumps(observation, ensure_ascii=False, allow_nan=False).encode()) > 100_000:
                raise ValueError("Browser preflight is too large")
        except Exception:  # noqa: BLE001 - inspection errors cannot authorize delivery or expose provider errors.
            observation = {"kind": "browser_form", "mode": "hybrid", "passed": False, "status": "unavailable",
                           "reason_code": "browser_preflight_failed",
                           "message": "Browser preflight could not finish; no application was sent.",
                           "job_application_url": record.job.application_url}
            self._save_route_observation(application_id, tracker, record.job, observation)
            self.store.set_state(application_id, tracker, "needs_input",
                                 code=observation["reason_code"], message=observation["message"])
            return self._outcome(application_id, observation["reason_code"], observation["message"],
                                 status="retryable_failure")
        current = self._record(application_id, tracker)
        if current.job != record.job or current.status in HISTORICAL | {"drafting", "enriching"}:
            return self._outcome(application_id, "browser_preflight_stale",
                                 "The job changed during inspection. Check the updated posting.", status="blocked")
        meta = self.store.metadata(application_id, tracker)
        previous = json.loads(meta.get("proposed_answers_json") or "{}")
        confirmed = {key: value for key, value in json.loads(meta.get("answers_json") or "{}").items()
                     if not key.casefold().startswith("review.")}
        previous_schema = previous.get(_FORM_SCHEMA_ANSWER)
        context_changed = bool(previous_schema and previous_schema != proposed.get(_FORM_SCHEMA_ANSWER))
        previous_questions = json.loads(meta.get("questions_json") or "[]")
        sensitive = {q["key"] for q in previous_questions + questions
                     if isinstance(q, dict) and q.get("sensitive")}
        retained = {key: value for key, value in confirmed.items() if key not in sensitive}
        changed = (previous | confirmed) != (proposed | confirmed)
        changed = changed or context_changed
        if current.current_fingerprint:
            package = self.store.get_current(application_id, tracker).package
            changed = changed or bool(passed and route.application_url != package.official_destination)
            if changed:
                self.store.invalidate_review(application_id, tracker, "Portal route or answers changed; review the updated package.")
                self.store.set_state(application_id, tracker, "review_ready")
        if context_changed and retained != confirmed:
            self.store.set_answers(application_id, tracker, retained)
        job = current.job
        updates = {}
        public_destination = bool(route_passed and route.application_url
                                  and route.application_url.startswith("https://")
                                  and route.application_url in route.evidence_urls
                                  and application_entry_url(route.application_url) == route.application_url)
        if (passed or self.settings.web_ready_to_apply and public_destination) and route.application_url != job.application_url:
            updates["application_url"] = route.application_url
        if (passed or self.settings.web_ready_to_apply and public_destination) and route.posting_text and route.posting_text != job.description:
            candidate = job.model_copy(update={"description": route.posting_text})
            if self._has_requirements(candidate):
                updates["description"] = route.posting_text
        if updates:
            job = job.model_copy(update=updates)
            if "description" in updates and self.job_scorer:
                job = self.job_scorer(job)
            self.store.revise_job(application_id, tracker, job)
            if current.current_fingerprint or current.status == "review_ready":
                self.store.set_state(application_id, tracker, "review_ready")
        observation["job_application_url"] = job.application_url
        self.store.save_application_route(application_id, tracker, route)
        # An overridden proposal cannot change the exact package or revoke approval.
        if self._record(application_id, tracker).status != "approved" or changed:
            self.store.set_proposed_answers(application_id, tracker, proposed)
        self.store.set_questions(application_id, tracker, questions)
        pending = self._pending_questions(application_id, tracker)
        observation.update({
            "passed": bool(passed and form_identified and not pending),
            "application_url": route.application_url,
            "package_fingerprint": self._record(application_id, tracker).current_fingerprint,
            "adapter": route.adapter_name, "checked_at": route.checked_at,
            "blockers": [q["key"] if isinstance(q, dict) else q for q in pending]
                        or ([] if passed else [observation["reason_code"]]),
        })
        self._save_route_observation(application_id, tracker, job, observation)
        blocked = self._hybrid_blocked_outcome(application_id, tracker, check_pending=False)
        if blocked:
            self.store.set_state(application_id, tracker, "needs_input", code=blocked.reason_code,
                                 message=blocked.message)
            if tailor_after and blocked.reason_code == "portal_input_required":
                if context_changed:
                    try:
                        self._manifest(application_id, tracker)
                    except (OSError, ValueError):
                        pass
                    else:
                        return blocked
                return self.request_tailoring(application_id, tracker, actor_id,
                                              background_preparation=background_preparation)
            if (tailor_after and self.settings.web_ready_to_apply
                    and self._manual_route(job, route.model_dump(mode="json"), observation)):
                try:
                    self._manifest(application_id, tracker)
                except (OSError, ValueError):
                    return self.request_tailoring(application_id, tracker, actor_id,
                                                  background_preparation=background_preparation)
            return blocked
        if self._record(application_id, tracker).status in {"needs_input", "blocked"}:
            try:
                self._manifest(application_id, tracker)
                state = "review_ready"
            except (OSError, ValueError):
                state = "discovered"
            self.store.set_state(application_id, tracker, state)
        if tailor_after:
            return self.request_tailoring(application_id, tracker, actor_id,
                                          background_preparation=background_preparation)
        return self._outcome(application_id, observation["reason_code"], observation["message"])

    def cv_artifact(self, application_id: str, tracker: TrackerName) -> Path:
        return self._manifest(application_id, tracker)[1]

    def cv_preview(self, application_id: str, tracker: TrackerName, checksum: str) -> bytes:
        content = self.cv_artifact(application_id, tracker).read_bytes()
        if len(content) > 5_000_000 or hashlib.sha256(content).hexdigest() != checksum:
            raise ApplicationConflict("Refresh the exact CV review before loading its preview")
        return _preview_png(content)

    def submission_proof(self, application_id: str, tracker: TrackerName) -> Path:
        attempt = self.store.latest_delivery(application_id, tracker)
        if (
            not attempt
            or attempt["state"] != "submitted"
            or not attempt["proof_path"]
            or not attempt["proof_sha256"]
        ):
            raise ValueError("No verified submission screenshot is available")
        path = self.store.verified_artifact(attempt["proof_path"], attempt["proof_sha256"])
        from PIL import Image
        with Image.open(path) as image:
            if image.format not in {"PNG", "JPEG"}:
                raise ValueError("The receipt proof is not a supported image")
            image.verify()
        return path

    def update_details(
        self, application_id, tracker, actor_id, *, description, official_url, years_experience
    ):
        self._authorize(actor_id)
        record = self._record(application_id, tracker)
        if len(description) > 30000 or len(description.strip()) < 60:
            raise ValueError("Provide the full job description")
        if application_entry_url(official_url) != official_url or not official_url.startswith(
            "https://"
        ):
            raise ValueError("An official HTTPS application entry URL is required")
        job = Job.model_validate(
            record.job.model_dump()
            | {
                "description": description,
                "application_url": official_url,
                "years_experience": years_experience,
            }
        )
        self.store.revise_job(application_id, tracker, job)
        self.store.set_state(application_id, tracker, "discovered")
        return self._outcome(
            application_id, "details_saved", "Job details saved; any old approval is invalid."
        )

    def update_answers(self, application_id, tracker, actor_id, answers):
        self._authorize(actor_id)
        if any(key.casefold().startswith("review.") for key in answers):
            raise ValueError("Reserved review context can only be recorded by browser preflight")
        meta = self.store.metadata(application_id, tracker)
        previous = json.loads(meta.get("answers_json") or "{}")
        questions = json.loads(meta.get("questions_json") or "[]")
        self.store.set_answers(application_id, tracker, previous | answers)
        if self._hybrid:
            self.store.set_questions(application_id, tracker, questions)
        return self._outcome(
            application_id, "answers_saved", "Answers saved; review the exact updated package."
        )

    def telegram_preparation_question(self, application_id, tracker, actor_id):
        self._authorize(actor_id)
        if not self.settings.web_ready_to_apply or not self._hybrid:
            return None
        record = self._record(application_id, tracker)
        meta = self.store.metadata(application_id, tracker)
        if (record.status != "needs_input" or meta.get("needs_input_code") != "portal_input_required"
                or self._verified_route(application_id, tracker) is None):
            return None
        pending = self._pending_questions(application_id, tracker)
        if not pending or not isinstance(pending[0], dict):
            return None
        question = pending[0]
        if re.search(
            r"password|secret|token|captcha|verification.code|privacy|consent|terms|agreement|marketing",
            f'{question["key"]} {question["label"]}', re.IGNORECASE,
        ):
            return None
        binding = json.dumps([application_id, tracker.value, record.job.application_url,
                              meta.get("proposed_answers_json"), question], sort_keys=True)
        token = hashlib.sha256(binding.encode()).hexdigest()[:16]
        command = f"/answer_{application_id} {token}"
        options = ", ".join(question.get("options", []))
        label = question["label"][:500]
        suffix = f"\nOptions: {options[:700]}" if options else ""
        return token, question, f"{record.job.company}: {label}{suffix}\n\n{command} YOUR_ANSWER"

    def answer_telegram_preparation(self, application_id, tracker, actor_id, token, answer):
        self._authorize(actor_id)
        meta = self.store.metadata(application_id, tracker)
        question = self.telegram_preparation_question(application_id, tracker, actor_id)
        if not question or token != question[0]:
            raise ApplicationConflict("This question changed or was already answered.")
        options = question[1].get("options", [])
        if not answer or len(answer) > 2000 or options and answer not in options:
            raise ValueError("Use one of the listed answers or enter a short factual answer.")
        previous = json.loads(meta.get("answers_json") or "{}")
        self.store.set_answers(
            application_id, tracker, previous | {question[1]["key"]: answer},
            expected_question_state={key: meta[key] for key in (
                "questions_json", "proposed_answers_json", "answers_json", "needs_input_code",
            )},
        )
        if self._pending_questions(application_id, tracker):
            self.store.set_state(application_id, tracker, "needs_input", code="portal_input_required",
                                 message="Required portal answers are still missing.")
            following = self.telegram_preparation_question(application_id, tracker, actor_id)
            return following[2] if following else "Answer saved. Complete the remaining question in the official portal."
        try:
            self._manifest(application_id, tracker)
        except (OSError, ValueError):
            self.store.set_state(application_id, tracker, "discovered")
            self.request_tailoring(application_id, tracker, actor_id, background_preparation=True)
            return "Answer saved. CV preparation queued; no application was approved."
        return "Answer saved. Review the exact CV and application in the private workspace; nothing was sent."

    def request_tailoring(self, application_id, tracker, actor_id, *, background_preparation=False):
        self._authorize(actor_id)
        record = self._record(application_id, tracker)
        if record.status in HISTORICAL:
            raise ApplicationConflict("Resolve the current application before drafting")
        if record.status in {"drafting", "enriching"}:
            return self._outcome(application_id, "queued", "CV drafting is already queued.")
        if self._hybrid:
            if self._verified_route(application_id, tracker) is None and (
                not self._has_requirements(record.job) or record.current_fingerprint
            ):
                return self.request_form_inspection(application_id, tracker, actor_id, tailor_after=True)
            if record.current_fingerprint and record.status in {"needs_input", "blocked"}:
                return self._outcome(application_id, "review_ready", "Review the existing PDF and remaining portal answers.")
        if blocked := self._posting_blocked_outcome(application_id, tracker):
            return blocked
        if record.status in {"review_ready", "approved"} and self.store.metadata(
            application_id, tracker
        ).get("claimed"):
            return self._outcome(
                application_id, "review_ready", "Review the current CV, or request a revision."
            )
        if not self._has_requirements(record.job):
            if self._hybrid:
                return self._outcome(application_id, "full_posting_required",
                                     "Browser preflight did not retrieve the full job requirements.", status="blocked")
            return self.request_refresh(application_id, tracker, actor_id, tailor_after=True)
        meta = self.store.metadata(application_id, tracker)
        try:
            previous_version = self._manifest(application_id, tracker)[0].draft_id
        except (OSError, ValueError):
            previous_version = "none"
        content = (
            record.job.model_dump_json() + (meta.get("revision_feedback") or "") + previous_version
        )
        key = hashlib.sha256(content.encode()).hexdigest()[:24]
        payload = self._task_context(record, actor_id)
        if background_preparation:
            payload["background_preparation"] = True
        task = enqueue_task(
            QueuedTaskKind.PACKAGE_CV_DRAFT,
            tracker,
            payload,
            self.store.database_path,
            idempotency_key=f"package-cv:{tracker.value}:{application_id}:{key}",
        )
        if task.status in {QueuedTaskStatus.BLOCKED, QueuedTaskStatus.DEAD_LETTER,
                           QueuedTaskStatus.FAILED, QueuedTaskStatus.CANCELLED}:
            # An explicit retry may draft again, but simultaneous clicks share active work.
            task = enqueue_task(QueuedTaskKind.PACKAGE_CV_DRAFT, tracker, payload, self.store.database_path)
        self.store.set_state(application_id, tracker, "drafting")
        return self._outcome(application_id, "queued", "CV drafting queued.", task_id=task.task_id)

    def request_refresh(self, application_id, tracker, actor_id, *, tailor_after=False):
        self._authorize(actor_id)
        if self._hybrid:
            return self.request_form_inspection(application_id, tracker, actor_id, tailor_after=tailor_after)
        record = self._record(application_id, tracker)
        if record.status in HISTORICAL or record.status == "drafting":
            raise ApplicationConflict("Resolve the current application work before fetching again")
        if record.status == "enriching":
            return self._outcome(application_id, "queued", "Job details are already being fetched.")
        self.store.set_state(
            application_id,
            tracker,
            "enriching",
            message="Fetching the full posting from its source.",
        )
        try:
            task = enqueue_task(
                QueuedTaskKind.PACKAGE_ENRICH,
                tracker,
                self._task_context(record, actor_id)
                | {"tailor_after": tailor_after, "actor_id": actor_id},
                self.store.database_path,
            )
        except (OSError, ValueError):
            self.store.set_state(
                application_id,
                tracker,
                "needs_input",
                code="source_refresh_failed",
                message="Job detail retrieval could not be queued. Try again.",
            )
            raise
        return self._outcome(
            application_id,
            "queued",
            "Fetching job details before creating the CV."
            if tailor_after
            else "Fetching job details from the original source.",
            task_id=task.task_id,
        )

    def refresh_details(
        self, application_id, tracker, actor_id, *, tailor_after=False, enricher=None
    ):
        self._authorize(actor_id)
        if self._hybrid:
            return self.request_form_inspection(application_id, tracker, actor_id, tailor_after=tailor_after)
        record = self._record(application_id, tracker)
        if record.status != "enriching":
            raise ApplicationConflict("No authorized source refresh is active")
        if enricher is None:
            from backend_scout.job_enrichment import enrich_job

            enricher = enrich_job
        result = enricher(record.job)
        if (
            result.status != "enriched"
            or result.job is None
            or not self._has_requirements(result.job)
        ):
            code = (
                result.reason_code
                if result.reason_code.startswith("source_")
                else f"source_{result.reason_code}"
            )
            self.store.set_state(
                application_id, tracker, "needs_input", code=code, message=result.message
            )
            return self._outcome(application_id, code, result.message, status="blocked")
        job = self.job_scorer(result.job) if self.job_scorer else result.job
        self.store.revise_job(application_id, tracker, job, allow_enriching=True)
        if tailor_after:
            return self.request_tailoring(application_id, tracker, actor_id)
        return self._outcome(
            application_id,
            "details_refreshed",
            "Job details were fetched and are ready for review.",
        )

    def request_revision(self, application_id, tracker, actor_id, feedback, fingerprint=None):
        self._authorize(actor_id)
        if blocked := self._posting_blocked_outcome(application_id, tracker):
            return blocked
        if not feedback.strip() or len(feedback) > 4000:
            raise ValueError("Provide concise revision feedback")
        if (
            fingerprint
            and self.preview_package(application_id, tracker)[0].fingerprint() != fingerprint
        ):
            raise ApplicationConflict("Review changed; refresh before requesting a revision")
        self.store.invalidate_review(application_id, tracker, feedback)
        return self.request_tailoring(application_id, tracker, actor_id)

    def draft(self, application_id, tracker) -> OperationOutcome:
        record = self._record(application_id, tracker)
        meta = self.store.metadata(application_id, tracker)
        if record.status != "drafting" or not meta.get("claimed"):
            raise ApplicationConflict("No authorized drafting request is active")
        try:
            route_pending = self._hybrid and self._verified_route(application_id, tracker) is None
            if not route_pending:
                self._refresh_route_observation(application_id, tracker)
            if blocked := self._posting_blocked_outcome(application_id, tracker):
                self.store.set_state(application_id, tracker, "needs_input",
                                     code=blocked.reason_code, message=blocked.message)
                return blocked
            self.drafter(
                record.job, application_id, tracker, self.settings, meta.get("revision_feedback")
            )
            self._manifest(application_id, tracker)
            if route_pending:
                self.store.set_state(
                    application_id, tracker, "review_ready", code="browser_preflight_required",
                    message="Your CV is ready. Check the employer form before reviewing the submission package.",
                )
                return self._outcome(
                    application_id, "cv_ready_route_pending",
                    "Your CV is ready to view; employer form verification is still required before applying.",
                )
            if not application_entry_url(record.job.application_url):
                self.store.set_state(
                    application_id,
                    tracker,
                    "review_ready",
                    code="official_route_required",
                    message="Your CV is ready. A verified employer application link is still needed before applying.",
                )
                return self._outcome(
                    application_id,
                    "cv_ready_route_missing",
                    "Your CV is ready to view; the employer application link still needs verification.",
                )
            package, path = self.preview_package(application_id, tracker, allow_drafting=True)
            if self._hybrid and self._pending_questions(application_id, tracker):
                self.store.set_state(application_id, tracker, "needs_input", code="portal_input_required",
                                     message="Your PDF is ready; required portal answers are still missing.")
                return self._outcome(application_id, "portal_input_required",
                                     "Review the PDF and answer the remaining portal questions.",
                                     status="blocked", fingerprint=package.fingerprint())
            self.store.save_package(package, path.relative_to(self.store.private_root).as_posix())
            self.store.set_state(application_id, tracker, "review_ready")
            if not self._hybrid:
                self.store.set_questions(application_id, tracker, [])
        except BudgetExceeded as exc:
            message = "CV generation is paused until pricing and the shared budget are available. Your existing CV is unchanged; no application was sent."
            self.store.set_state(application_id, tracker, "needs_input", code=exc.reason_code, message=message)
            return self._outcome(application_id, exc.reason_code, message, status="blocked")
        except CvEvidenceGap as exc:
            self.store.set_state(application_id, tracker, "needs_input",
                                 code="cv_evidence_required", message=str(exc))
            return self._outcome(application_id, "cv_evidence_required", str(exc), status="blocked")
        except (OSError, ValueError):
            self.store.set_state(
                application_id,
                tracker,
                "needs_input",
                code="cv_draft_needs_review",
                message="Drafting could not finish. Check job requirements, confirmed evidence and local credentials; no application was sent.",
            )
            return self._outcome(
                application_id,
                "cv_draft_needs_review",
                "CV drafting needs attention.",
                status="blocked",
            )
        return self._outcome(
            application_id,
            "review_ready",
            "The exact PDF is ready for review.",
            fingerprint=package.fingerprint(),
        )

    def approve_and_queue(self, application_id, tracker, actor_id, fingerprint):
        self._authorize(actor_id)
        if (self.settings.web_ready_to_apply and self._hybrid
                and not hybrid_delivery_allowed(self.settings, self.store, application_id, tracker)):
            return self._outcome(application_id, "rollout_gate",
                                 "Production hybrid delivery awaits approved canary rollout.", status="blocked")
        if blocked := self._hybrid_blocked_outcome(application_id, tracker):
            return blocked
        if blocked := self._posting_blocked_outcome(application_id, tracker):
            return blocked
        package, path = self.preview_package(application_id, tracker)
        if package.fingerprint() != fingerprint:
            raise ApplicationConflict(
                "The reviewed package is stale; refresh the PDF, route and answers"
            )
        if not self.store.metadata(application_id, tracker).get("claimed"):
            self.store.set_state(application_id, tracker, "review_ready")
        self.store.save_package(package, path.relative_to(self.store.private_root).as_posix())
        self.store.approve(application_id, tracker, fingerprint, actor_id)
        if not self.settings.web_allow_delivery:
            return self._outcome(
                application_id,
                "delivery_disabled",
                "Package approved; automatic delivery is disabled on this host.",
                status="blocked",
                fingerprint=fingerprint,
            )
        return self.queue_delivery(application_id, tracker, actor_id, fingerprint)

    def queue_delivery(self, application_id, tracker, actor_id, fingerprint):
        self._authorize(actor_id)
        if (self.settings.web_ready_to_apply and self._hybrid
                and not hybrid_delivery_allowed(self.settings, self.store, application_id, tracker)):
            return self._outcome(application_id, "rollout_gate",
                                 "Production hybrid delivery awaits approved canary rollout.", status="blocked")
        if blocked := self._hybrid_blocked_outcome(application_id, tracker):
            return blocked
        if blocked := self._posting_blocked_outcome(application_id, tracker):
            return blocked
        if not self.settings.web_allow_delivery:
            return self._outcome(
                application_id,
                "delivery_disabled",
                "Automatic delivery is disabled on this host.",
                status="blocked",
            )
        package, _path = self.preview_package(application_id, tracker)
        if package.fingerprint() != fingerprint:
            raise ApplicationConflict("The reviewed package changed")
        self.store.resume_approved(application_id, tracker, fingerprint)
        count = self.store.attempt_count(application_id, tracker) + 1
        request_key = f"package-deliver:{tracker.value}:{fingerprint}:{count}"
        task = enqueue_task(
            QueuedTaskKind.PACKAGE_DELIVER,
            tracker,
            self._task_context(self._record(application_id, tracker), actor_id)
            | {"fingerprint": fingerprint, "request_key": request_key},
            self.store.database_path,
            idempotency_key=request_key,
        )
        return self._outcome(
            application_id,
            "queued",
            "Approved application queued.",
            task_id=task.task_id,
            fingerprint=fingerprint,
        )

    def deliver(self, application_id, tracker, fingerprint, request_key) -> OperationOutcome:
        if self._record(application_id, tracker).status in HISTORICAL:
            raise ApplicationConflict("Resolve the current delivery or historical state before retrying")
        if not self.settings.web_allow_delivery or self.delivery_adapter is None:
            return self._outcome(
                application_id,
                "delivery_unavailable",
                "No delivery adapter is active.",
                status="blocked",
            )
        if blocked := self._hybrid_blocked_outcome(application_id, tracker):
            return blocked
        if blocked := self._posting_blocked_outcome(application_id, tracker):
            return blocked
        package, path = self.preview_package(application_id, tracker)
        if package.fingerprint() != fingerprint:
            raise ApplicationConflict("The current files, destination or answers changed")
        self._refresh_route_observation(application_id, tracker)
        if blocked := self._posting_blocked_outcome(application_id, tracker):
            return blocked
        attempt = self.store.begin_attempt(application_id, tracker, fingerprint, request_key)
        if not attempt.created:
            return self._outcome(
                application_id,
                "attempt_already_recorded",
                "Existing delivery will not be replayed.",
                status="uncertain" if attempt.state in {"sending", "unknown"} else "succeeded",
            )
        try:
            result = self.delivery_adapter(package, path, attempt.attempt_id)
            if not hasattr(result, "state") or result.state not in {
                "submitted",
                "submission_unknown",
                "submission_prepared",
                "awaiting_human_verification",
                "needs_input",
            }:
                raise ValueError("Adapter returned an unsupported delivery outcome")
            review_questions = self._review_questions(getattr(result, "review_questions", ()))
            reason = getattr(result, "reason_code", None)
            if reason is not None and (not isinstance(reason, str) or not reason.strip() or len(reason) > 160):
                raise ValueError("Invalid adapter reason code")
            receipt_details = getattr(result, "receipt_details", None)
            if receipt_details is not None and not isinstance(receipt_details, dict):
                raise ValueError("Invalid receipt audit")
            if self._hybrid or receipt_details is not None or review_questions or reason:
                self.store.record_browser_observation(application_id, tracker, fingerprint, "delivery", {
                    "state": result.state, "reason_code": reason, "message": result.message,
                    "review_questions": review_questions, "receipt_details": receipt_details,
                    "cv_version": package.cv_version, "destination": package.official_destination,
                    "proof_path": result.screenshot_path, "proof_sha256": result.screenshot_sha256,
                    "resolved_application_url": getattr(result, "resolved_application_url", None),
                    "adapter_name": getattr(result, "adapter_name", None),
                    "page_fingerprint": getattr(result, "page_fingerprint", None),
                })
        except Exception:  # noqa: BLE001 - an unexpected external interruption is never evidence of non-delivery.
            self.store.finish_attempt(
                attempt.attempt_id,
                "unknown",
                "Adapter interrupted; inspect the portal before any retry.",
            )
            return self._outcome(
                application_id,
                "receipt_unknown",
                "Delivery may have started; reconcile before retrying.",
                status="uncertain",
            )
        if result.state == "submitted" and (
            not result.screenshot_path or not result.screenshot_sha256
        ):
            self.store.finish_attempt(
                attempt.attempt_id, "unknown", "Receipt was reported without verifiable proof."
            )
            return self._outcome(
                application_id,
                "receipt_unknown",
                "Receipt proof is missing; inspect before retrying.",
                status="uncertain",
            )
        if result.state == "submitted":
            proof = Path(result.screenshot_path).resolve()
            try:
                verified = (
                    proof.is_relative_to(self.store.private_root)
                    and hashlib.sha256(proof.read_bytes()).hexdigest() == result.screenshot_sha256
                )
            except OSError:
                verified = False
            if not verified:
                self.store.finish_attempt(
                    attempt.attempt_id, "unknown", "Confirmation proof could not be verified."
                )
                return self._outcome(
                    application_id,
                    "receipt_unknown",
                    "Confirmation proof needs reconciliation.",
                    status="uncertain",
                )
            self.store.finish_attempt(
                attempt.attempt_id,
                "submitted",
                json.dumps(receipt_details, ensure_ascii=False, sort_keys=True) if receipt_details else result.message,
                proof.relative_to(self.store.private_root).as_posix(),
            )
            return self._outcome(
                application_id,
                reason or "submitted",
                "Application receipt and screenshot saved.",
                fingerprint=fingerprint,
            )
        if result.state == "submission_unknown":
            self.store.finish_attempt(attempt.attempt_id, "unknown", result.message)
            return self._outcome(
                application_id, reason or "receipt_unknown", result.message, status="uncertain"
            )
        self.store.finish_attempt(attempt.attempt_id, "not_sent", result.message)
        code = reason or (
            "human_verification"
            if result.state == "awaiting_human_verification"
            else "portal_input_required"
        )
        self.store.set_state(
            application_id,
            tracker,
            "needs_input",
            code=code,
            message=result.message
            + (
                " Missing: " + ", ".join(result.unresolved_required_fields)
                if result.unresolved_required_fields
                else ""
            ),
        )
        technical = {
            "portal_authentication",
            "cv_attachment",
            "supported_portal_adapter",
            "application_url",
            "portal_preparation",
            "final_submit_control",
            "delivery_route",
            "binding_changed",
            "session_limit",
            "profile_locked",
            "unsafe_profile",
            "browser_start_failed",
            "session_open_failed",
            "session_unavailable",
            "session_cleanup_required",
        }
        questions = review_questions or [q for q in result.unresolved_required_fields if q not in technical][:50]
        if self._hybrid:
            previous = json.loads(self.store.metadata(application_id, tracker).get("questions_json") or "[]")
            by_key = {q["key"] if isinstance(q, dict) else q: q for q in previous + questions}
            questions = list(by_key.values())
        self.store.set_questions(application_id, tracker, questions)
        return self._outcome(application_id, code, result.message, status="blocked")


def _rescore_enriched_job(job: Job) -> Job:
    from backend_scout.candidate_profile import load_candidate_profile
    from backend_scout.matcher import score_job

    try:
        result = score_job(load_candidate_profile(), job)
    except (OSError, ValueError):
        return job.model_copy(
            update={
                "match_score": None,
                "match_reason": "Job details were updated; matching awaits profile validation.",
            }
        )
    return job.model_copy(
        update={"match_score": result.match_score, "match_reason": result.reason_summary}
    )


def build_application_service(
    settings: Settings, *, delivery_adapter=None, browser_inspector=None,
) -> ApplicationService:
    private_root = settings.cv_archive_root.expanduser().resolve().parent
    return ApplicationService(
        ApplicationStore(settings.workflow_database_path, private_root),
        settings,
        delivery_adapter=delivery_adapter,
        browser_inspector=browser_inspector,
        job_scorer=_rescore_enriched_job,
        route_checker=_inspect_public_route if settings.portal_delivery_mode != "hybrid" else None,
    )


def _inspect_public_route(job: Job):
    from backend_scout.greenhouse_forms import inspect_greenhouse_form
    from backend_scout.portal_adapters import adapter_for_url

    adapter = adapter_for_url(job.application_url or "", tracker=TrackerName.PRODUCTION)
    return inspect_greenhouse_form(job) if adapter and adapter.name == "greenhouse" else None
