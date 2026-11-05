"""One private pricing and limit configuration for the shared workflow ledger."""

import json

from backend_scout.application_store import ApplicationStore
from backend_scout.browser_budget import BrowserBudget


def configured_budget(settings):
    ApplicationStore(settings.workflow_database_path, settings.cv_archive_root.resolve().parent)
    prices = {}
    if settings.portal_ai_prices_path is not None:
        try:
            candidate = json.loads(settings.portal_ai_prices_path.read_text())
            if isinstance(candidate, dict):
                prices = candidate
        except (OSError, ValueError):
            pass
    options = {
        "monthly_limit_nis": settings.portal_ai_monthly_limit_nis,
        "application_limit_nis": settings.portal_ai_application_limit_nis,
        "total_monthly_limit_nis": settings.project_monthly_limit_nis,
        "fixed_monthly_nis": settings.project_fixed_monthly_nis,
        "max_model_calls": settings.portal_max_model_calls,
        "max_actions": settings.portal_max_actions,
        "max_active_seconds": settings.portal_max_active_seconds,
    }
    try:
        return BrowserBudget(settings.workflow_database_path, prices=prices, **options)
    except (ValueError, TypeError):
        # Invalid prices fail closed for paid calls, without disabling known forms.
        return BrowserBudget(settings.workflow_database_path, prices={}, **options)
