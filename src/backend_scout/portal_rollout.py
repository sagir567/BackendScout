"""One rollout decision shared by review readiness and actual delivery."""

import json
from contextlib import suppress

from backend_scout.config import TrackerName


def hybrid_delivery_allowed(settings, store, application_id: str, tracker: TrackerName) -> bool:
    if settings.portal_delivery_mode != "hybrid":
        return False
    if tracker != TrackerName.PRODUCTION:
        return True
    canaries = {item.strip() for item in settings.portal_canary_application_ids.split(",") if item.strip()}
    if settings.portal_hybrid_rollout == "test":
        return False
    if settings.portal_hybrid_rollout == "canary":
        return application_id in canaries
    completed = 0
    for identifier in canaries:
        prior = store.latest_delivery(identifier, tracker)
        if not prior or prior["state"] != "submitted":
            continue
        with suppress(ValueError, OSError, TypeError):
            receipt = json.loads(prior["receipt"])
            if isinstance(receipt, dict) and receipt.get("engine") == "employer-hybrid":
                store.verified_artifact(prior["proof_path"], prior["proof_sha256"])
                completed += 1
                if completed >= 3:
                    return True
    return False
