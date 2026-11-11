"""Owner-only HTTP transport for the shared application service."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend_scout.application_service import ApplicationService, build_application_service
from backend_scout.application_store import ApplicationConflict
from backend_scout.config import Settings, TrackerName
from backend_scout.web_sessions import OwnerSession, OwnerSessionStore, PairingRejected

STATIC_ROOT = Path(__file__).parent / "static"
SESSION_COOKIE = "bs_owner"
CSRF_COOKIE = "bs_csrf"


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PairInput(Input):
    code: str = Field(min_length=1, max_length=128)


class ApprovalInput(Input):
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class RevisionInput(Input):
    feedback: str = Field(min_length=1, max_length=4000)
    fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class DetailsInput(Input):
    description: str = Field(min_length=60, max_length=30000)
    official_url: str = Field(min_length=1, max_length=3000)
    years_experience: str | None = Field(default=None, max_length=200)


class AnswersInput(Input):
    answers: dict[str, str]


def create_app(
    settings: Settings | None = None,
    *,
    service: ApplicationService | None = None,
    session_store: OwnerSessionStore | None = None,
    tracker: TrackerName = TrackerName.PRODUCTION,
):
    settings = settings or Settings()
    service = service or build_application_service(settings)
    sessions = session_store or OwnerSessionStore(
        settings.workflow_database_path, service.allowed_actors
    )
    origin = settings.web_public_origin.rstrip("/")
    parsed = urlsplit(origin)
    if (
        parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or not parsed.hostname
    ):
        raise ValueError("Configure one exact web origin without a path or credentials")
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError("Non-loopback browser access requires HTTPS")
    if parsed.scheme == "https" and not settings.web_cookie_secure:
        raise ValueError("HTTPS owner sessions require Secure cookies")
    app = FastAPI(title="BackendScout", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service, app.state.sessions = service, sessions
    app.add_middleware(
        TrustedHostMiddleware, allowed_hosts=[parsed.hostname, "localhost", "127.0.0.1", "::1"]
    )

    @app.middleware("http")
    async def private_transport(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") != origin:
                return JSONResponse(
                    {"detail": f"Open the configured workspace at {origin} before signing in or taking actions."},
                    status_code=403,
                )
            if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
                return JSONResponse({"detail": "JSON content is required"}, status_code=415)
            try:
                if int(request.headers.get("content-length", "0")) > 65536:
                    return JSONResponse({"detail": "Request too large"}, status_code=413)
            except ValueError:
                return JSONResponse({"detail": "Invalid content length"}, status_code=400)
            if len(await request.body()) > 65536:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'self'"
        )
        return response

    @app.exception_handler(ApplicationConflict)
    async def conflict(_request, _exc):
        return JSONResponse(
            {
                "detail": "The application changed or needs reconciliation. Refresh the current review.",
                "reason_code": "review_conflict",
            },
            status_code=409,
        )

    @app.exception_handler(PermissionError)
    async def forbidden(_request, _exc):
        return JSONResponse({"detail": "Owner authorization is required"}, status_code=403)

    @app.exception_handler(ValueError)
    async def invalid(_request, _exc):
        return JSONResponse(
            {
                "detail": "The requested action could not be validated. Check the current job details."
            },
            status_code=400,
        )

    def owner(request: Request):
        token = request.cookies.get(SESSION_COOKIE, "")
        session = sessions.authenticate(token)
        if session is None:
            raise HTTPException(401, "Pair this browser with the authorized owner")
        if request.method not in {"GET", "HEAD"} and not sessions.verify_csrf(
            token, request.headers.get("x-csrf-token", "")
        ):
            raise HTTPException(403, "CSRF verification failed")
        return session

    Owner = Annotated[OwnerSession, Depends(owner)]

    @app.get("/healthz")
    def liveness():
        return {"status": "ok"}

    @app.get("/api/session")
    def session_info(request: Request):
        session = sessions.authenticate(request.cookies.get(SESSION_COOKIE, ""))
        csrf = request.cookies.get(CSRF_COOKIE, "")
        if session is None or not sessions.verify_csrf(
            request.cookies.get(SESSION_COOKIE, ""), csrf
        ):
            return {"authenticated": False, "tracker": tracker.value}
        return {
            "authenticated": True,
            "actor_id": session.actor_id,
            "csrf_token": csrf,
            "tracker": tracker.value,
        }

    @app.post("/api/pair")
    def pair(payload: PairInput, request: Request, response: Response):
        client_key = request.client.host if request.client else "unknown-client"
        try:
            credentials = sessions.redeem_pairing_code(payload.code, client_key)
        except PairingRejected as exc:
            raise HTTPException(
                403, "Pairing code is invalid, expired or temporarily rate-limited"
            ) from exc
        expires = credentials.expires_at
        age = max(1, int((expires - datetime.now(UTC)).total_seconds()))
        for name, value in (
            (SESSION_COOKIE, credentials.token),
            (CSRF_COOKIE, credentials.csrf_token),
        ):
            response.set_cookie(
                name,
                value,
                max_age=age,
                expires=expires,
                httponly=True,
                secure=settings.web_cookie_secure,
                samesite="strict",
                path="/",
            )
        return {
            "authenticated": True,
            "actor_id": credentials.actor_id,
            "csrf_token": credentials.csrf_token,
            "tracker": tracker.value,
        }

    @app.post("/api/logout")
    def logout(request: Request, response: Response, _session: Owner):
        sessions.revoke(request.cookies.get(SESSION_COOKIE, ""))
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
        return {"status": "succeeded"}

    @app.get("/api/overview")
    def overview(_session: Owner):
        return service.overview(tracker)

    @app.get("/api/applications/{application_id}")
    def detail(application_id: str, _session: Owner):
        return service.detail(application_id, tracker)

    @app.get("/api/applications/{application_id}/cv")
    def cv(application_id: str, _session: Owner):
        try:
            path = service.cv_artifact(application_id, tracker)
        except (OSError, ValueError) as exc:
            raise HTTPException(404, "The verified CV is unavailable") from exc
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=path.name,
            content_disposition_type="inline",
        )

    @app.post("/api/applications/{application_id}/tailor")
    def tailor(application_id: str, _payload: Input, session: Owner):
        return service.request_tailoring(application_id, tracker, session.actor_id)

    @app.post("/api/applications/{application_id}/refresh-details")
    def refresh_details(application_id: str, _payload: Input, session: Owner):
        return service.request_refresh(application_id, tracker, session.actor_id)

    @app.get("/api/applications/{application_id}/cv-preview")
    def cv_preview(application_id: str, sha256: str, _session: Owner):
        try:
            content = service.cv_preview(application_id, tracker, sha256)
        except OSError as exc:
            raise HTTPException(404, "The verified CV preview is unavailable") from exc
        return Response(content, media_type="image/png")

    @app.get("/api/applications/{application_id}/proof")
    def proof(application_id: str, _session: Owner):
        try:
            path = service.submission_proof(application_id, tracker)
        except (OSError, ValueError) as exc:
            raise HTTPException(404, "No verified submission proof is available") from exc
        return FileResponse(path, media_type="image/jpeg" if path.suffix.casefold() in {".jpg", ".jpeg"} else "image/png")

    @app.post("/api/applications/{application_id}/approve")
    def approve(application_id: str, payload: ApprovalInput, session: Owner):
        return service.approve_and_queue(
            application_id, tracker, session.actor_id, payload.fingerprint
        )

    @app.post("/api/applications/{application_id}/inspect-form")
    def inspect_form(application_id: str, _payload: Input, session: Owner):
        return service.request_form_inspection(application_id, tracker, session.actor_id)

    @app.post("/api/applications/{application_id}/revise")
    def revise(application_id: str, payload: RevisionInput, session: Owner):
        return service.request_revision(
            application_id, tracker, session.actor_id, payload.feedback, payload.fingerprint
        )

    @app.post("/api/applications/{application_id}/details")
    def save_details(application_id: str, payload: DetailsInput, session: Owner):
        return service.update_details(
            application_id, tracker, session.actor_id, **payload.model_dump()
        )

    @app.post("/api/applications/{application_id}/answers")
    def save_answers(application_id: str, payload: AnswersInput, session: Owner):
        return service.update_answers(application_id, tracker, session.actor_id, payload.answers)

    @app.post("/api/applications/{application_id}/deliver")
    def deliver(application_id: str, payload: ApprovalInput, session: Owner):
        return service.queue_delivery(
            application_id, tracker, session.actor_id, payload.fingerprint
        )

    @app.post("/api/applications/{application_id}/prepare")
    def prepare(application_id: str, _payload: Input, session: Owner):
        # Preparation and delivery use one reviewed package, never a remote status.
        package, _path = service.preview_package(application_id, tracker)
        return service.queue_delivery(
            application_id, tracker, session.actor_id, package.fingerprint()
        )

    @app.get("/")
    def index():
        return FileResponse(STATIC_ROOT / "index.html", media_type="text/html")

    app.mount("/static", StaticFiles(directory=STATIC_ROOT, check_dir=False), name="static")
    return app
