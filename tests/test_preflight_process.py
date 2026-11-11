import subprocess
import sys
from pathlib import Path

import pytest

from backend_scout.config import TrackerName
from backend_scout.preflight_process import inspect_in_subprocess


def test_preflight_child_returns_structured_unsafe_url_outcome(review_service):
    job = review_service.store.get_application("example-job", TrackerName.TEST).job
    job = job.model_copy(update={"application_url": "http://example.invalid/apply"})
    result = inspect_in_subprocess(job, TrackerName.PRODUCTION, timeout=10)
    assert result["route"]["reason_code"] == "unsafe_url"
    assert result["questions"] == []


def test_preflight_timeout_reaps_process_and_removes_disposable_profile(review_service, monkeypatch):
    job = review_service.store.get_application("example-job", TrackerName.TEST).job
    real_popen = subprocess.Popen
    started = []

    def stalled_child(argv, **kwargs):
        child = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        started.append((child, Path(argv[-1])))
        return child

    monkeypatch.setattr(subprocess, "Popen", stalled_child)
    with pytest.raises(ValueError, match="deadline"):
        inspect_in_subprocess(job, TrackerName.TEST, timeout=0.15)
    child, directory = started[0]
    assert child.poll() is not None
    assert not directory.exists()
