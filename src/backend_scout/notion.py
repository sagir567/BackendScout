from typing import Any

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

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "NotionClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def build_job_page_properties(
    job: Job,
    status: ApplicationStatus = ApplicationStatus.FOUND,
) -> dict[str, Any]:
    properties: dict[str, Any] = {
        APPLICATIONS_PROPERTY_NAMES["role"]: _title(job.title),
        APPLICATIONS_PROPERTY_NAMES["company"]: _rich_text(job.company),
        APPLICATIONS_PROPERTY_NAMES["status"]: {"status": {"name": status.value}},
        APPLICATIONS_PROPERTY_NAMES["source"]: _rich_text(job.source),
        APPLICATIONS_PROPERTY_NAMES["source_url"]: {"url": job.source_url},
        APPLICATIONS_PROPERTY_NAMES["description"]: _rich_text(job.description),
        APPLICATIONS_PROPERTY_NAMES["discovered_at"]: {
            "date": {"start": job.discovered_at.date().isoformat()}
        },
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


def _title(content: str) -> dict[str, Any]:
    return {"title": [{"type": "text", "text": {"content": _clip_text(content)}}]}


def _rich_text(content: str) -> dict[str, Any]:
    return {"rich_text": [{"type": "text", "text": {"content": _clip_text(content)}}]}


def _clip_text(content: str, limit: int = 1900) -> str:
    if len(content) <= limit:
        return content
    return content[: limit - 3].rstrip() + "..."
