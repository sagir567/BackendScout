"""Scoped application forms and reviewed values; no model or submission authority."""

import hashlib
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from backend_scout.guided_submission import GuidedControlKind, is_sensitive_form_field
from backend_scout.package_portal import _identifier, _normal
from backend_scout.submission import capture_guided_form

RESUME = re.compile(r"\b(cv|resume|curriculum vitae)\b|קורות\s*חיים", re.IGNORECASE)
OTHER_FILE = re.compile(r"cover|portfolio|transcript|grade\s*sheet|מכתב|גיליון", re.IGNORECASE)
MARKETING = re.compile(
    r"marketing|newsletter|promotional|future opportunit|extended time|"
    r"שיווק|דיוור|עדכונים.*הטבות|משרות.*עתיד", re.IGNORECASE
)
LEGAL = re.compile(r"terms|agreement|privacy|consent|תנאי|פרטיות|הסכמה", re.IGNORECASE)
FINAL = re.compile(r"^(submit(?: application)?|send(?: application)?|apply(?: now| today)?|"
                   r"הגשת מועמדות|להגשת מועמדות|שליחת מועמדות|שליחת הבקשה|שלח(?:/י)?)$", re.IGNORECASE)
IDENTITY = {
    "full name": "full_name", "name": "full_name", "שם מלא": "full_name",
    "first name": "first_name", "given name": "first_name", "שם פרטי": "first_name",
    "last name": "last_name", "family name": "last_name", "surname": "last_name",
    "שם משפחה": "last_name", "email": "email", "email address": "email",
    "e mail": "email", "כתובת אימייל": "email", 'כתובת דוא"ל': "email",
    "אימייל": "email", 'דוא"ל': "email", "phone": "phone", "mobile": "phone",
    "phone number": "phone", "טלפון": "phone", "טלפון נייד": "phone",
    "linkedin": "linkedin", "linkedin url": "linkedin", "linkedin profile": "linkedin",
    "city": "city", "residence city": "city", "עיר מגורים": "city",
    "country": "country", "country of residence": "country", "מדינה": "country",
    "location": "location", "current location": "location",
}


@dataclass
class ApplicationFormView:
    page: object
    frame: object
    container: object
    file_control: object
    url: str
    frames: tuple = ()
    main_frame: object = None

    def locator(self, *args, **kwargs):
        return self.container.locator(*args, **kwargs)


def field_keys(field, control):
    return list(dict.fromkeys([field.label, *[
        value for key in ("name", "id", "aria-label")
        if (value := control.get_attribute(key))
    ]]))


def question_key(field, control):
    for key in ("name", "id"):
        value = control.get_attribute(key)
        if value and len(value) <= 200:
            return value
    if len(field.label) <= 200:
        return field.label
    return "field:" + hashlib.sha256(field.label.encode()).hexdigest()[:24]


def question(field, control):
    label = field.label
    key = question_key(field, control)
    if key.startswith("cards[") and callable(getattr(control, "evaluate", None)):
        group_label = control.evaluate("""el => {
            const row = el.closest('li.application-question.custom-question');
            const labels = row?.querySelectorAll('.application-label');
            if (labels?.length !== 1 || !labels[0].getClientRects().length) return '';
            return (labels[0].innerText || '').trim();
        }""")
        if group_label:
            label = group_label
    if (field.sensitive or LEGAL.search(label)) and callable(getattr(control, "evaluate", None)):
        links = control.evaluate("""el => Array.from(el.labels || []).flatMap(label =>
            Array.from(label.querySelectorAll('a[href]')).map(a=>a.href))""")
        if links:
            label += " (" + ", ".join(links) + ")"
    return {
        "key": key, "label": label[:2000],
        "required": field.required, "sensitive": field.sensitive or bool(LEGAL.search(label)),
        "options": (["yes", "no"] if field.kind == GuidedControlKind.CHECKBOX
                    else field.options[:100]),
    }


def lever_choice_groups(captured):
    """Recognize Lever cards whose repeated checkbox name represents one question."""
    groups = {}
    for field in captured.snapshot.fields:
        if field.kind == GuidedControlKind.CHECKBOX:
            control = captured.controls[field.field_id]
            key = question_key(field, control)
            if re.fullmatch(r"cards\[[A-Za-z0-9-]+\]\[field\d+\]", key):
                groups.setdefault(key, []).append(field)
    return {key for key, fields in groups.items() if len(fields) > 1}


def _greenhouse_question_id(view, control):
    key = control.get_attribute("id") or ""
    if (urlsplit(view.url).hostname in {"job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"}
            and re.fullmatch(r"question_[0-9]+", key)
            and control.get_attribute("role") == "combobox"):
        return key
    return None


def greenhouse_choices(view, control, *, action=lambda: None, choose=None):
    """Open only the known Greenhouse custom-question widget, not arbitrary controls."""
    key = _greenhouse_question_id(view, control)
    if key is None or control.get_attribute("aria-expanded") != "false":
        return []
    try:
        expanded = view.frame.locator(f'[id="{key}"][aria-expanded="true"]')
        for _ in range(3):
            if control.get_attribute("aria-expanded") != "false":
                break
            action()
            control.click(timeout=2000)
            try:
                expanded.wait_for(state="attached", timeout=900)
                break
            except PlaywrightTimeout:
                # The server-rendered field may be visible before React handles clicks.
                continue
        list_id = f"react-select-{key}-listbox"
        if control.get_attribute("aria-controls") != list_id:
            return []
        menu = view.frame.locator(f'[id="{list_id}"][role="listbox"]')
        if menu.count() != 1 or not menu.is_visible():
            return []
        options = menu.get_by_role("option")
        labels = options.all_inner_texts()
        if not labels or len(labels) > 50 or len(set(labels)) != len(labels) or any(
            not label or len(label) > 1000 for label in labels
        ):
            return []
        if choose is not None:
            if choose not in labels:
                return []
            target = menu.get_by_role("option", name=choose, exact=True)
            if target.count() != 1:
                return []
            action()
            target.click(timeout=2000)
        return labels
    finally:
        if control.get_attribute("aria-expanded") == "true":
            action()
            control.press("Escape", timeout=2000)


def _greenhouse_selected(control):
    return control.evaluate("""el => {
        const box = el.closest('.select__control');
        const labels = box?.querySelectorAll('.select__single-value');
        return el.getAttribute('aria-expanded') === 'false' && labels?.length === 1
            ? labels[0].innerText : '';
    }""")


def form_review_signature(view, *, attached_filename=None, action=lambda: None,
                          captured=None, observed_choices=None):
    """Bind question meaning and consent links, excluding changing field values."""
    if captured is None:
        captured = capture_guided_form(view)
    fields = []
    for field in captured.snapshot.fields:
        control = captured.controls[field.field_id]
        options = field.options
        if field.kind == GuidedControlKind.COMBOBOX:
            options = (greenhouse_choices(view, control, action=action)
                       if observed_choices is None else observed_choices[field.field_id])
        fields.append({
            "key": question_key(field, control), "label": field.label,
            "kind": field.kind.value, "required": field.required,
            "options": options,
            "sensitive": field.sensitive,
        })
    links = view.locator("a[href]").evaluate_all("els=>els.map(e=>({text:e.innerText,url:e.href}))")
    if attached_filename and re.fullmatch(r"jobs(?:\.eu)?\.lever\.co", urlsplit(view.url).hostname or ""):
        display = view.file_control.evaluate("""(cv, {links, filename}) => {
            const link = cv.parentElement;
            if (!link?.matches('a.visible-resume-upload.has-file[href="#"]') ||
                !cv.matches('input#resume-upload-input[name="resume"][type="file"]') ||
                cv.files?.length !== 1 || cv.files[0].name !== filename) return null;
            const label = link.querySelector(':scope > .default-label');
            const shown = link.querySelector(':scope > .filename');
            const escaped = document.createElement('span');
            escaped.textContent = filename;
            if (label?.textContent !== 'ATTACH RESUME/CV' || shown?.textContent !== escaped.innerHTML ||
                link.innerText.trim() !== shown.innerText.trim()) return null;
            const index = [...(cv.form?.querySelectorAll('a[href]') || [])].indexOf(link);
            if (links[index]?.url === link.href && links[index]?.text === link.innerText) {
                const fieldLabel = cv.labels?.length === 1 ? cv.labels[0] : null;
                const fieldWrapper = link.parentElement;
                const status = fieldLabel?.contains(fieldWrapper)
                    ? [...fieldWrapper.children].filter(el =>
                        el.matches('.resume-upload-working,.resume-upload-failure,.resume-upload-success')
                        && el.getClientRects().length > 0).map(el => el.innerText.trim())
                    : [];
                const known = ['Analyzing resume...', "Couldn't auto-read resume.", 'Success!'];
                return {index, before: label.textContent, after: link.innerText,
                    status: status.length === 1 && known.includes(status[0]) ? status[0] : ''};
            }
            return null;
        }""", {"links": links, "filename": attached_filename})
        if display:
            links[display["index"]]["text"] = display["before"]
            suffix = " " + " ".join(f'{display["after"]} {display["status"]}'.split())
            for field in fields:
                if field["key"] == "resume" and field["kind"] == "file" and field["label"].endswith(suffix):
                    field["label"] = field["label"].removesuffix(suffix) + " " + display["before"]
    form = view.file_control.evaluate("""el => {
        const f=el.form;
        return f ? {action:f.action,method:f.method,target:f.target,enctype:f.enctype,
            controls:Array.from(f.querySelectorAll('button,input[type=submit]')).map(b=>({
                action:b.getAttribute('formaction'),target:b.getAttribute('formtarget'),
                text:b.innerText||b.value||''}))} : null;
    }""")
    payload = {"fields": fields, "links": links, "form": form}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def find_application_form(page, route):
    """Choose a unique CV-associated form, never an unrelated footer widget."""
    from backend_scout.application_routes import route_allows_url

    candidates = []
    scopes = [page] + [f for f in page.frames if f != page.main_frame]
    for scope in scopes:
        if not route_allows_url(route, scope.url):
            continue
        files = scope.locator('input[type="file"]')
        for index in range(min(files.count(), 30)):
            upload = files.nth(index)
            meta = upload.evaluate("""el => ({label:Array.from(el.labels || [])
                .map(l=>l.innerText).join(' ')+' '+(el.getAttribute('aria-label') || '')
                +' '+el.name+' '+el.id, disabled:el.disabled})""")
            if meta["disabled"] or OTHER_FILE.search(meta["label"]):
                continue
            container = upload.locator('xpath=ancestor::form[1]')
            if not container.count():
                container = upload.locator('xpath=ancestor::fieldset[1]')
            if not container.count() or not container.is_visible():
                continue
            context = container.inner_text(timeout=2000)
            if not RESUME.search(meta["label"]) and not RESUME.search(context):
                continue
            if (container.locator('input[type="email"], input[name*="email" i]').count() == 0
                and not re.search(r"email|אימייל|דוא", context, re.IGNORECASE)):
                continue
            candidates.append(ApplicationFormView(page, scope, container, upload, scope.url))
    return candidates[0] if len(candidates) == 1 else None


def requires_additional_attachment(view):
    """A CV-only package cannot satisfy a distinct required file field."""
    return bool(view.file_control.evaluate("""cv => {
        const scope = cv.closest('form') || cv.closest('fieldset');
        return [...(scope?.querySelectorAll('input[type="file"]:not(:disabled)') || [])]
          .some(el => el !== cv && (el.required || el.getAttribute('aria-required') === 'true'));
    }"""))


def _checked(control, desired, action):
    if control.is_checked() == desired:
        return
    action()
    try:
        control.set_checked(desired, timeout=1500)
    except PlaywrightError:
        if control.is_checked() == desired:
            return
        action()
        # Click the label's non-link text, not a privacy-policy hyperlink.
        label = control.locator('xpath=ancestor::label[1]')
        if label.count():
            label.click(position={"x": 4, "y": 4}, timeout=1500)
        else:
            element_id = control.get_attribute("id")
            if not element_id or not re.fullmatch(r"[A-Za-z0-9_.:-]+", element_id):
                raise ValueError("Custom choice needs a verified visible label") from None
            control.page.locator(f'label[for="{element_id}"]').last.click(timeout=1500)
    if control.is_checked() != desired:
        raise ValueError("Choice did not retain the reviewed value")


def prepare_reviewed_fields(view, package, *, fill, action=lambda: None):
    captured = capture_guided_form(view)
    checkbox_groups = lever_choice_groups(captured)
    supplied = {_normal(answer.key): answer.value for answer in package.answers}
    identity = {answer.key: answer.value for answer in package.answers
                if answer.key.startswith("identity.")}
    pending = []
    radio_groups = {}
    by_key = {}
    for field in captured.snapshot.fields:
        if field.kind not in {GuidedControlKind.RADIO, GuidedControlKind.FILE}:
            control = captured.controls[field.field_id]
            key = question_key(field, control)
            if key not in checkbox_groups:
                by_key.setdefault(key, []).append((field, control))
    duplicates = [pair for pairs in by_key.values() if len(pairs) > 1 for pair in pairs]
    if duplicates:
        return captured, [question(field, control) | {"key": "ambiguous:" + field.field_id}
                          for field, control in duplicates]
    for field in captured.snapshot.fields:
        control = captured.controls[field.field_id]
        if field.kind == GuidedControlKind.FILE:
            continue
        keys = field_keys(field, control)
        expected = supplied.get(_normal(question_key(field, control)))
        values = {supplied[_normal(k)] for k in keys if _normal(k) in supplied}
        if len(values) > 1:
            pending.append(question(field, control))
            continue
        if expected is None and values:
            expected = next(iter(values))
        sensitive = field.sensitive or any(is_sensitive_form_field(_identifier(k)) for k in keys)
        if expected is None and not sensitive:
            matches = {IDENTITY[_identifier(k)] for k in keys if _identifier(k) in IDENTITY}
            if len(matches) == 1:
                expected = identity.get("identity." + next(iter(matches)))
        if field.kind == GuidedControlKind.RADIO or question_key(field, control) in checkbox_groups:
            group = control.get_attribute("name") or question_key(field, control)
            radio_groups.setdefault(group, []).append((field, control, expected))
            continue
        if field.kind == GuidedControlKind.CHECKBOX:
            if MARKETING.search(" ".join(keys)):
                if fill:
                    _checked(control, False, action)
                if control.is_checked():
                    pending.append(question(field, control))
                continue
            if expected is None:
                if fill and control.is_checked():
                    _checked(control, False, action)
                if field.required:
                    pending.append(question(field, control))
                continue
            desired = _normal(expected) in {"yes", "true", _normal(field.label)}
            if _normal(expected) not in {"yes", "true", "no", "false", _normal(field.label)}:
                pending.append(question(field, control))
                continue
            if fill:
                _checked(control, desired, action)
            if control.is_checked() != desired or (field.required and not desired):
                pending.append(question(field, control))
            continue
        if expected is None:
            if (field.required or field.current_value_present
                    or field.kind == GuidedControlKind.COMBOBOX
                    and _greenhouse_question_id(view, control) and _greenhouse_selected(control)):
                pending.append(question(field, control))
            continue
        if field.kind == GuidedControlKind.COMBOBOX:
            recognized = _greenhouse_question_id(view, control)
            if fill and recognized and not _greenhouse_selected(control):
                greenhouse_choices(view, control, action=action, choose=expected)
            if not recognized or _greenhouse_selected(control) != expected:
                pending.append(question(field, control))
        elif field.kind == GuidedControlKind.SELECT:
            if fill and not field.current_value_present and expected in field.options:
                action()
                control.select_option(label=expected, timeout=3000)
            selected = control.evaluate("el => el.selectedOptions?.[0]?.label || ''")
            if selected != expected:
                pending.append(question(field, control))
        else:
            current = control.input_value(timeout=2000)
            if fill and not current:
                action()
                control.fill(expected, timeout=3000)
                current = control.input_value(timeout=2000)
            if current != expected:
                pending.append(question(field, control))
    for choices in radio_groups.values():
        prompt = question(choices[0][0], choices[0][1]) | {
            "options": [field.label for field, _, _ in choices],
            "required": any(field.required for field, _, _ in choices),
        }
        values = {expected for _, _, expected in choices if expected is not None}
        if len(values) != 1:
            if any(f.required or f.current_value_present for f, _, _ in choices):
                pending.append(prompt)
            continue
        expected = next(iter(values))
        targets = [c for f, c, _ in choices if expected in {f.label, c.get_attribute("value")}]
        if len(targets) != 1:
            pending.append(prompt)
            continue
        if fill:
            for field, control, _ in choices:
                if field.kind == GuidedControlKind.CHECKBOX and control is not targets[0] and control.is_checked():
                    _checked(control, False, action)
            _checked(targets[0], True, action)
        if not targets[0].is_checked() or any(c is not targets[0] and c.is_checked() for _, c, _ in choices):
            pending.append(prompt)
    # A rerender may introduce new required controls after the captured snapshot.
    if not pending:
        invalid = view.locator('input:invalid:not([type="file"]),textarea:invalid,select:invalid')
        if any(invalid.nth(i).is_visible() for i in range(invalid.count())):
            pending.append({"key": "form_changed", "label": "Required fields changed; inspect the current form.",
                            "required": True, "options": [], "sensitive": False})
    unique = {item["key"]: item for item in pending}
    return captured, list(unique.values())


def unique_submit(view):
    controls = view.locator('button, input[type="submit"]')
    eligible = []
    for i in range(controls.count()):
        control = controls.nth(i)
        if not control.is_visible() or not control.is_enabled():
            continue
        label = control.inner_text() if control.evaluate("el=>el.tagName") == "BUTTON" else control.get_attribute("value")
        label = re.sub(r"[^\w\s/]+", "", label or "", flags=re.UNICODE).strip()
        if FINAL.fullmatch(label):
            eligible.append(control)
    return eligible[0] if len(eligible) == 1 else None


def proposed_notes(evidence, job, view):
    """Evidence-only draft text enters package review, never consent or eligibility."""
    selected = []
    for experience in evidence.experience:
        for item in experience.bullets:
            if any(word in item.text.casefold() for word in ("api", "backend", "pipeline")):
                selected.append(item.text)
    selected = selected[:2]
    if not selected:
        return {}
    note = (f"I would like to apply for the {job.title} position at {job.company}. "
            + " ".join(selected) + " I would welcome the opportunity to discuss this experience.")
    results = {}
    captured = capture_guided_form(view)
    for field in captured.snapshot.fields:
        control = captured.controls[field.field_id]
        if (field.kind == GuidedControlKind.TEXTAREA and not field.sensitive
                and re.search(r"cover|personal note|מכתב נלווה|ספר.י.*עצמך|מתאים לתפקיד", field.label, re.IGNORECASE)):
            results[question_key(field, control)] = note
    return results
