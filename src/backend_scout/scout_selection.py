"""Select unseen jobs before applying the digest size limit."""

from collections.abc import Callable, Iterable
from typing import Any

from backend_scout.matcher import ScoredJob


def select_reviewable_jobs(
    ranked: Iterable[ScoredJob],
    lookup: Callable[[ScoredJob], dict[str, Any] | None],
    limit: int,
) -> tuple[list[tuple[ScoredJob, dict[str, Any] | None]], int]:
    selected = []
    already_reviewed = 0
    seen_identities: set[tuple[str, str]] = set()
    for job in ranked:
        if len(selected) >= limit:
            break
        # Match Notion's company/title fallback even when discovery URLs differ.
        identity = (
            " ".join(job.job.company.casefold().split()),
            " ".join(job.job.title.casefold().split()),
        )
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        page = lookup(job)
        if page is not None:
            status = page.get("properties", {}).get("Status", {}).get("status") or {}
            # A failed delivery may leave a created row in `found`.
            if status.get("name") != "found":
                already_reviewed += 1
                continue
        selected.append((job, page))
    return selected, already_reviewed
