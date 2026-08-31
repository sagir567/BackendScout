from pathlib import Path

from backend_scout.matcher import ScoredJob, score_jobs
from backend_scout.models import CandidateProfile, Job, ManualJobImport
from backend_scout.yaml_files import load_yaml_mapping


def load_manual_job_import(path: Path) -> ManualJobImport:
    return ManualJobImport.model_validate(load_yaml_mapping(path))


def score_manual_job_import(
    profile: CandidateProfile,
    manual_import: ManualJobImport,
) -> list[ScoredJob]:
    return score_jobs(profile, manual_import.jobs)


def job_with_match_result(job: Job, scored_job: ScoredJob) -> Job:
    return job.model_copy(
        update={
            "match_score": scored_job.result.match_score,
            "match_reason": scored_job.result.reason_summary,
        }
    )


def job_dedupe_key(job: Job) -> str:
    return "|".join(
        [
            _normalize_job_identity_part(job.source),
            _normalize_job_identity_part(job.company),
            _normalize_job_identity_part(job.title),
            _normalize_job_identity_part(job.source_url),
        ]
    )


def unique_scored_jobs(scored_jobs: list[ScoredJob]) -> list[ScoredJob]:
    unique_jobs: list[ScoredJob] = []
    seen_keys: set[str] = set()

    for scored_job in scored_jobs:
        dedupe_key = job_dedupe_key(scored_job.job)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        unique_jobs.append(scored_job)

    return unique_jobs


def _normalize_job_identity_part(value: str) -> str:
    return " ".join(value.strip().lower().split())
