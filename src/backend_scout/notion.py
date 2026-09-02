from typing import Any, Self

from backend_scout.models import ApplicationDigestItem, ApplicationStatus, Job

NOTION_BASE_URL = "https://api.notion.com/v1"

APPLICATIONS_PROPERTY_NAMES = {
    "role": "Role",
    "company": "Company",
    "status": "Status",
    "source": "Source",
    "source_url": "Source URL",
    "location": "Location",
    "remote_policy": "Remote Policy",
    "employment_type": "Employment Type",
    "salary": "Salary",
    "match_score": "Match Score",
    "required_skills": "Required Skills",
    "years_experience": "Years Experience",
    "match_reason": "Match Reason",
    "description": "Description",
    "discovered_at": "Discovered At",
    "submission_channel": "Submission Channel",
    "contact_source": "Contact Source",
    "submission_record": "Submission Record",
}

APPLICATIONS_PROPERTY_TYPES = {
    "role": "title",
    "company": "rich_text",
    "status": "status",
    "source": "rich_text",
    "source_url": "url",
    "location": "rich_text",
    "remote_policy": "rich_text",
    "employment_type": "rich_text",
    "salary": "rich_text",
    "match_score": "number",
    "required_skills": "multi_select",
    "years_experience": "rich_text",
    "match_reason": "rich_text",
    "description": "rich_text",
    "discovered_at": "date",
    "submission_channel": "select",
    "contact_source": "rich_text",
    "submission_record": "rich_text",
}


class NotionClient:
    def __init__(
        self,
        api_key: str,
        api_version: str = "2026-03-11",
        base_url: str = NOTION_BASE_URL,
    ) -> None:
        import httpx

        self._client = httpx.Client(
            base_url=base_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Notion-Version": api_version,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        response = self._client.get(f"/data_sources/{data_source_id}")
        response.raise_for_status()
        return response.json()

    def update_data_source_properties(
        self,
        data_source_id: str,
        properties: dict[str, Any],
    ) -> dict[str, Any]:
        response = self._client.patch(
            f"/data_sources/{data_source_id}",
            json={"properties": properties},
        )
        response.raise_for_status()
        return response.json()

    def query_data_source(
        self,
        data_source_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._client.post(f"/data_sources/{data_source_id}/query", json=payload or {})
        response.raise_for_status()
        return response.json()

    def retrieve_page(self, page_id: str) -> dict[str, Any]:
        response = self._client.get(f"/pages/{page_id}")
        response.raise_for_status()
        return response.json()

    def create_job_page(
        self,
        data_source_id: str,
        job: Job,
        status: ApplicationStatus = ApplicationStatus.FOUND,
    ) -> dict[str, Any]:
        response = self._client.post(
            "/pages",
            json={
                "parent": {
                    "type": "data_source_id",
                    "data_source_id": data_source_id,
                },
                "properties": build_job_page_properties(job, status),
            },
        )
        response.raise_for_status()
        return response.json()

    def update_job_page(self, page_id: str, job: Job) -> dict[str, Any]:
        response = self._client.patch(
            f"/pages/{page_id}",
            json={"properties": build_job_page_properties(job, include_status=False)},
        )
        response.raise_for_status()
        return response.json()

    def update_application_status(
        self,
        page_id: str,
        status: ApplicationStatus,
    ) -> dict[str, Any]:
        response = self._client.patch(
            f"/pages/{page_id}",
            json={
                "properties": {
                    APPLICATIONS_PROPERTY_NAMES["status"]: {"status": {"name": status.value}}
                }
            },
        )
        response.raise_for_status()
        return response.json()

    def record_submission(
        self,
        page_id: str,
        channel: str,
        contact_source: str,
        record: str,
    ) -> dict[str, Any]:
        response = self._client.patch(
            f"/pages/{page_id}",
            json={
                "properties": {
                    APPLICATIONS_PROPERTY_NAMES["status"]: {
                        "status": {"name": ApplicationStatus.SUBMITTED.value}
                    },
                    APPLICATIONS_PROPERTY_NAMES["submission_channel"]: {"select": {"name": channel}},
                    APPLICATIONS_PROPERTY_NAMES["contact_source"]: _rich_text(contact_source),
                    APPLICATIONS_PROPERTY_NAMES["submission_record"]: _rich_text(record),
                }
            },
        )
        response.raise_for_status()
        return response.json()

    def find_job_page(
        self,
        data_source_id: str,
        job: Job,
    ) -> dict[str, Any] | None:
        if match := _find_job_page_by_source_url(self, data_source_id, job):
            return match
        return _find_job_page_by_company_and_role(self, data_source_id, job)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def build_job_page_properties(
    job: Job,
    status: ApplicationStatus = ApplicationStatus.FOUND,
    include_status: bool = True,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        APPLICATIONS_PROPERTY_NAMES["role"]: _title(job.title),
        APPLICATIONS_PROPERTY_NAMES["company"]: _rich_text(job.company),
        APPLICATIONS_PROPERTY_NAMES["source"]: _rich_text(job.source),
        APPLICATIONS_PROPERTY_NAMES["source_url"]: {"url": job.source_url},
        APPLICATIONS_PROPERTY_NAMES["description"]: _rich_text(job.description),
    }

    if include_status:
        properties[APPLICATIONS_PROPERTY_NAMES["status"]] = {"status": {"name": status.value}}
        properties[APPLICATIONS_PROPERTY_NAMES["discovered_at"]] = {
            "date": {"start": job.discovered_at.date().isoformat()}
        }

    optional_values = {
        "location": job.location,
        "remote_policy": job.remote_policy,
        "employment_type": job.employment_type,
        "salary": job.salary_text,
        "years_experience": job.years_experience,
        "match_reason": job.match_reason,
    }
    for key, value in optional_values.items():
        if value:
            properties[APPLICATIONS_PROPERTY_NAMES[key]] = _rich_text(value)

    if job.match_score is not None:
        properties[APPLICATIONS_PROPERTY_NAMES["match_score"]] = {"number": job.match_score}

    if job.required_skills:
        properties[APPLICATIONS_PROPERTY_NAMES["required_skills"]] = {
            "multi_select": [{"name": skill} for skill in job.required_skills]
        }

    return properties


def upsert_job_page(
    client: NotionClient,
    data_source_id: str,
    job: Job,
    status: ApplicationStatus = ApplicationStatus.FOUND,
) -> tuple[str, dict[str, Any]]:
    existing_page = client.find_job_page(data_source_id, job)
    if existing_page:
        return "updated", client.update_job_page(existing_page["id"], job)
    return "created", client.create_job_page(data_source_id, job, status=status)


def list_jobs_by_status(
    client: NotionClient,
    data_source_id: str,
    status: ApplicationStatus,
    page_size: int = 20,
) -> list[ApplicationDigestItem]:
    query_result = client.query_data_source(
        data_source_id,
        {
            "page_size": page_size,
            "filter": {
                "property": APPLICATIONS_PROPERTY_NAMES["status"],
                "status": {"equals": status.value},
            },
        },
    )
    return [application_digest_item_from_page(page) for page in query_result.get("results", [])]


def update_application_status(
    client: NotionClient,
    page_id: str,
    status: ApplicationStatus,
) -> dict[str, Any]:
    return client.update_application_status(page_id, status)


def application_digest_item_from_page(page: dict[str, Any]) -> ApplicationDigestItem:
    properties = page.get("properties", {})
    return ApplicationDigestItem(
        notion_page_id=page["id"],
        company=_extract_rich_text_value(properties, "company") or "Unknown Company",
        title=_extract_title_value(properties, "role") or "Untitled Role",
        status=ApplicationStatus(_extract_status_value(properties, "status") or ApplicationStatus.FOUND.value),
        source=_extract_rich_text_value(properties, "source") or "unknown",
        source_url=_extract_url_value(properties, "source_url") or "",
        location=_extract_rich_text_value(properties, "location"),
        remote_policy=_extract_rich_text_value(properties, "remote_policy"),
        employment_type=_extract_rich_text_value(properties, "employment_type"),
        salary_text=_extract_rich_text_value(properties, "salary"),
        match_score=_extract_number_value(properties, "match_score"),
        match_reason=_extract_rich_text_value(properties, "match_reason"),
        required_skills=_extract_multi_select_values(properties, "required_skills"),
    )


def job_from_application_page(page: dict[str, Any]) -> Job:
    properties = page.get("properties", {})
    return Job(
        source=_extract_rich_text_value(properties, "source") or "unknown",
        source_url=_extract_url_value(properties, "source_url") or "",
        company=_extract_rich_text_value(properties, "company") or "Unknown Company",
        title=_extract_title_value(properties, "role") or "Untitled Role",
        description=_extract_rich_text_value(properties, "description") or "",
        location=_extract_rich_text_value(properties, "location"),
        remote_policy=_extract_rich_text_value(properties, "remote_policy"),
        employment_type=_extract_rich_text_value(properties, "employment_type"),
        salary_text=_extract_rich_text_value(properties, "salary"),
        match_score=_extract_number_value(properties, "match_score"),
        match_reason=_extract_rich_text_value(properties, "match_reason"),
        required_skills=_extract_multi_select_values(properties, "required_skills"),
        years_experience=_extract_rich_text_value(properties, "years_experience"),
    )


def validate_applications_data_source(data_source: dict[str, Any]) -> list[str]:
    """Return human-readable schema problems for the Applications data source."""
    properties = data_source.get("properties") or {}
    problems: list[str] = []

    for key, expected_name in APPLICATIONS_PROPERTY_NAMES.items():
        if key in {"submission_channel", "contact_source", "submission_record"}:
            continue
        expected_type = APPLICATIONS_PROPERTY_TYPES[key]
        notion_property = properties.get(expected_name)
        if not notion_property:
            problems.append(f"Missing property: {expected_name} ({expected_type})")
            continue

        actual_type = notion_property.get("type")
        if actual_type != expected_type:
            problems.append(
                f"Property {expected_name} should be {expected_type}, got {actual_type or 'unknown'}"
            )

    return problems


def validate_submission_data_source(data_source: dict[str, Any]) -> list[str]:
    properties = data_source.get("properties", {})
    problems = []
    for key in ("submission_channel", "contact_source", "submission_record"):
        name = APPLICATIONS_PROPERTY_NAMES[key]
        actual_type = properties.get(name, {}).get("type")
        if actual_type != APPLICATIONS_PROPERTY_TYPES[key]:
            problems.append(f"{name} must be a {APPLICATIONS_PROPERTY_TYPES[key]} property")
    return problems


def missing_submission_property_definitions(data_source: dict[str, Any]) -> dict[str, Any]:
    """Return only additive submission columns; never replace an existing column."""
    properties = data_source.get("properties") or {}
    definitions: dict[str, Any] = {}
    for key, definition in {
        "submission_channel": {
            "select": {
                "options": [
                    {"name": "email", "color": "blue"},
                    {"name": "portal", "color": "green"},
                    {"name": "whatsapp", "color": "yellow"},
                ]
            }
        },
        "contact_source": {"rich_text": {}},
        "submission_record": {"rich_text": {}},
    }.items():
        name = APPLICATIONS_PROPERTY_NAMES[key]
        existing = properties.get(name)
        if existing is None:
            definitions[name] = definition
        elif existing.get("type") != APPLICATIONS_PROPERTY_TYPES[key]:
            raise ValueError(
                f"Existing property {name} has type {existing.get('type')}; "
                f"expected {APPLICATIONS_PROPERTY_TYPES[key]}"
            )
    return definitions


def _title(content: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": _clip_text(content)}}]}


def _rich_text(content: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": _clip_text(content)}}]}


def _clip_text(content: str, limit: int = 1900) -> str:
    if len(content) <= limit:
        return content
    return content[: limit - 3].rstrip() + "..."


def _extract_title_value(properties: dict[str, Any], property_key: str) -> str | None:
    title_items = properties.get(APPLICATIONS_PROPERTY_NAMES[property_key], {}).get("title", [])
    return _join_notion_text(title_items)


def _extract_rich_text_value(properties: dict[str, Any], property_key: str) -> str | None:
    rich_text_items = properties.get(APPLICATIONS_PROPERTY_NAMES[property_key], {}).get("rich_text", [])
    return _join_notion_text(rich_text_items)


def _extract_status_value(properties: dict[str, Any], property_key: str) -> str | None:
    status_value = properties.get(APPLICATIONS_PROPERTY_NAMES[property_key], {}).get("status")
    if not isinstance(status_value, dict):
        return None
    return status_value.get("name")


def _extract_url_value(properties: dict[str, Any], property_key: str) -> str | None:
    value = properties.get(APPLICATIONS_PROPERTY_NAMES[property_key], {}).get("url")
    return value if isinstance(value, str) and value.strip() else None


def _extract_number_value(properties: dict[str, Any], property_key: str) -> int | None:
    value = properties.get(APPLICATIONS_PROPERTY_NAMES[property_key], {}).get("number")
    return value if isinstance(value, int) else None


def _extract_multi_select_values(properties: dict[str, Any], property_key: str) -> list[str]:
    items = properties.get(APPLICATIONS_PROPERTY_NAMES[property_key], {}).get("multi_select", [])
    return [item["name"] for item in items if isinstance(item, dict) and item.get("name")]


def _join_notion_text(items: list[dict[str, Any]]) -> str | None:
    parts = []
    for item in items:
        plain_text = item.get("plain_text")
        if isinstance(plain_text, str) and plain_text:
            parts.append(plain_text)
            continue

        text_content = item.get("text", {}).get("content")
        if isinstance(text_content, str) and text_content:
            parts.append(text_content)

    joined = "".join(parts).strip()
    return joined or None

def _find_rich_text_equals_filter(property_name: str, value: str) -> dict[str, Any]:
    return {"property": property_name, "rich_text": {"equals": value}}


def _find_url_equals_filter(property_name: str, value: str) -> dict[str, Any]:
    return {"property": property_name, "url": {"equals": value}}


def _find_title_equals_filter(property_name: str, value: str) -> dict[str, Any]:
    return {"property": property_name, "title": {"equals": value}}


def _first_result(query_result: dict[str, Any]) -> dict[str, Any] | None:
    results = query_result.get("results", [])
    return results[0] if results else None


def _compound_and_filter(*filters: dict[str, Any]) -> dict[str, Any]:
    return {"and": list(filters)}


def _query_single_job_page(
    client: NotionClient,
    data_source_id: str,
    query_filter: dict[str, Any],
) -> dict[str, Any] | None:
    query_result = client.query_data_source(
        data_source_id,
        {"page_size": 1, "filter": query_filter},
    )
    return _first_result(query_result)


def _find_job_page_by_source_url(
    self: NotionClient,
    data_source_id: str,
    job: Job,
) -> dict[str, Any] | None:
    return _query_single_job_page(
        self,
        data_source_id,
        _query_filter_for_source_url(job),
    )


def _find_job_page_by_company_and_role(
    self: NotionClient,
    data_source_id: str,
    job: Job,
) -> dict[str, Any] | None:
    return _query_single_job_page(
        self,
        data_source_id,
        _compound_and_filter(
            _find_rich_text_equals_filter(APPLICATIONS_PROPERTY_NAMES["company"], job.company),
            _find_title_equals_filter(APPLICATIONS_PROPERTY_NAMES["role"], job.title),
        ),
    )


def _query_filter_for_source_url(job: Job) -> dict[str, Any]:
    return _compound_and_filter(
        _find_url_equals_filter(APPLICATIONS_PROPERTY_NAMES["source_url"], job.source_url),
        _find_rich_text_equals_filter(APPLICATIONS_PROPERTY_NAMES["company"], job.company),
        _find_title_equals_filter(APPLICATIONS_PROPERTY_NAMES["role"], job.title),
    )
