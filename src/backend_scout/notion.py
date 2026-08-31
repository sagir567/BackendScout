from typing import Any, Self

from backend_scout.models import ApplicationStatus, Job

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

    def query_data_source(
        self,
        data_source_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = self._client.post(f"/data_sources/{data_source_id}/query", json=payload or {})
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


def validate_applications_data_source(data_source: dict[str, Any]) -> list[str]:
    """Return human-readable schema problems for the Applications data source."""
    properties = data_source.get("properties") or {}
    problems: list[str] = []

    for key, expected_name in APPLICATIONS_PROPERTY_NAMES.items():
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


def _title(content: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": _clip_text(content)}}]}


def _rich_text(content: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": _clip_text(content)}}]}


def _clip_text(content: str, limit: int = 1900) -> str:
    if len(content) <= limit:
        return content
    return content[: limit - 3].rstrip() + "..."

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
