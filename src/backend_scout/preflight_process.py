"""Bound the lifetime of read-only Playwright inspections outside the delivery worker."""

import json
import os
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory

from backend_scout.config import Settings, TrackerName
from backend_scout.models import Job


def inspect_in_subprocess(job: Job, tracker: TrackerName, *, timeout: float = 90) -> dict:
    request = json.dumps({"job": job.model_dump(mode="json"), "tracker": tracker.value})
    with (
        TemporaryDirectory(prefix="backendscout-preflight-") as directory,
        subprocess.Popen(
            [sys.executable, "-m", "backend_scout.preflight_process", directory],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, start_new_session=True,
        ) as child,
    ):
        try:
            output, _ = child.communicate(request, timeout=timeout)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            raise ValueError("Read-only browser inspection exceeded its deadline.") from None
        if child.returncode:
            raise ValueError("Read-only browser inspection exited unexpectedly.")
    try:
        result = json.loads(output)
    except ValueError:
        raise ValueError("Read-only browser inspection returned invalid output.") from None
    if not isinstance(result, dict):
        raise TypeError("Read-only browser inspection returned invalid output.")
    return result


def main() -> None:
    from backend_scout.hybrid_portal import HybridPortalAdapter
    from backend_scout.portal_sessions import PortalSessionManager

    request = json.load(sys.stdin)
    root = Path(sys.argv[1])
    settings = Settings().model_copy(update={
        "workflow_database_path": root / "inspection.sqlite3",
        "cv_archive_root": root / "cv_archive",
    })
    with PortalSessionManager(root / "browser") as manager:
        result = HybridPortalAdapter(manager, settings).inspect(
            Job.model_validate(request["job"]), TrackerName(request["tracker"]),
        )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
