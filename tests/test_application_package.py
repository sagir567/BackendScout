import hashlib
import json
from typing import Any

import pytest
from pydantic import ValidationError

from backend_scout.application_package import (
    SCHEMA_VERSION,
    ApplicationPackage,
    EmailContent,
    ReviewedAnswer,
)
from backend_scout.config import TrackerName


def _data(**changes: Any) -> dict[str, Any]:
    return {
        "application_id": "job-123",
        "tracker": "test",
        "company": "Example Systems",
        "role": "Backend Engineer",
        "official_destination": "https://jobs.example.test/apply/123",
        "delivery_route": "portal",
        "cv_version": "v1",
        "cv_sha256": "a" * 64,
        "answers": [
            {"key": "work_authorization", "value": "Yes"},
            {"key": "availability", "value": "Two weeks"},
        ],
    } | changes


def _email(**changes: Any) -> dict[str, str]:
    return {
        "recipient": "Hiring.Team+backend@example.test",
        "subject": "Application for Backend Engineer",
        "body": "Hello team,\n\nPlease review my CV.\n\nRegards,\nCandidate\n",
    } | changes


def _package(**changes: Any) -> ApplicationPackage:
    return ApplicationPackage.model_validate(_data(**changes))


def test_package_has_only_versioned_material_review_fields() -> None:
    package = _package()

    assert package.schema_version == SCHEMA_VERSION == 1
    assert package.tracker is TrackerName.TEST
    assert set(package.model_dump()) == {
        "schema_version",
        "application_id",
        "tracker",
        "company",
        "role",
        "official_destination",
        "delivery_route",
        "cv_version",
        "cv_sha256",
        "answers",
        "email",
    }
    assert package.email is None
    assert isinstance(package.answers, tuple)
    assert [answer.key for answer in package.answers] == ["availability", "work_authorization"]


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("application_id", "job-456"),
        ("tracker", "production"),
        ("company", "Other Systems"),
        ("role", "Senior Backend Engineer"),
        ("official_destination", "https://jobs.example.test/apply/456"),
        ("delivery_route", "whatsapp"),
        ("cv_version", "v2"),
        ("cv_sha256", "b" * 64),
        (
            "answers",
            [
                {"key": "availability", "value": "Three weeks"},
                {"key": "work_authorization", "value": "Yes"},
            ],
        ),
        (
            "answers",
            [
                {"key": "start_date", "value": "Two weeks"},
                {"key": "work_authorization", "value": "Yes"},
            ],
        ),
        ("answers", [{"key": "availability", "value": "Two weeks"}]),
        ("answers", []),
        ("answers", [*_data()["answers"], {"key": "location", "value": "Remote"}]),
    ],
)
def test_every_material_package_field_changes_fingerprint(field: str, changed: Any) -> None:
    assert _package(**{field: changed}).fingerprint() != _package().fingerprint()


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("recipient", "other@example.test"),
        ("recipient", "hiring.Team+backend@example.test"),
        ("subject", "Application for another role"),
        ("subject", _email()["subject"] + " "),
        ("body", "Hello team,\nPlease review the updated application."),
        ("body", _email()["body"].replace("\n", "\r\n")),
        ("body", _email()["body"] + " "),
    ],
)
def test_every_email_field_changes_fingerprint(field: str, changed: str) -> None:
    original = _package(delivery_route="email", email=_email())
    changed_package = _package(delivery_route="email", email=_email(**{field: changed}))

    assert changed_package.fingerprint() != original.fingerprint()
    assert original.fingerprint() != _package().fingerprint()


def test_known_canonical_json_and_sha256_vector() -> None:
    package = _package(answers=[])
    expected = (
        '{"answers":[],"application_id":"job-123","company":"Example Systems",'
        '"cv_sha256":"' + "a" * 64 + '","cv_version":"v1","delivery_route":"portal",'
        '"email":null,"official_destination":"https://jobs.example.test/apply/123",'
        '"role":"Backend Engineer","schema_version":1,"tracker":"test"}'
    )

    assert package.canonical_json() == expected
    assert (
        package.fingerprint() == "d62c4bd4b6de6871ffed0d301186ec26f96612ebc8f4efb1a80ad7fd76b3a000"
    )
    assert package.fingerprint() == hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert len(package.fingerprint()) == 64
    assert set(package.fingerprint()) <= set("0123456789abcdef")
    future_schema = json.loads(expected) | {"schema_version": 2}
    future_preimage = json.dumps(future_schema, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(future_preimage.encode()).hexdigest() != package.fingerprint()


def test_answer_and_input_field_order_do_not_change_fingerprint() -> None:
    first = _package()
    reordered = dict(reversed(list(_data().items())))
    reordered["answers"] = tuple(reversed(reordered["answers"]))
    second = ApplicationPackage.model_validate(reordered)

    assert first == second
    assert first.canonical_json() == second.canonical_json()
    assert first.fingerprint() == second.fingerprint()
    assert first.fingerprint() == first.fingerprint()


def test_explicit_defaults_and_enum_inputs_do_not_change_fingerprint() -> None:
    assert _package(schema_version=1, tracker=TrackerName.TEST, email=None).fingerprint() == (
        _package().fingerprint()
    )


@pytest.mark.parametrize("answers", [[], ()])
def test_empty_or_omitted_answers_are_allowed(answers: Any) -> None:
    data = _data()
    del data["answers"]
    omitted = ApplicationPackage.model_validate(data)

    assert omitted.answers == _package(answers=answers).answers == ()
    assert omitted.fingerprint() == _package(answers=answers).fingerprint()


@pytest.mark.parametrize("route", ["portal", "email", "whatsapp"])
def test_json_round_trip_preserves_content_and_fingerprint(route: str) -> None:
    package = _package(delivery_route=route, email=_email() if route == "email" else None)
    restored = ApplicationPackage.model_validate_json(package.model_dump_json())

    assert restored == package
    assert restored.fingerprint() == package.fingerprint()


def test_text_is_preserved_without_trimming_casefolding_or_unicode_normalization() -> None:
    answer_text = "  First line\r\nSecond line\n\u05e9\u05dc\u05d5\u05dd e\u0301  "
    package = _package(
        application_id=" job-123 ",
        company=" Example Systems ",
        role=" Backend Engineer ",
        cv_version=" v1 ",
        answers=[{"key": " Exact Key ", "value": answer_text}],
        delivery_route="email",
        email=_email(subject="  Reviewed subject  ", body=answer_text),
    )

    assert package.application_id == " job-123 "
    assert package.company == " Example Systems "
    assert package.role == " Backend Engineer "
    assert package.cv_version == " v1 "
    assert package.answers == (ReviewedAnswer(key=" Exact Key ", value=answer_text),)
    assert package.email is not None
    assert package.email.subject == "  Reviewed subject  "
    assert package.email.body == answer_text
    assert package.email.recipient == _email()["recipient"]
    assert ApplicationPackage.model_validate_json(package.model_dump_json()) == package


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("application_id", "job-123 "),
        ("company", "Example Systems "),
        ("role", "backend Engineer"),
        ("cv_version", "v1 "),
        ("official_destination", "https://JOBS.example.test/apply/123"),
        ("official_destination", "https://jobs.example.test/apply/123?review=1"),
        ("official_destination", "https://jobs.example.test/apply/123#form"),
    ],
)
def test_exact_text_changes_are_bound(field: str, changed: str) -> None:
    assert _package(**{field: changed}).fingerprint() != _package().fingerprint()


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("answer", "answer "),
        ("Yes", "yes"),
        ("line\nend", "line\r\nend"),
        ("caf\u00e9", "cafe\u0301"),
    ],
)
def test_exact_answer_text_changes_are_bound(first: str, second: str) -> None:
    assert (
        _package(answers=[{"key": "k", "value": first}]).fingerprint()
        != _package(answers=[{"key": "k", "value": second}]).fingerprint()
    )


def test_checksum_hex_case_is_the_only_normalization() -> None:
    assert _package(cv_sha256="A" * 64).cv_sha256 == "a" * 64
    assert _package(cv_sha256="A" * 64).fingerprint() == _package().fingerprint()


@pytest.mark.parametrize("field", ["application_id", "company", "role", "cv_version"])
@pytest.mark.parametrize("value", ["", " \t\r\n", None, 123, True, b"text", [], {}, "\ud800"])
def test_required_text_rejects_blank_nonstring_or_unencodable_values(
    field: str, value: Any
) -> None:
    with pytest.raises(ValidationError):
        _package(**{field: value})


@pytest.mark.parametrize(
    "field",
    [
        "application_id",
        "tracker",
        "company",
        "role",
        "official_destination",
        "delivery_route",
        "cv_version",
        "cv_sha256",
    ],
)
def test_required_fields_cannot_be_omitted(field: str) -> None:
    data = _data()
    del data[field]
    with pytest.raises(ValidationError):
        ApplicationPackage.model_validate(data)


@pytest.mark.parametrize("value", [None, "", "1", 0, 2, -1, 1.0, True, False])
def test_only_supported_integer_schema_version_is_accepted(value: Any) -> None:
    with pytest.raises(ValidationError):
        _package(schema_version=value)


@pytest.mark.parametrize("value", ["", "TEST", "staging", None, 1, True])
def test_invalid_trackers_are_rejected(value: Any) -> None:
    with pytest.raises(ValidationError):
        _package(tracker=value)


@pytest.mark.parametrize("value", ["", "sms", "EMAIL", None, 1, True])
def test_invalid_routes_are_rejected(value: Any) -> None:
    with pytest.raises(ValidationError):
        _package(delivery_route=value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        " " * 64,
        "a" * 63,
        "a" * 65,
        "g" * 64,
        "a" * 63 + "\n",
        "0x" + "a" * 64,
        "a" * 64 + " ",
        None,
        123,
        b"a" * 64,
    ],
)
def test_malformed_sha256_is_rejected(value: Any) -> None:
    with pytest.raises(ValidationError):
        _package(cv_sha256=value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "  ",
        None,
        123,
        b"https://example.test",
        "http://example.test/apply",
        "ftp://example.test/apply",
        "mailto:hiring@example.test",
        "/apply/123",
        "//example.test/apply",
        "https:example.test",
        "https:/example.test",
        "https:///example.test",
        "https://",
        "https://?job=123",
        "https://[bad-host",
        "https://example.test:invalid",
        "https://example.test:99999",
        "https://user:password@example.test/apply",
        "https://user@example.test/apply",
        " https://example.test/apply",
        "https://example.test/apply ",
        "https://example.test/job name",
        "https://example.test/\napply",
        "https://example.test/\x00apply",
        "https://example.test/\x7fapply",
        "https://example.test\\@other.test/apply",
    ],
)
def test_malformed_or_unsafe_destination_is_rejected(value: Any) -> None:
    with pytest.raises(ValidationError):
        _package(official_destination=value)


def test_valid_https_destination_is_not_reserialized() -> None:
    url = "https://JOBS.example.test:443/apply/%41?next=%2f&x=1#Review"
    assert _package(official_destination=url).official_destination == url


@pytest.mark.parametrize("route", ["portal", "whatsapp"])
def test_non_email_routes_reject_email_content(route: str) -> None:
    with pytest.raises(ValidationError, match="only allowed for email"):
        _package(delivery_route=route, email=_email())


@pytest.mark.parametrize("content", [None, {}, {"recipient": "hiring@example.test"}])
def test_email_route_requires_complete_email_content(content: Any) -> None:
    with pytest.raises(ValidationError):
        _package(delivery_route="email", email=content)


@pytest.mark.parametrize("field", ["recipient", "subject", "body"])
@pytest.mark.parametrize("value", ["", " \t\n", None, 42, b"text", "\ud800"])
def test_email_text_must_be_nonblank_strict_strings(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        _package(delivery_route="email", email=_email(**{field: value}))


@pytest.mark.parametrize(
    "recipient",
    [
        "not-an-address",
        "local@",
        "@example.test",
        "a@example.test,b@example.test",
        "Hiring <a@example.test>",
        " a@example.test",
        "a@example.test ",
        "a@example.test\r\nBcc:b@example.test",
        "a\x00@example.test",
        "a@@example.test",
        "a@[",
        "a@[x",
        "a@[]",
        "a@[127.0.0.1]",
        "a(comment)@example.test",
        "a@example.test(comment)",
        "a@example..test",
        "a@example.test;",
    ],
)
def test_malformed_multiple_or_unsupported_email_recipients_are_rejected(recipient: str) -> None:
    with pytest.raises(ValidationError):
        _package(delivery_route="email", email=_email(recipient=recipient))


@pytest.mark.parametrize("subject", ["First\rSecond", "First\nSecond", "First\x00Second"])
def test_subject_rejects_header_control_characters(subject: str) -> None:
    with pytest.raises(ValidationError):
        _package(delivery_route="email", email=_email(subject=subject))


@pytest.mark.parametrize("field", ["key", "value"])
@pytest.mark.parametrize("value", ["", " \n\t", None, 42, False, b"text", [], "\ud800"])
def test_answers_require_nonblank_strict_text(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        _package(answers=[{"key": "question", "value": "answer"} | {field: value}])


@pytest.mark.parametrize("answers", [None, "answer", {"question": "answer"}, [None], [{}]])
def test_malformed_answer_collections_are_rejected(answers: Any) -> None:
    with pytest.raises(ValidationError):
        _package(answers=answers)


@pytest.mark.parametrize("second_value", ["first", "different"])
def test_duplicate_answer_keys_are_rejected_not_silently_collapsed(second_value: str) -> None:
    with pytest.raises(ValidationError, match="duplicate answer keys"):
        _package(
            answers=[
                {"key": "question", "value": "first"},
                {"key": "other", "value": "another"},
                {"key": "question", "value": second_value},
            ]
        )


def test_answer_keys_are_exact_and_sorted_case_sensitively() -> None:
    package = _package(
        answers=[
            {"key": "key", "value": "one"},
            {"key": "Key", "value": "two"},
            {"key": " key ", "value": "three"},
        ]
    )
    assert [answer.key for answer in package.answers] == [" key ", "Key", "key"]


@pytest.mark.parametrize("extra", ["approved", "fingerprint", "notion_page_id", "unknown"])
def test_package_rejects_extra_fields(extra: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _package(**{extra: "not part of this contract"})


def test_nested_content_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _package(answers=[{"key": "k", "value": "v", "reviewed": True}])
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        _package(delivery_route="email", email=_email(cc="other@example.test"))


def test_input_containers_and_exported_data_cannot_mutate_package() -> None:
    data = _data(delivery_route="email", email=_email())
    package = ApplicationPackage.model_validate(data)
    fingerprint = package.fingerprint()
    data["company"] = "Changed"
    data["answers"][0]["value"] = "Changed"
    data["answers"].clear()
    data["email"]["body"] = "Changed"
    dumped = package.model_dump()
    dumped["answers"][0]["value"] = "Changed again"
    dumped["email"]["recipient"] = "changed@example.test"

    assert package.fingerprint() == fingerprint
    assert len(package.answers) == 2
    assert package.email is not None
    assert package.email.body == _email()["body"]


def test_supplied_models_are_defensively_revalidated_and_copied() -> None:
    answer = ReviewedAnswer(key="question", value="Reviewed")
    email = EmailContent(**_email())
    package = _package(answers=[answer], delivery_route="email", email=email)

    assert package.answers[0] == answer
    assert package.answers[0] is not answer
    assert package.email == email
    assert package.email is not email
    restored = ApplicationPackage.model_validate(package)
    assert restored == package
    assert restored is not package
    assert restored.answers[0] is not package.answers[0]
    assert restored.email is not package.email


def test_invalid_unvalidated_nested_models_are_not_trusted() -> None:
    with pytest.raises(ValidationError):
        _package(answers=[ReviewedAnswer.model_construct(key="question", value=[])])
    with pytest.raises(ValidationError):
        _package(
            delivery_route="email",
            email=EmailContent.model_construct(recipient="invalid", subject="s", body="b"),
        )


@pytest.mark.parametrize("field", list(ApplicationPackage.model_fields))
def test_all_package_fields_reject_assignment_and_deletion(field: str) -> None:
    package = _package()
    fingerprint = package.fingerprint()
    with pytest.raises(ValidationError, match="frozen"):
        setattr(package, field, "changed")
    with pytest.raises(ValidationError, match="frozen"):
        delattr(package, field)
    assert package.fingerprint() == fingerprint


def test_answer_tuple_and_nested_models_are_immutable() -> None:
    package = _package(delivery_route="email", email=_email())
    fingerprint = package.fingerprint()
    with pytest.raises(TypeError):
        package.answers[0] = ReviewedAnswer(key="changed", value="changed")
    with pytest.raises(AttributeError):
        package.answers.append(ReviewedAnswer(key="changed", value="changed"))
    for field in ("key", "value"):
        with pytest.raises(ValidationError, match="frozen"):
            setattr(package.answers[0], field, "changed")
        with pytest.raises(ValidationError, match="frozen"):
            delattr(package.answers[0], field)
    assert package.email is not None
    for field in ("recipient", "subject", "body"):
        with pytest.raises(ValidationError, match="frozen"):
            setattr(package.email, field, "changed")
        with pytest.raises(ValidationError, match="frozen"):
            delattr(package.email, field)
    assert package.fingerprint() == fingerprint


@pytest.mark.parametrize("deep", [False, True])
def test_model_copy_revalidates_and_defensively_freezes_updates(deep: bool) -> None:
    package = _package()
    answers = [{"key": "updated", "value": "Reviewed"}]
    copied = package.model_copy(update={"answers": answers}, deep=deep)
    fingerprint = copied.fingerprint()
    answers[0]["value"] = "Changed after copying"

    assert copied.answers == (ReviewedAnswer(key="updated", value="Reviewed"),)
    assert copied.fingerprint() == fingerprint != package.fingerprint()
    assert package.model_copy(deep=deep) == package


@pytest.mark.parametrize(
    "update",
    [
        {"company": " "},
        {"unknown": "extra"},
        {"schema_version": 2},
        {"delivery_route": "email"},
        {"answers": [{"key": "k", "value": []}]},
        {"answers": [{"key": "k", "value": "v"}, {"key": "k", "value": "v"}]},
    ],
)
def test_model_copy_cannot_bypass_validation(update: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _package().model_copy(update=update)


def test_nested_model_copy_cannot_bypass_validation() -> None:
    with pytest.raises(ValidationError):
        ReviewedAnswer(key="k", value="v").model_copy(update={"value": []})
    with pytest.raises(ValidationError):
        EmailContent(**_email()).model_copy(update={"recipient": "invalid"})
