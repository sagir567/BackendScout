from backend_scout.models import ApplicationStatus


def test_application_statuses_include_approval_gates() -> None:
    assert ApplicationStatus.APPROVED_TO_TAILOR.value == "approved_to_tailor"
    assert ApplicationStatus.APPROVED_TO_SUBMIT.value == "approved_to_submit"
    assert ApplicationStatus.SUBMITTED.value == "submitted"

