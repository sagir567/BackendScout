from backend_scout.contacts import discover_job_post_contacts, discover_official_page_contacts
from backend_scout.models import Job


def test_contact_discovery_only_returns_visible_job_post_contacts() -> None:
    job = Job(
        source="manual",
        source_url="https://example.com/jobs/backend",
        company="Example",
        title="Backend Engineer",
        description="Email jobs@company.example or message https://wa.me/972501234567.",
    )

    contacts = discover_job_post_contacts(job)

    assert {(item.channel, item.value, item.source) for item in contacts} == {
        ("email", "jobs@company.example", "job_post"),
        ("whatsapp", "972501234567", "job_post"),
    }


def test_contact_discovery_marks_explicit_official_page_separately() -> None:
    contacts = discover_official_page_contacts("Contact us at hiring@company.example")

    assert contacts[0].source == "official_site"
    assert contacts[0].value == "hiring@company.example"
