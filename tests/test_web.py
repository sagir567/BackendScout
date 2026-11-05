import pytest
from fastapi.testclient import TestClient

from backend_scout.config import TrackerName
from backend_scout.web.app import create_app
from backend_scout.web_sessions import OwnerSessionStore

ORIGIN = "http://127.0.0.1:8765"


@pytest.fixture
def web(review_service):
    sessions = OwnerSessionStore(review_service.store.database_path, {"12345"})
    app = create_app(
        review_service.settings,
        service=review_service,
        session_store=sessions,
        tracker=TrackerName.TEST,
    )
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, sessions, review_service


def pair(client, sessions):
    code = sessions.issue_pairing_code("12345")
    response = client.post("/api/pair", json={"code": code}, headers={"Origin": ORIGIN})
    assert response.status_code == 200, response.text
    return {"Origin": ORIGIN, "X-CSRF-Token": response.json()["csrf_token"]}


def test_private_data_and_artifacts_require_owner_session(web):
    client, _, _ = web
    assert client.get("/api/session").json()["authenticated"] is False
    for path in (
        "/api/overview",
        "/api/applications/example-job",
        "/api/applications/example-job/cv",
        "/api/applications/example-job/cv-preview?sha256=x",
        "/api/applications/example-job/proof",
    ):
        assert client.get(path).status_code == 401


def test_pairing_session_cookie_csrf_and_refresh(web):
    client, sessions, _ = web
    headers = pair(client, sessions)
    assert client.get("/api/session").json()["csrf_token"] == headers["X-CSRF-Token"]
    assert client.get("/api/overview").json()["items"][0]["years_experience"] == "2+ years"
    cookie = next(cookie for cookie in client.cookies.jar if cookie.name == "bs_owner")
    assert cookie.has_nonstandard_attr("HttpOnly")
    assert cookie.get_nonstandard_attr("SameSite") == "strict"


def test_public_form_check_requires_session_csrf_and_queues_only(web, monkeypatch):
    client, sessions, service = web
    path = "/api/applications/example-job/inspect-form"
    assert client.post(path, json={}, headers={"Origin": ORIGIN}).status_code == 401
    headers = pair(client, sessions)
    job = service.store.get_application("example-job", TrackerName.TEST).job
    service.store.revise_job("example-job", TrackerName.TEST, job.model_copy(
        update={"application_url": "https://job-boards.greenhouse.io/example/jobs/123"}))
    monkeypatch.setattr(service, "inspect_form", lambda *_: pytest.fail("No inline HTTP fetch"))
    assert client.get(path).status_code == 405
    assert client.post(path, json={}, headers={"Origin": ORIGIN}).status_code == 403
    response = client.post(path, json={}, headers=headers)
    assert response.status_code == 200 and response.json()["reason_code"] == "queued"
    detail = client.get("/api/applications/example-job").json()
    assert detail["form_check_pending"] is True
    assert detail["package"]["approved"] is False


@pytest.mark.parametrize("headers", [{}, {"Origin": "https://evil.example"}, {"Origin": ORIGIN}])
def test_mutations_reject_cross_origin_or_missing_csrf(web, headers):
    client, sessions, _ = web
    pair(client, sessions)
    response = client.post("/api/applications/example-job/tailor", json={}, headers=headers)
    assert response.status_code == 403


def test_pair_code_is_single_use_and_cannot_be_redeemed_cross_origin(web):
    client, sessions, _ = web
    code = sessions.issue_pairing_code("12345")
    assert (
        client.post(
            "/api/pair", json={"code": code}, headers={"Origin": "https://evil.example"}
        ).status_code
        == 403
    )
    assert (
        client.post("/api/pair", json={"code": code}, headers={"Origin": ORIGIN}).status_code == 200
    )
    assert (
        client.post("/api/pair", json={"code": code}, headers={"Origin": ORIGIN}).status_code == 403
    )


def test_review_stale_approval_and_real_cv_response(web):
    client, sessions, _ = web
    headers = pair(client, sessions)
    detail = client.get("/api/applications/example-job").json()
    assert detail["package"]["approved"] is False
    assert (
        client.post(
            "/api/applications/example-job/approve", json={"fingerprint": "0" * 64}, headers=headers
        ).status_code
        == 409
    )
    response = client.get("/api/applications/example-job/cv")
    assert response.content.startswith(b"%PDF")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "application/pdf"
    assert (
        client.get("/api/applications/example-job/cv-preview?sha256=" + "0" * 64).status_code == 409
    )
    assert client.get("/api/applications/example-job/proof").status_code == 404


def test_known_unavailable_link_disables_review_and_resume_without_hiding_pdf(web):
    client, sessions, service = web
    headers = pair(client, sessions)
    package, _ = service.preview_package("example-job", TrackerName.TEST)
    service.approve_and_queue("example-job", TrackerName.TEST, "12345", package.fingerprint())
    service.store.save_form_inspection("example-job", TrackerName.TEST, package.official_destination,
                                       {"status": "unavailable", "reason_code": "closed"})
    detail = client.get("/api/applications/example-job").json()
    assert detail["package_blocker"]["reason_code"] == "posting_unavailable"
    assert detail["can_resume"] is False
    assert client.get("/api/overview").json()["counts"]["agent_apply"] == 0
    assert client.get("/api/applications/example-job/cv").status_code == 200
    for action in ("approve", "deliver"):
        result = client.post(f"/api/applications/example-job/{action}",
                             json={"fingerprint": package.fingerprint()}, headers=headers)
        assert result.status_code == 200
        assert result.json()["status"] == "blocked"
        assert result.json()["reason_code"] == "posting_unavailable"
    assert service.store.attempt_count("example-job", TrackerName.TEST) == 0


def test_explicit_package_approval_queues_once_without_delivery_in_http(web):
    client, sessions, service = web
    headers = pair(client, sessions)
    fp = client.get("/api/applications/example-job").json()["package"]["fingerprint"]
    service.delivery_adapter = lambda *_: pytest.fail("HTTP never performs delivery")
    first = client.post(
        "/api/applications/example-job/approve", json={"fingerprint": fp}, headers=headers
    )
    second = client.post(
        "/api/applications/example-job/approve", json={"fingerprint": fp}, headers=headers
    )
    assert first.status_code == 200
    assert first.json()["task_id"] == second.json()["task_id"]


def test_source_refresh_queues_work_without_network_or_model_in_request(web):
    client, sessions, service = web
    headers = pair(client, sessions)
    response = client.post(
        "/api/applications/example-job/refresh-details", json={}, headers=headers
    )
    assert response.status_code == 200
    assert response.json()["reason_code"] == "queued"
    assert service.store.get_application("example-job", TrackerName.TEST).status == "enriching"
    detail = client.get("/api/applications/example-job").json()
    assert detail["package"] is None
    assert detail["package_blocker"]["reason_code"] == "enriching"
    assert detail["item"]["cv_url"]


def test_logout_revokes_session_and_blocks_artifacts(web):
    client, sessions, _ = web
    headers = pair(client, sessions)
    assert client.post("/api/logout", json={}, headers=headers).status_code == 200
    assert client.get("/api/applications/example-job/cv").status_code == 401


def test_origin_and_body_type_guards_are_enforced_before_pairing(web):
    client, _, _ = web
    assert (
        client.post(
            "/api/pair",
            content="code=anything",
            headers={"Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"},
        ).status_code
        == 415
    )
    assert (
        client.post("/api/pair", json={"code": "x" * 70000}, headers={"Origin": ORIGIN}).status_code
        == 413
    )
    assert client.get("/api/overview", headers={"Host": "evil.example"}).status_code == 400


def test_public_plain_http_is_not_an_available_configuration(review_service):
    settings = review_service.settings.model_copy(
        update={"web_public_origin": "http://192.168.1.20:8765"}
    )
    with pytest.raises(ValueError, match="HTTPS"):
        create_app(settings, service=review_service)


def test_https_configuration_requires_secure_owner_cookies(review_service):
    settings = review_service.settings.model_copy(
        update={
            "web_public_origin": "https://scout-private.example.test",
            "web_cookie_secure": False,
        }
    )
    with pytest.raises(ValueError, match="Secure cookies"):
        create_app(settings, service=review_service)


def test_private_https_pairing_uses_secure_cookies_and_exact_origin(review_service):
    origin = "https://scout-private.example.test"
    settings = review_service.settings.model_copy(
        update={"web_public_origin": origin, "web_cookie_secure": True}
    )
    sessions = OwnerSessionStore(review_service.store.database_path, {"12345"})
    app = create_app(
        settings, service=review_service, session_store=sessions, tracker=TrackerName.TEST
    )
    with TestClient(app, base_url=origin) as client:
        response = client.post(
            "/api/pair",
            json={"code": sessions.issue_pairing_code("12345")},
            headers={"Origin": origin},
        )
        assert response.status_code == 200
        assert all(cookie.secure for cookie in client.cookies.jar)
        assert all(
            "HttpOnly" in value and "Secure" in value
            for value in response.headers.get_list("set-cookie")
        )
        assert client.get("/api/overview").status_code == 200
        assert client.get("http://scout-private.example.test/api/overview").status_code == 401
        csrf = response.json()["csrf_token"]
        assert (
            client.post(
                "/api/logout", json={}, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/logout", json={}, headers={"Origin": origin, "X-CSRF-Token": csrf}
            ).status_code
            == 200
        )
