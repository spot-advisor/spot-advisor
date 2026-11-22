"""Summarize a checkpoint pilot without treating missing usage as zero."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from math import isfinite
from pathlib import Path
from statistics import median

from report import estimate, read_json, sum_known

ARMS = ("luna_only", "luna_advice", "astra_takeover")
QUESTION_ARM = "luna_generic_question"


def executor_receipts(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    latest = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        latest[row["attempt_id"]] = row
    return list(latest.values())


def summarize_pilot(root: Path) -> list[dict]:
    rows = []
    for manifest in sorted(root.glob("*/checkpoint-summary.json")):
        case = manifest.parent
        prefix = executor_receipts(case / "prefix.jsonl")
        for arm in read_json(manifest)["order"]:
            folder = case / arm
            path = folder / "result.json"
            if not path.is_file():
                continue
            result = read_json(path)
            receipts = prefix + executor_receipts(folder / "attempts.jsonl")
            receipts.extend(attempt for consultation in result["consultations"]
                            for attempt in consultation["attempts"] if attempt["dispatch_possible"])
            usage = [receipt.get("usage") or {} for receipt in receipts]
            by_model = {}
            for receipt, counts in zip(receipts, usage):
                model = receipt.get("model") or "unattributed"
                by_model.setdefault(model, []).append(counts)
            rows.append({
                "task": case.name, "arm": arm, "accepted": result["accepted"],
                "wall_seconds": result.get("wall_seconds"),
                "input_tokens": sum_known(item.get("input_tokens") for item in usage) if usage else None,
                "output_tokens": sum_known(item.get("output_tokens") for item in usage) if usage else None,
                "api_equivalent_usd": sum_known(
                    estimate(receipt) if receipt.get("usage") else None for receipt in receipts
                ) if receipts else None,
                "observed_usage_attempts": sum(
                    item.get("input_tokens") is not None and item.get("output_tokens") is not None
                    for item in usage
                ),
                "attempts": len(receipts),
                "usage_by_model": {
                    model: {
                        "input_tokens": sum_known(item.get("input_tokens") for item in counts),
                        "output_tokens": sum_known(item.get("output_tokens") for item in counts),
                        "cache_read_tokens": sum_known(item.get("cached_input_tokens") for item in counts),
                        "cache_write_tokens": sum_known(item.get("cache_write_input_tokens") for item in counts),
                        "attempts": len(counts),
                    }
                    for model, counts in sorted(by_model.items())
                },
                "advisor_statuses": [call["status"] for call in result["consultations"]],
            })
    return rows


def continuation_totals(cohort: list[dict]) -> dict:
    accepted = sum(row["accepted"] for row in cohort)
    tokens = sum_known(sum_known([row["input_tokens"], row["output_tokens"]]) for row in cohort)
    cost = sum_known(row["api_equivalent_usd"] for row in cohort)
    frontier_tokens = []
    for row in cohort:
        usage = row["usage_by_model"]
        if "unattributed" in usage:
            frontier_tokens.append(None)
            continue
        astra = usage.get("gpt-6.2-astra", {"input_tokens": 0, "output_tokens": 0})
        frontier_tokens.append(sum_known([astra["input_tokens"], astra["output_tokens"]]))
    frontier = sum_known(frontier_tokens)
    elapsed = [row["wall_seconds"] for row in cohort]
    return {
        "accepted": accepted,
        "median_continuation_seconds": median(elapsed) if elapsed and None not in elapsed else None,
        "total_tokens_per_verified_task": tokens / accepted if accepted and tokens is not None else None,
        "astra_tokens_per_verified_task": frontier / accepted if accepted and frontier is not None else None,
        "api_equivalent_usd_per_verified_task": cost / accepted if accepted and cost is not None else None,
    }


def matched_summary(rows: list[dict], planned: tuple[str, ...] = ()) -> dict:
    by_task = {}
    for row in rows:
        arms = by_task.setdefault(row["task"], {})
        if row["arm"] in arms:
            raise ValueError(f"Duplicate checkpoint continuation: {row['task']}/{row['arm']}")
        arms[row["arm"]] = row
    complete = {
        task: arms for task, arms in by_task.items()
        if all(arm in arms and type(arms[arm]["accepted"]) is bool for arm in ARMS)
    }
    totals = {arm: continuation_totals([arms[arm] for arms in complete.values()]) for arm in ARMS}
    question_arms = ("luna_advice", QUESTION_ARM)
    question_pairs = {task: arms for task, arms in by_task.items()
                      if all(arm in arms and type(arms[arm]["accepted"]) is bool for arm in question_arms)}
    pairs = list(question_pairs.values())
    return {
        "complete_tasks": sorted(complete),
        "incomplete_tasks_with_results": sorted(by_task.keys() - complete.keys()),
        "planned_tasks_without_results": sorted(set(planned) - by_task.keys()),
        "arms": totals,
        "advice_only_passes": sum(arms["luna_advice"]["accepted"] and not arms["luna_only"]["accepted"]
                                  for arms in complete.values()),
        "luna_only_passes_against_advice": sum(arms["luna_only"]["accepted"] and not arms["luna_advice"]["accepted"]
                                              for arms in complete.values()),
        "question_comparison": {
            "pairs": len(question_pairs),
            "task_ids": sorted(question_pairs),
            "focused_passes": sum(arms["luna_advice"]["accepted"] for arms in pairs),
            "generic_passes": sum(arms["luna_generic_question"]["accepted"] for arms in pairs),
            "focused_only_passes": sum(arms["luna_advice"]["accepted"] and not arms["luna_generic_question"]["accepted"]
                                       for arms in pairs),
            "generic_only_passes": sum(arms["luna_generic_question"]["accepted"] and not arms["luna_advice"]["accepted"]
                                       for arms in pairs),
            "arms": {arm: continuation_totals([arms[arm] for arms in pairs]) for arm in question_arms},
        },
    }


def matched_run(folder: Path) -> dict:
    plan = folder / "pilot-plan.json"
    planned = tuple(read_json(plan)["cases"]) if plan.is_file() else ()
    continuations = summarize_pilot(folder)
    summary = matched_summary(continuations, planned)
    dispositions = {path.parent.name: read_json(path) for path in sorted(folder.glob("*/result.json"))}
    summary["prefix_dispositions"] = dispositions
    summary["planned_tasks_without_results"] = [
        task for task in summary["planned_tasks_without_results"] if task not in dispositions
    ]
    summary["continuations"] = continuations
    return summary


def checkpoint_assignment_outcomes(rows: list[dict], assignments: dict, deadline_seconds: int) -> dict:
    arms = (*ARMS, QUESTION_ARM)
    outcomes = {}
    for row in rows:
        key = (row["task"], row["arm"])
        if key in outcomes or key[0] not in assignments or key[1] not in arms:
            raise ValueError("A continuation does not match one assigned checkpoint workflow.")
        outcomes[key] = row
    for order in assignments.values():
        if len(order) != len(arms) or set(order) != set(arms):
            raise ValueError("Each saved checkpoint must assign all four workflows once.")

    count = len(assignments)
    totals = {}
    for arm in arms:
        recorded = [outcomes[task, arm] for task in assignments if (task, arm) in outcomes]
        grades = [row for row in recorded if type(row["accepted"]) is bool]
        verified = [row for row in grades if row["accepted"]]
        timed = [row for row in verified if row.get("wall_seconds") is not None]
        timely = sum(row["wall_seconds"] <= deadline_seconds for row in timed)
        missing_grades = count - len(grades)
        missing_deadline_outcomes = missing_grades + len(verified) - len(timed)
        totals[arm] = {
            "recorded": len(recorded), "verified": len(verified), "verified_by_deadline": timely,
            "missing_grades": missing_grades, "missing_deadline_outcomes": missing_deadline_outcomes,
        }

    focused = totals["luna_advice"]
    effects = {}
    if count:
        for baseline in ("luna_only", QUESTION_ARM, "astra_takeover"):
            other = totals[baseline]
            difference = focused["verified_by_deadline"] - other["verified_by_deadline"]
            effects[baseline] = {
                "lower": (difference - other["missing_deadline_outcomes"]) / count,
                "upper": (difference + focused["missing_deadline_outcomes"]) / count,
            }
    return {"assigned_checkpoints": count, "deadline_seconds": deadline_seconds,
            "arms": totals, "focused_deadline_difference_bounds": effects}


def screening_check_counts(checks_by_task: dict) -> dict:
    selected = [[check for check in checks if not check["default_command"]] for checks in checks_by_task.values()]
    counts = {
        "with_nondefault_checks": sum(bool(checks) for checks in selected),
        "with_failed_nondefault_checks": sum(any(not check["passed"] for check in checks) for checks in selected),
        "with_nondefault_exit_124": sum(any(check["exit_code"] == 124 for check in checks) for checks in selected),
        "with_nondefault_triggers": sum(any(check["trigger"] is not None for check in checks) for checks in selected),
    }
    if any("command_id" in check for checks in selected for check in checks):
        counts["with_distinct_failed_nondefault_commands"] = sum(
            len({check["command_id"] for check in checks if not check["passed"]}) > 1 for checks in selected
        )
    return counts


def prefix_stop_category(error: str) -> str:
    message = error.casefold()
    if "tool-call limit" in message or "tool allowance" in message:
        return "tool_allowance"
    if "request limit" in message:
        return "request_allowance"
    if "wall-clock limit" in message:
        return "prefix_deadline"
    if "transport failed" in message:
        if "timeout" in message:
            return "transport_timeout"
        return "tls_error" if "sslerror" in message else "transport_error"
    if "timed out after" in message or "timeout" in message or "time limit" in message:
        return "other_timeout"
    return "other_prefix_failure"


def prefix_elapsed_seconds(path: Path) -> float | None:
    if not path.is_file():
        return None
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    if len(events) < 2 or events[0]["event"] != "dispatched" or events[-1]["event"] != "finished":
        return None
    start, stop = (item.get("timestamp_utc") for item in (events[0], events[-1]))
    if start is None or stop is None:
        return None
    elapsed = (datetime.fromisoformat(stop) - datetime.fromisoformat(start)).total_seconds()
    return elapsed if elapsed >= 0 else None


def screening_timeout_timing(dispositions: dict, elapsed: dict, budget_seconds: int) -> dict:
    timeouts = {task for task, row in dispositions.items() if row.get("stop_category") == "transport_timeout"}
    if (not elapsed.keys() <= timeouts or budget_seconds <= 0
            or any(not isfinite(seconds) or seconds < 0 for seconds in elapsed.values())):
        raise ValueError("Invalid timing for a prefix with a recorded transport timeout.")
    return {"elapsed_by_task": elapsed, "recorded_timeouts": len(timeouts),
            "at_or_after_budget": sum(seconds >= budget_seconds for seconds in elapsed.values()),
            "range_seconds": [min(elapsed.values()), max(elapsed.values())] if elapsed else None}


def screening_diagnostics(dispositions: dict, saved: set, by_task: dict) -> dict:
    failed = [row for row in dispositions.values() if row["status"] == "prefix_failed"]
    common = {task: by_task[task]["spot_advisor_22"] for task in saved | dispositions.keys()
              if "spot_advisor_22" in by_task.get(task, {})}
    return {
        "prefix_failures": len(failed),
        "stop_categories": dict(sorted(Counter(row["stop_category"] for row in failed).items())),
        "verified_prefix_failures": sum(row["accepted"] is True for row in failed),
        "coding_task_ids": sorted(common),
        "coding_runs_with_repeated_failure": sum(
            any(check["trigger"] == "two_failed_checks" for check in row["checkpoints"]) for row in common.values()
        ),
    }


def calibration_counts(trials: list[dict], public_cases: list[str]) -> dict:
    counts = {}
    for trial in trials:
        plan = trial["plan"]
        cases = set(plan["cases"])
        if (not cases or len(cases) != len(plan["cases"]) or cases.intersection(public_cases)
                or plan["natural_trigger"] is not False or plan["purpose"] != "Diagnostic qualification."):
            raise ValueError("Expected a disjoint, manual-trigger calibration.")
        arms = set(ARMS)
        stage = "continuation"
        if plan.get("compare_questions"):
            arms.add("luna_generic_question")
            stage = "question"
        group = counts.setdefault(stage, {"checkpoints": 0, "graded": 0, "passed": 0, "advice_deliveries": 0})
        snapshots = trial["snapshots"]
        if set(snapshots) != cases or any(snapshot["trigger"] != "manual" or set(snapshot["order"]) != arms
                                         for snapshot in snapshots.values()):
            raise ValueError("Calibration workspaces do not match the planned cases and arms.")
        rows = trial["rows"]
        keys = {(row["task"], row["arm"]) for row in rows}
        if (len(keys) != len(rows) or keys != {(task, arm) for task in cases for arm in arms}
                or any(type(row["accepted"]) is not bool for row in rows)):
            raise ValueError("Calibration grades do not cover exactly the planned continuations.")
        group["checkpoints"] += len(cases)
        group["graded"] += len(rows)
        group["passed"] += sum(row["accepted"] for row in rows)
        group["advice_deliveries"] += sum(row["advisor_statuses"].count("ok") for row in rows)
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--matched", action="store_true", help="Report only complete three-arm comparisons.")
    args = parser.parse_args()
    print(json.dumps(matched_run(args.run) if args.matched else summarize_pilot(args.run), indent=2))
