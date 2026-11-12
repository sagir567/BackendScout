"""Synthetic fixtures only; Chromium checks opt in with BACKENDSCOUT_BROWSER_SMOKE=1."""

import os
import re
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from backend_scout import application_routes as routes
from backend_scout import employer_forms as forms
from backend_scout.application_package import ReviewedAnswer
from backend_scout.application_routes import ApplicationRoute
from backend_scout.guided_submission import GuidedControlKind, GuidedFormField, GuidedFormSnapshot
from backend_scout.submission import capture_guided_form

FIXTURES = Path(__file__).parent / "fixtures" / "employer_forms"
URL = "https://example.test/jobs/1"


@pytest.mark.parametrize("label", ["Apply today", "Apply now", "Submit application"])
def test_known_final_application_labels_include_employer_apply_today(label):
    assert forms.FINAL.fullmatch(label)


@pytest.mark.parametrize("label", ["Subscribe", "Send message", "Contact us"])
def test_unrelated_commands_are_never_final_application_labels(label):
    assert not forms.FINAL.fullmatch(label)


def fixture_text(name):
    return (FIXTURES / (name + ".html")).read_text(encoding="utf-8")


def verified_route(url=URL, *, employer=None):
    route = ApplicationRoute(
        status="verified",
        reason_code="fixture",
        message="Synthetic fixture only.",
        source_url=employer or url,
        employer_posting_url=employer or url,
        application_url=url,
        company="Example",
        title="Junior Backend Engineer",
        evidence_urls=list(dict.fromkeys([employer or url, url])),
        allowed_origins=list(
            dict.fromkeys(
                ["https://" + routes.urlsplit(item).hostname for item in (employer or url, url)]
            )
        ),
    )
    route._tracker = "test"
    return route


class DOM:
    """Small parser fixture for form selection, not a browser behavior emulator."""

    def __init__(self, items):
        self.items = items

    def count(self):
        return len(self.items)

    def nth(self, index):
        return DOM([self.items[index]])

    def locator(self, selector):
        if selector.startswith("xpath=ancestor::"):
            tag = selector.split("::")[1].split("[")[0]
            parent = self.items[0].find_parent(tag)
            return DOM([parent] if parent else [])
        return DOM([node for root in self.items for node in root.select(selector)])

    def is_visible(self):
        node = self.items[0]
        return not any(
            parent.has_attr("hidden")
            or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", parent.get("style", ""))
            for parent in [node, *node.parents]
            if getattr(parent, "attrs", None) is not None
        )

    def inner_text(self, **kwargs):
        soup = BeautifulSoup(str(self.items[0]), "html.parser")
        for node in soup.select("[hidden],script,style"):
            node.decompose()
        return soup.get_text("\n", strip=True)

    def get_attribute(self, name):
        return self.items[0].get(name)

    def evaluate(self, script):
        assert "el.labels" in script
        node = self.items[0]
        root = node
        while root.parent is not None:
            root = root.parent
        labels = root.select('label[for="' + node.get("id", "") + '"]')
        parent = node.find_parent("label")
        if parent is not None and parent not in labels:
            labels.append(parent)
        return {
            "label": " ".join(
                [
                    *[label.get_text(" ", strip=True) for label in labels],
                    node.get("name", ""),
                    node.get("id", ""),
                    node.get("aria-label", ""),
                ]
            ),
            "disabled": node.has_attr("disabled"),
        }


def fake_page(source, *, url=URL, frames=()):
    soup = BeautifulSoup(source, "html.parser")
    page = SimpleNamespace(url=url, locator=DOM([soup]).locator, soup=soup)
    page.main_frame = page
    page.frames = [page, *frames]
    return page


class Control:
    def __init__(self, name, *, value="", checked=False, **attributes):
        self.attributes = {"name": name, **attributes}
        self.value, self.checked = value, checked
        self.writes = []

    def get_attribute(self, key):
        return self.attributes.get(key)

    def input_value(self, **kwargs):
        return self.value

    def fill(self, value, **kwargs):
        self.value = value
        self.writes.append(value)

    def is_checked(self):
        return self.checked

    def set_checked(self, value, **kwargs):
        self.checked = value
        self.writes.append(value)


def field(label, name, *, kind=GuidedControlKind.TEXT, required=True, value="", checked=False):
    return GuidedFormField(
        field_id="unassigned",
        label=label,
        kind=kind,
        required=required,
        current_value_present=bool(value) or checked,
    ), Control(name, value=value, checked=checked)


def prepare(monkeypatch, pairs, answers=(), *, fill=True):
    fields, controls = [], {}
    for index, (item, control) in enumerate(pairs):
        item = item.model_copy(update={"field_id": f"field-{index}"})
        fields.append(item)
        controls[item.field_id] = control
    captured = SimpleNamespace(
        snapshot=GuidedFormSnapshot(page_url=URL, fields=fields), controls=controls
    )
    monkeypatch.setattr(forms, "capture_guided_form", lambda _: captured)
    view = SimpleNamespace(locator=lambda _: DOM([]))
    package = SimpleNamespace(
        answers=tuple(ReviewedAnswer(key=key, value=value) for key, value in answers)
    )
    _, pending = forms.prepare_reviewed_fields(view, package, fill=fill)
    return pending


@pytest.mark.parametrize("name", ["raft", "ibi", "skyhawk", "richkid", "comeet-form"])
def test_fixture_form_selection_is_scoped_and_accepts_hidden_resume_labels(name):
    page = fake_page(fixture_text(name))
    view = forms.find_application_form(page, verified_route())
    assert view is not None
    assert view.file_control.get_attribute("type") == "file"
    assert view.locator("#contact-form input").count() == 0
    assert view.locator("input[name=contact_email]").count() == 0
    assert view.frames == ()


def test_footer_remains_outside_selected_submission_scope():
    page = fake_page(fixture_text("skyhawk"))
    view = forms.find_application_form(page, verified_route())
    assert view.container.get_attribute("id") == "application-form"
    assert view.locator("textarea").count() == 1
    assert page.locator("textarea").count() == 2


def test_two_resume_forms_or_a_foreign_frame_are_not_silently_selected():
    source = fixture_text("raft")
    page = fake_page(source + source)
    assert forms.find_application_form(page, verified_route()) is None
    foreign = fake_page(source, url="https://untrusted.test/jobs/1")
    page = fake_page("<main>No application form</main>", frames=[foreign])
    assert forms.find_application_form(page, verified_route()) is None


def test_city_is_not_inferred_from_country_or_generic_location(monkeypatch):
    city = field("City", "city")
    pending = prepare(
        monkeypatch, [city], [("identity.country", "Israel"), ("identity.location", "Israel")]
    )
    assert city[1].value == ""
    assert [item["key"] for item in pending] == ["city"]
    pending = prepare(monkeypatch, [city], [("identity.city", "Synthetic City")])
    assert city[1].value == "Synthetic City"
    assert pending == []


def test_conflicting_city_country_field_metadata_requires_input(monkeypatch):
    city = field("City", "country")
    pending = prepare(
        monkeypatch, [city], [("identity.country", "Israel"), ("identity.city", "Synthetic City")]
    )
    assert city[1].writes == []
    assert pending[0]["key"] == "country"


@pytest.mark.parametrize("reviewed", [(), (("marketing", "yes"),), (("marketing", "no"),)])
def test_marketing_remains_off_even_with_a_reviewed_yes(monkeypatch, reviewed):
    checkbox = field(
        "Marketing newsletter",
        "marketing",
        kind=GuidedControlKind.CHECKBOX,
        required=False,
        checked=True,
    )
    pending = prepare(monkeypatch, [checkbox], reviewed)
    assert checkbox[1].checked is False
    assert pending == []


def test_inspection_does_not_toggle_existing_marketing_or_consent(monkeypatch):
    marketing = field(
        "Marketing newsletter",
        "marketing",
        kind=GuidedControlKind.CHECKBOX,
        required=False,
        checked=True,
    )
    consent = field("Privacy consent", "privacy", kind=GuidedControlKind.CHECKBOX, checked=True)
    pending = prepare(monkeypatch, [marketing, consent], fill=False)
    assert marketing[1].checked and consent[1].checked
    assert marketing[1].writes == consent[1].writes == []
    assert "marketing" in {item["key"] for item in pending}


def test_unreviewed_required_consent_is_cleared_and_requires_input(monkeypatch):
    consent = field("Privacy consent", "privacy", kind=GuidedControlKind.CHECKBOX, checked=True)
    pending = prepare(monkeypatch, [consent])
    assert not consent[1].checked
    assert pending[0]["sensitive"]


def test_ambiguous_answers_are_not_filled_and_current_values_not_overwritten(monkeypatch):
    name = field("Full name", "applicant_name")
    pending = prepare(
        monkeypatch, [name], [("Full name", "Synthetic One"), ("applicant_name", "Synthetic Two")]
    )
    assert name[1].writes == []
    assert pending[0]["key"] == "applicant_name"
    name = field("Full name", "applicant_name", value="Existing value")
    pending = prepare(monkeypatch, [name], [("identity.full_name", "Synthetic One")])
    assert name[1].value == "Existing value"
    assert name[1].writes == []
    assert pending


def test_duplicate_question_keys_must_not_reuse_one_answer_for_distinct_questions(monkeypatch):
    name = field("Full name", "full_name")
    first = field("Why this role?", "answer")
    second = field("Describe your leadership experience", "answer")
    pending = prepare(
        monkeypatch,
        [name, first, second],
        [
            ("answer", "Reviewed motivation only"),
            ("identity.full_name", "Synthetic Candidate"),
        ],
    )
    assert name[1].writes == []
    assert first[1].writes == second[1].writes == []
    assert len(pending) == 2
    assert len({item["key"] for item in pending}) == 2
    assert all(item["key"].startswith("ambiguous:") for item in pending)


def test_fieldset_cannot_hide_other_submitted_controls_from_the_review_scope():
    page = fake_page(fixture_text("raft"))
    form = page.soup.select_one("#application-form")
    subset = page.soup.new_tag("fieldset")
    for selector in ("label[for=email]", "label[for=cv]", "button"):
        subset.append(form.select_one(selector).extract())
    form.append(subset)
    view = forms.find_application_form(page, verified_route())
    assert view is not None
    assert view.container.get_attribute("id") == "application-form"
    assert view.locator("input[name=full_name]").count() == 1


def test_hybrid_guard_rejects_candidate_post_to_another_job_on_same_origin():
    from backend_scout.hybrid_portal import HybridPortalAdapter

    context = SimpleNamespace(route=lambda _, handler: setattr(context, "handler", handler))
    adapter = HybridPortalAdapter.__new__(HybridPortalAdapter)
    adapter._guards = {}
    adapter._guard(SimpleNamespace(page=object(), context=context), verified_route())
    outcomes = []
    request = SimpleNamespace(
        url=URL.replace("/1", "/2") + "/applications",
        method="POST",
        is_navigation_request=lambda: False,
    )
    context.handler(
        SimpleNamespace(
            request=request,
            continue_=lambda: outcomes.append("allowed"),
            fallback=lambda: outcomes.append("allowed"),
            abort=lambda: outcomes.append("blocked"),
        )
    )
    assert outcomes == ["blocked"]


@pytest.fixture(scope="module")
def isolated_browser():
    if os.getenv("BACKENDSCOUT_BROWSER_SMOKE") != "1":
        pytest.skip("Opt-in isolated Chromium: BACKENDSCOUT_BROWSER_SMOKE=1")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True, args=["--disable-background-networking"]
        )
        yield browser
        browser.close()


@pytest.fixture
def browser_fixture(isolated_browser, monkeypatch):
    monkeypatch.setattr(
        routes.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    context = isolated_browser.new_context(service_workers="block")
    documents = {
        f"https://example.test/jobs/{path.stem}": path.read_text(encoding="utf-8")
        for path in FIXTURES.glob("*.html")
    }
    employer = "https://example.test/jobs/solaredge"
    iframe = BeautifulSoup(documents[employer], "html.parser").select_one("iframe")["src"]
    documents[iframe] = fixture_text("comeet-form")
    unexpected = []

    def serve(intercept):
        request = intercept.request
        if request.url in documents and request.method == "GET":
            intercept.fulfill(status=200, content_type="text/html", body=documents[request.url])
        else:
            unexpected.append((request.method, request.url))
            intercept.abort()

    context.route("**/*", serve)
    page = context.new_page()

    def load(name):
        url = f"https://example.test/jobs/{name}"
        page.goto(url, wait_until="load")
        return verified_route(url)

    yield page, load, iframe
    context.close()
    assert unexpected == [], "A fixture attempted an unfulfilled request (all were aborted)"


def reviewed_package(**answers):
    identity = {
        "identity.full_name": "Synthetic Candidate",
        "identity.first_name": "Synthetic",
        "identity.last_name": "Candidate",
        "identity.email": "candidate@example.test",
        "identity.phone": "0500000000",
        "identity.linkedin": "https://example.test/in/candidate",
    }
    return SimpleNamespace(
        answers=tuple(
            ReviewedAnswer(key=key, value=value) for key, value in (identity | answers).items()
        )
    )


def test_lever_attachment_display_does_not_invalidate_review(isolated_browser):
    url = "https://jobs.eu.lever.co/example/123/apply"
    context = isolated_browser.new_context(service_workers="block")
    try:
        page = context.new_page()
        context.route("**/*", lambda route: route.fulfill(content_type="text/html", body="""
            <form method="post">
              <input type="email" name="email" required>
              <label>Resume/CV <span><a href="#" class="visible-resume-upload"><span class="filename"
                style="text-transform: uppercase"></span><span
                class="default-label">ATTACH RESUME/CV</span><input id="resume-upload-input"
                name="resume" type="file"></a>
                <span class="resume-upload-working" hidden>Analyzing resume...</span>
              </span></label>
              <a id="privacy" href="/privacy">Privacy policy</a>
              <button type="submit">Submit application</button>
            </form>
            <script>
              document.querySelector('input[type=file]').onchange = ({target}) => {
                target.parentElement.classList.add('has-file');
                target.parentElement.querySelector('.filename').textContent = target.files[0].name;
                target.parentElement.querySelector('.default-label').style.display = 'none';
                document.querySelector('.resume-upload-working').hidden = false;
              };
            </script>
        """) if route.request.url == url and route.request.method == "GET" else route.abort())
        page.goto(url)
        upload = page.locator("input[type=file]")
        view = forms.ApplicationFormView(page, page.main_frame, page.locator("form"), upload, url)
        expected = forms.form_review_signature(view)
        upload.set_input_files({"name": "Example CV.pdf", "mimeType": "application/pdf", "buffer": b"PDF"})
        assert forms.form_review_signature(view) != expected
        assert forms.form_review_signature(view, attached_filename="Example CV.pdf") == expected
        status = page.locator(".resume-upload-working")
        status.evaluate("el => el.textContent = 'New consent required'")
        assert forms.form_review_signature(view, attached_filename="Example CV.pdf") != expected
        status.evaluate("el => el.textContent = 'Analyzing resume...'")
        assert forms.form_review_signature(view, attached_filename="Other.pdf") != expected
        page.locator("#privacy").evaluate("link => link.href = '/new-privacy'")
        assert forms.form_review_signature(view, attached_filename="Example CV.pdf") != expected
        page.locator("#privacy").evaluate("link => link.href = '/privacy'")
        page.locator("form").evaluate("form => form.action = '/different-role'")
        assert forms.form_review_signature(view, attached_filename="Example CV.pdf") != expected
    finally:
        context.close()


@pytest.mark.parametrize("name", ["raft", "ibi", "skyhawk", "richkid", "comeet-form"])
def test_browser_fixture_selection_and_no_submission(browser_fixture, name):
    page, load, _ = browser_fixture
    route = load(name)
    view = forms.find_application_form(page, route)
    assert view is not None
    assert forms.unique_submit(view) is not None
    captured = capture_guided_form(view)
    assert sum(field.kind == GuidedControlKind.FILE for field in captured.snapshot.fields) == 1
    assert all("contact" not in field.label.casefold() for field in captured.snapshot.fields)
    assert page.evaluate("window.sent") == 0


def test_browser_hidden_cv_labels_are_captured_without_clicking_or_uploading(browser_fixture):
    page, load, _ = browser_fixture
    route = load("richkid")
    view = forms.find_application_form(page, route)
    assert not view.file_control.is_visible()
    captured = capture_guided_form(view)
    files = [field for field in captured.snapshot.fields if field.kind == GuidedControlKind.FILE]
    assert len(files) == 1
    assert forms.RESUME.search(files[0].label)
    assert view.file_control.evaluate("el => el.files.length") == 0
    assert page.evaluate("window.sent") == 0


def test_browser_footer_and_custom_questions_remain_isolated(browser_fixture):
    page, load, _ = browser_fixture
    view = forms.find_application_form(page, load("skyhawk"))
    _, pending = forms.prepare_reviewed_fields(view, reviewed_package(), fill=True)
    assert {item["key"] for item in pending} == {"cover_letter"}
    assert page.locator("#contact-form input[name=contact_name]").input_value() == ""
    assert page.locator("#contact-form textarea").input_value() == ""
    assert page.evaluate("window.sent") == 0


def test_browser_lever_checkbox_group_sets_only_exact_reviewed_choice(browser_fixture):
    page, load, _ = browser_fixture
    view = forms.find_application_form(page, load("raft"))
    view.file_control.evaluate("""el => el.closest('form').insertAdjacentHTML('beforeend',
      '<ul><li class="application-question custom-question"><div class="application-label">Professional Python experience?</div>'
      + '<label><input type="checkbox" name="cards[example][field0]" checked>At least two years</label>'
      + '<label><input type="checkbox" name="cards[example][field0]">Less than two years</label></li></ul>')""")
    choices = page.locator('input[name="cards[example][field0]"]')
    _, pending = forms.prepare_reviewed_fields(
        view, reviewed_package(**{"cards[example][field0]": "Less than two years"}), fill=True,
    )
    assert not any(item["key"] == "cards[example][field0]" for item in pending)
    assert not choices.nth(0).is_checked()
    assert choices.nth(1).is_checked()
    assert page.evaluate("window.sent") == 0


def test_browser_recognizes_required_second_attachment_without_upload(browser_fixture):
    page, load, _ = browser_fixture
    route = load("raft")
    view = forms.find_application_form(page, route)
    assert not forms.requires_additional_attachment(view)
    view.file_control.evaluate("""el => el.closest('form').insertAdjacentHTML('beforeend',
      '<label>Grade sheet<input type="file" name="grade_sheet" required></label>')""")
    view = forms.find_application_form(page, route)
    assert view is not None
    assert forms.requires_additional_attachment(view)
    assert page.evaluate("window.sent") == 0


def test_browser_city_not_country_and_marketing_stays_off(browser_fixture):
    page, load, _ = browser_fixture
    view = forms.find_application_form(page, load("ibi"))
    page.locator("#city").evaluate("el => { el.required = true; }")
    package = reviewed_package(
        **{"identity.country": "Israel", "identity.location": "Israel", "Mailing_Consent__c": "yes"}
    )
    _, pending = forms.prepare_reviewed_fields(view, package, fill=True)
    assert "city" in {item["key"] for item in pending}
    assert page.locator("#city").input_value() == ""
    assert not page.locator("#marketing").is_checked()
    assert not page.locator("#privacy").is_checked()
    assert page.evaluate("window.sent") == 0


def test_browser_hidden_marketing_control_cannot_retain_a_default_opt_in(browser_fixture):
    page, load, _ = browser_fixture
    view = forms.find_application_form(page, load("ibi"))
    page.locator("#marketing").evaluate("el => { el.hidden = true; }")
    assert page.locator('label[for="marketing"]').is_visible()
    answers = {
        page.locator("#privacy").get_attribute("name"): "yes",
        "source": page.locator("#source option[value=site]").inner_text(),
    }
    _, pending = forms.prepare_reviewed_fields(view, reviewed_package(**answers), fill=True)
    assert pending == []
    assert not page.locator("#marketing").is_checked()
    assert page.evaluate("window.sent") == 0


def test_browser_only_observed_bound_comeet_frame_is_selected(browser_fixture):
    page, load, iframe = browser_fixture
    employer_route = load("solaredge")
    route = verified_route(iframe, employer=employer_route.application_url)
    view = forms.find_application_form(page, route)
    assert view is not None
    assert view.frame != page.main_frame
    assert view.url == iframe
    assert view.file_control.get_attribute("id") == "cv"
    assert view.frame.evaluate("window.sent") == 0


def test_browser_resolver_guard_chains_to_fixtures_without_any_live_request(browser_fixture):
    from backend_scout.models import Job

    page, _, _ = browser_fixture
    url = "https://example.test/jobs/raft"
    job = Job(
        source="fixture",
        source_url=url,
        company="Example Raft",
        title="Junior Backend Engineer",
        description="Synthetic public fixture.",
    )
    result = routes.resolve_application_route(job, page, tracker="test")
    # The fixture host is deliberately not employer authority; navigation must
    # nevertheless use the earlier fulfillment handler rather than live DNS.
    assert result.reason_code == "untrusted_employer"
    assert page.evaluate("window.sent") == 0
