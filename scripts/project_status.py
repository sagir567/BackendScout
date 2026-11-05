"""Validate the frozen development plan and report dependency-ready work."""

import argparse
import hashlib
import json
import re
from pathlib import Path

import yaml

STATUSES = {"pending", "in_progress", "done", "blocked"}
EXECUTIONS = {"autonomous", "owner", "external"}


class PlanError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in mapping:
            raise PlanError("Plan mappings require unique string keys")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def require(condition, message):
    if not condition:
        raise PlanError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def relative_path(value):
    require(nonempty(value), "Expected a relative path")
    path = Path(value)
    require(not path.is_absolute() and ".." not in path.parts, "Path escapes its track")
    return path


def load_plan(path, check_evidence=False):
    path = Path(path).resolve()
    require(path.stat().st_size <= 256_000, "Plan exceeds the supported size")
    plan = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
    require(isinstance(plan, dict), "Plan must be a mapping")
    require(
        set(plan) == {"schema_version", "updated_on", "architecture", "budget", "tracks", "tasks"},
        "Plan has missing or unknown fields",
    )
    require(
        type(plan["schema_version"]) is int and plan["schema_version"] == 1,
        "Unsupported plan version",
    )
    require(
        isinstance(plan["updated_on"], str)
        and re.fullmatch(r"\d{4}-\d{2}-\d{2}", plan["updated_on"]),
        "Plan needs an ISO update date",
    )
    architecture = plan["architecture"]
    require(
        isinstance(architecture, dict) and set(architecture) == {"path", "version", "sha256"},
        "Invalid architecture reference",
    )
    baseline = path.parent / relative_path(architecture["path"])
    require(baseline.resolve().is_relative_to(path.parent), "Architecture path escapes repository")
    require(nonempty(architecture["version"]), "Architecture version is missing")
    require(
        isinstance(architecture["sha256"], str)
        and re.fullmatch(r"[a-f0-9]{64}", architecture["sha256"]),
        "Invalid baseline checksum",
    )
    require(
        hashlib.sha256(baseline.read_bytes()).hexdigest() == architecture["sha256"],
        "Architecture differs from the frozen baseline; record an authorized amendment",
    )
    budget = plan["budget"]
    require(
        isinstance(budget, dict)
        and set(budget) == {"currency", "total_monthly_ceiling"}
        and budget["currency"] == "NIS"
        and type(budget["total_monthly_ceiling"]) is int
        and budget["total_monthly_ceiling"] == 100,
        "Budget differs from the baseline",
    )
    tracks = plan["tracks"]
    require(
        isinstance(tracks, dict) and set(tracks) == {"personal", "commercial"}, "Invalid tracks"
    )
    expected_roots = {"personal": ".", "commercial": "../BackendScout-Skill"}
    for key, track in tracks.items():
        require(
            isinstance(track, dict) and track == {"root": expected_roots[key]},
            "Track root differs from the workspace contract",
        )
    tasks = plan["tasks"]
    require(isinstance(tasks, list) and bool(tasks), "Plan needs tasks")
    by_id = {}
    fields = {
        "id",
        "track",
        "title",
        "status",
        "execution",
        "owner",
        "depends_on",
        "write_scope",
        "acceptance",
        "evidence",
    }
    for task in tasks:
        require(
            isinstance(task, dict) and fields <= set(task) <= fields | {"blocker"},
            "Invalid task fields",
        )
        key = task["id"]
        require(isinstance(key, str) and re.fullmatch(r"[A-Z][0-9]{2}", key), "Invalid task ID")
        require(key not in by_id, "Duplicate task ID")
        by_id[key] = task
        require(nonempty(task["track"]) and task["track"] in tracks, f"{key}: unknown track")
        require(nonempty(task["status"]) and task["status"] in STATUSES, f"{key}: unknown status")
        require(
            nonempty(task["execution"]) and task["execution"] in EXECUTIONS,
            f"{key}: unknown execution mode",
        )
        require(nonempty(task["title"]) and nonempty(task["owner"]), f"{key}: missing title/owner")
        for field in ("depends_on", "write_scope", "acceptance"):
            require(
                isinstance(task[field], list) and all(nonempty(item) for item in task[field]),
                f"{key}: invalid {field}",
            )
        require(bool(task["acceptance"]), f"{key}: missing acceptance criteria")
        require(
            len(task["depends_on"]) == len(set(task["depends_on"])),
            f"{key}: duplicate dependencies",
        )
        for scope in task["write_scope"]:
            relative_path(scope)
        require(
            task["status"] != "blocked" or nonempty(task.get("blocker")),
            f"{key}: blocked work needs a concrete blocker",
        )
        evidence = task["evidence"]
        require(isinstance(evidence, list), f"{key}: invalid evidence")
        require(task["status"] != "done" or bool(evidence), f"{key}: done without evidence")
        for item in evidence:
            require(
                isinstance(item, dict)
                and set(item) == {"path", "result"}
                and nonempty(item["result"]),
                f"{key}: invalid evidence record",
            )
            relative = relative_path(item["path"])
            if check_evidence:
                root = (path.parent / tracks[task["track"]]["root"]).resolve()
                target = (root / relative).resolve()
                require(
                    target.is_relative_to(root) and target.exists(),
                    f"{key}: evidence file is unavailable",
                )
    visiting, visited = set(), set()

    def visit(key):
        require(key not in visiting, "Task dependency cycle detected")
        if key in visited:
            return
        visiting.add(key)
        task = by_id[key]
        for dependency in task["depends_on"]:
            require(dependency in by_id, f"{key}: unknown dependency")
            visit(dependency)
            require(
                task["status"] != "done" or by_id[dependency]["status"] == "done",
                f"{key}: done before its dependency",
            )
        visiting.remove(key)
        visited.add(key)

    for key in by_id:
        visit(key)
    return plan


def status_report(plan, track=None):
    by_id = {task["id"]: task for task in plan["tasks"]}
    tasks = [task for task in plan["tasks"] if track is None or task["track"] == track]
    ready = [
        task
        for task in tasks
        if task["status"] == "pending"
        and all(by_id[key]["status"] == "done" for key in task["depends_on"])
    ]

    def brief(task):
        return {key: task[key] for key in ("id", "track", "title", "owner", "execution")}

    return {
        "architecture_version": plan["architecture"]["version"],
        "complete": all(task["status"] == "done" for task in tasks),
        "counts": {
            state: sum(task["status"] == state for task in tasks) for state in sorted(STATUSES)
        },
        "in_progress": [brief(task) for task in tasks if task["status"] == "in_progress"],
        "ready_autonomous": [brief(task) for task in ready if task["execution"] == "autonomous"],
        "ready_owner_or_external": [
            brief(task) for task in ready if task["execution"] != "autonomous"
        ],
        "blocked": [
            {**brief(task), "blocker": task["blocker"]}
            for task in tasks
            if task["status"] == "blocked"
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--plan", type=Path, default=Path(__file__).resolve().parents[1] / "EXECUTION_PLAN.yaml"
    )
    parser.add_argument("--track", choices=["personal", "commercial"])
    parser.add_argument(
        "--check-evidence", action="store_true", help="Require evidence paths to exist locally"
    )
    args = parser.parse_args(argv)
    try:
        report = status_report(load_plan(args.plan, args.check_evidence), args.track)
    except (OSError, ValueError, TypeError, yaml.YAMLError) as error:
        print(json.dumps({"valid": False, "error": str(error)}))
        return 1
    print(json.dumps({"valid": True, **report}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
