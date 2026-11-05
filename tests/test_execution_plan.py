import hashlib
import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("project_status", ROOT / "scripts/project_status.py")
status = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(status)


@pytest.fixture
def plan_file(tmp_path):
    (tmp_path / "ARCHITECTURE.md").write_text("frozen baseline")
    plan = {
        "schema_version": 1,
        "updated_on": "2026-11-01",
        "architecture": {
            "path": "ARCHITECTURE.md",
            "version": "1.0",
            "sha256": hashlib.sha256(b"frozen baseline").hexdigest(),
        },
        "budget": {"currency": "NIS", "total_monthly_ceiling": 100},
        "tracks": {"personal": {"root": "."}, "commercial": {"root": "../BackendScout-Skill"}},
        "tasks": [],
    }
    for key, execution, dependencies in [
        ("A00", "autonomous", []),
        ("P01", "autonomous", ["A00"]),
        ("S01", "owner", ["A00"]),
    ]:
        plan["tasks"].append(
            {
                "id": key,
                "track": "personal" if key != "S01" else "commercial",
                "title": key,
                "status": "done" if key == "A00" else "pending",
                "execution": execution,
                "owner": "integration",
                "depends_on": dependencies,
                "write_scope": [],
                "acceptance": ["Meaningful outcome"],
                "evidence": [{"path": "ARCHITECTURE.md", "result": "Reviewed"}]
                if key == "A00"
                else [],
            }
        )
    path = tmp_path / "EXECUTION_PLAN.yaml"

    def save():
        path.write_text(yaml.safe_dump(plan, sort_keys=False))
        return path

    return plan, save


def test_ready_work_respects_dependency_and_owner_gate(plan_file):
    _, save = plan_file
    report = status.status_report(status.load_plan(save(), check_evidence=True))
    assert not report["complete"]
    assert [item["id"] for item in report["ready_autonomous"]] == ["P01"]
    assert [item["id"] for item in report["ready_owner_or_external"]] == ["S01"]


def test_detects_baseline_drift_and_budget_change(plan_file):
    plan, save = plan_file
    path = save()
    (path.parent / "ARCHITECTURE.md").write_text("unapproved architecture change")
    with pytest.raises(status.PlanError, match="frozen baseline"):
        status.load_plan(path)
    (path.parent / "ARCHITECTURE.md").write_text("frozen baseline")
    plan["budget"]["total_monthly_ceiling"] = 200
    with pytest.raises(status.PlanError, match="Budget"):
        status.load_plan(save())


def test_unknown_dependencies_cycles_and_premature_completion_fail(plan_file):
    plan, save = plan_file
    plan["tasks"][1]["depends_on"] = ["X99"]
    with pytest.raises(status.PlanError, match="unknown dependency"):
        status.load_plan(save())
    plan["tasks"][1]["depends_on"] = ["A00"]
    plan["tasks"][0]["depends_on"] = ["P01"]
    with pytest.raises(status.PlanError, match="cycle"):
        status.load_plan(save())
    plan["tasks"][0]["depends_on"] = ["S01"]
    plan["tasks"][2]["depends_on"] = []
    with pytest.raises(status.PlanError, match="done before"):
        status.load_plan(save())


def test_done_requires_evidence_and_blocked_requires_reason(plan_file):
    plan, save = plan_file
    plan["tasks"][0]["evidence"] = []
    with pytest.raises(status.PlanError, match="done without evidence"):
        status.load_plan(save())
    plan["tasks"][0]["status"] = "blocked"
    with pytest.raises(status.PlanError, match="concrete blocker"):
        status.load_plan(save())


def test_duplicate_yaml_keys_and_task_ids_rejected(plan_file):
    plan, save = plan_file
    path = save()
    path.write_text(path.read_text() + "schema_version: 1\n")
    with pytest.raises(status.PlanError, match="unique"):
        status.load_plan(path)
    plan["tasks"].append(dict(plan["tasks"][0]))
    with pytest.raises(status.PlanError, match="Duplicate task"):
        status.load_plan(save())


def test_evidence_cannot_escape_track(plan_file):
    plan, save = plan_file
    plan["tasks"][0]["evidence"][0]["path"] = "../private/evidence.yaml"
    with pytest.raises(status.PlanError, match="escapes"):
        status.load_plan(save())


def test_current_workspace_plan_is_structurally_valid():
    assert status.load_plan(ROOT / "EXECUTION_PLAN.yaml")["architecture"]["version"] == "1.1"
