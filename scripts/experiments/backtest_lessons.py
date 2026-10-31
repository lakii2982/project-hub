#!/usr/bin/env python3
"""Experiment EXP-1 backtest: would brief --precedents have surfaced past lessons?

Read-only. Replays the committed report history in task-id order and measures,
for every task whose report carries lessons, whether a same-module lesson
already existed in an earlier delivered report at the moment the task started.
Also measures the injection cost of brief --precedents under a strict
only-prior-reports cutoff. Experiment evidence for the self-improvement loop;
not part of the delivery contract.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
PROJECTCTL = ROOT / "scripts" / "projectctl.py"


def _load_projectctl() -> Any:
    spec = importlib.util.spec_from_file_location("exp1_projectctl", PROJECTCTL)
    if spec is None or spec.loader is None:
        raise SystemExit("cannot load projectctl")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument(
        "--samples",
        default="",
        help=(
            "comma-separated task ids: print the injection each would receive "
            "from all reports on disk (production semantics, no per-task time cutoff)"
        ),
    )
    arguments = parser.parse_args()
    root = Path(arguments.root).resolve()
    ctl = _load_projectctl()

    document = json.loads((root / ctl.TASKS_PATH).read_text(encoding="utf-8"))
    all_tasks = [task for task in document.get("tasks", []) if isinstance(task, dict)]
    tasks_by_id = {
        task["id"]: task for task in all_tasks if isinstance(task.get("id"), str)
    }
    universal = ctl._universal_paths(all_tasks)
    universal_modules = ctl._universal_modules(all_tasks, universal)
    reports, skipped = ctl._iter_reports(root)

    lessons_by_task: dict[str, list[dict[str, str]]] = {}
    for order, task_id, payload in reports:
        entries = ctl._report_lessons(task_id, payload)
        if entries:
            lessons_by_task[task_id] = entries

    replay: list[dict[str, Any]] = []
    module_unknown = 0
    for order, task_id, _payload in reports:
        if task_id not in lessons_by_task:
            continue
        task = tasks_by_id.get(task_id)
        if task is None:
            module_unknown += 1
            continue
        modules = ctl._task_modules(task, universal, universal_modules)
        priors: list[dict[str, Any]] = []
        for prior_order, prior_id, _prior_payload in reports:
            if prior_order >= order:
                break
            prior_task = tasks_by_id.get(prior_id)
            if prior_task is None or prior_id not in lessons_by_task:
                continue
            overlap = sorted(modules & ctl._task_modules(prior_task, universal, universal_modules))
            if overlap:
                priors.append({"task_id": prior_id, "modules": overlap})
        replay.append(
            {
                "task_id": task_id,
                "modules": sorted(modules),
                "lesson_count": len(lessons_by_task[task_id]),
                "had_prior_lesson": bool(priors),
                "prior_sources": priors[:4],
            }
        )

    surfaced = sum(1 for entry in replay if entry["had_prior_lesson"])

    cost_rows: list[dict[str, Any]] = []
    for _order, task_id, _payload in reports:
        task = tasks_by_id.get(task_id)
        if task is None:
            continue
        modules = ctl._task_modules(task, universal, universal_modules)
        tasks_by_id_local = tasks_by_id
        entries: list[dict[str, str]] = []
        matched: set[str] = set()
        for prior_order, prior_id, prior_payload in reversed(reports):
            if len(entries) >= ctl.LESSON_MAX_ENTRIES:
                break
            if prior_order >= _order:
                continue
            if prior_id == task_id:
                continue
            prior_task = tasks_by_id_local.get(prior_id)
            if prior_task is None:
                continue
            overlap = modules & ctl._task_modules(prior_task, universal, universal_modules)
            if not overlap:
                continue
            matched.update(overlap)
            taken = 0
            for lesson in ctl._report_lessons(prior_id, prior_payload):
                if taken >= ctl.LESSON_PER_TASK_MAX or len(entries) >= ctl.LESSON_MAX_ENTRIES:
                    break
                entries.append(
                    {
                        "module": sorted(overlap)[0],
                        "source": lesson["source"],
                        "task_id": prior_id,
                        "text": lesson["text"],
                    }
                )
                taken += 1
        payload_bytes = sum(
            len(json.dumps(entry, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            for entry in entries
        )
        cost_rows.append({"task_id": task_id, "bytes": payload_bytes, "entries": len(entries)})

    non_empty = [row for row in cost_rows if row["entries"] > 0]
    bytes_all = [row["bytes"] for row in cost_rows] or [0]
    bytes_non_empty = [row["bytes"] for row in non_empty] or [0]

    samples: dict[str, Any] = {}
    for sample_id in [item.strip() for item in arguments.samples.split(",") if item.strip()]:
        sample_task = tasks_by_id.get(sample_id)
        if sample_task is None:
            samples[sample_id] = {"error": "task not found"}
            continue
        samples[sample_id] = ctl._precedent_entries(
            root,
            tasks_by_id,
            ctl._task_modules(sample_task, universal, universal_modules),
            sample_id,
            universal,
            universal_modules,
        )["entries"]

    summary = {
        "universal_paths_dropped": sorted(universal),
        "universal_modules_dropped": sorted(universal_modules),
        "reports_scanned": len(reports),
        "reports_skipped": skipped,
        "lesson_entries_total": sum(len(v) for v in lessons_by_task.values()),
        "tasks_with_lessons": len(lessons_by_task),
        "tasks_module_unknown": module_unknown,
        "tasks_replayed": len(replay),
        "tasks_with_prior_lesson": surfaced,
        "surfacing_potential_pct": round(100.0 * surfaced / len(replay), 1) if replay else 0.0,
        "injection_cost": {
            "tasks_measured": len(cost_rows),
            "tasks_with_nonempty_injection": len(non_empty),
            "nonempty_pct": round(100.0 * len(non_empty) / len(cost_rows), 1) if cost_rows else 0.0,
            "avg_bytes_all": round(sum(bytes_all) / len(bytes_all), 1),
            "max_bytes_all": max(bytes_all),
            "avg_bytes_nonempty": round(sum(bytes_non_empty) / len(bytes_non_empty), 1),
            "max_bytes_nonempty": max(bytes_non_empty),
        },
    }

    json.dump(
        {
            "ok": True,
            "summary": summary,
            "replay": replay,
            "injection_cost_rows": cost_rows,
            "samples": samples,
        },
        sys.stdout,
        ensure_ascii=False,
        indent=2,
    )
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
