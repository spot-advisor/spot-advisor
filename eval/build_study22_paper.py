"""Build a private 2.2 manuscript from a frozen coding scorecard."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from collections import Counter
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from checkpoint_report import (calibration_counts, checkpoint_assignment_outcomes, executor_receipts, matched_run,
                               prefix_elapsed_seconds, prefix_stop_category, screening_check_counts,
                               screening_diagnostics, screening_timeout_timing, summarize_pilot)
from cost_report22 import LABELS, matched_resource_audit, plot_matched_resources
from peer_report22 import peer_audit, peer_evidence_files
from replication22 import (assignment_totals, consultation_funnel, create_package, observed_request_usage,
                           optimistic_quality_counts, original_assignment, verify_coding_patches, verify_coding_source,
                           verify_gate_qualification, verify_patch_archive, verify_peer_artifacts, verify_recovery,
                           verify_redis_origin, verify_vision_probe,
                           verify_visual_artifacts)
from report import REQUEST_TIMING_FIELDS
from run import ROOT
from study22 import binomial_upper
from visual_ratings22 import collect_review


def arxiv_metadata(output: Path) -> None:
    source = (output / "main.tex").read_text(encoding="utf-8")
    title = re.search(r"\\title\{([^{}]+)\}", source).group(1)
    title = " ".join(title.replace(r"\\", " ").replace(r"\large", "").split())
    authors = re.search(r"\\author\{([^{}]+)\}", source).group(1)
    abstract = re.search(r"\\begin\{abstract\}(.*?)\\end\{abstract\}", source, re.S).group(1)
    abstract = " ".join(abstract.replace(
        r"\input{abstract-state}", (output / "abstract-state.tex").read_text(encoding="utf-8")).split())
    if not 0 < len(abstract) <= 1920 or any(not field.isascii() or "\\" in field
                                          for field in (title, authors, abstract)):
        raise ValueError("Expected plain ASCII arXiv metadata with an abstract at most 1920 characters.")
    (output / "arxiv-metadata.txt").write_text(
        f"Title\n{title}\n\nAuthors\n{authors}\n\nAbstract\n{abstract}\n\n"
        "Proposed primary category\ncs.SE (Software Engineering)\n",
        encoding="ascii",
    )


def archived_receipts(journal: Path, role: str) -> list[dict]:
    return [
        {"role": role, "requested_model": row["requested_model"], "model": row.get("model"),
         "status": row.get("status") or "dispatch_unresolved", "usage": row.get("usage"),
         **{key: row[key] for key in REQUEST_TIMING_FIELDS if key in row}}
        for row in executor_receipts(journal)
        if role == "executor" or row["dispatch_possible"]
    ]


def interrupted_receipts(arm: Path) -> list[dict]:
    return (archived_receipts(arm / "executor-attempts.jsonl", "executor")
            + archived_receipts(arm / "advisor-session.json.attempts.jsonl", "advisor"))


def gate_journal(probe_root: Path, record: dict) -> list[dict] | None:
    source = probe_root / record["task"] / "spot_advisor_22" / "advisor-session.json.attempts.jsonl"
    if not source.is_file():
        return None
    frozen = (probe_root / "advisor-frozen.py").read_bytes()
    if hashlib.sha256(frozen).hexdigest() != record["candidate_advisor_sha256"]:
        raise ValueError("The gate journal does not use the reported candidate advisor.")
    original = [json.loads(line) for line in source.read_bytes().splitlines()]
    if len({row["attempt_id"] for row in original}) != 1:
        raise ValueError("Expected one attempt in the manual qualification journal.")
    fields = ("event", "task_id", "revision", "round", "transport", "dispatch_possible", "requested_model",
              "model", "status", "usage")
    return [{key: row[key] for key in fields if key in row} for row in original]


def assignment_audit(manifest: dict, by_task: dict, run: Path | None) -> dict:
    if run is not None and json.loads((run / "manifest.json").read_bytes()) != manifest:
        raise ValueError("Recovery source uses a different frozen manifest.")
    assignments = []
    for task in manifest["tasks"]:
        task_id = task["id"]
        for arm in task["arms"]:
            row = by_task.get(task_id, {}).get(arm)
            record = original_assignment(task_id, arm, row)
            if row is None and run is not None:
                recovered = run / task_id / arm / "recovered"
                if (recovered / "grade.json").is_file():
                    record.update(state="regraded_after_interruption",
                                  recovery_capture=json.loads((recovered / "capture.json").read_bytes()),
                                  recovery_grade=json.loads((recovered / "grade.json").read_bytes()),
                                  interrupted_attempts=interrupted_receipts(recovered.parent))
                    verify_recovery(record, (recovered / "model.patch").read_bytes())
            assignments.append(record)
    return {"assignments": assignments, **assignment_totals(manifest, assignments)}


def visual_result(folder: Path) -> dict:
    record = json.loads((folder / "result.json").read_bytes())
    if (folder / "advice.txt").is_file():
        record["advice"] = (folder / "advice.txt").read_text(encoding="utf-8")
    return record


def visual_pilot_audit(pilot: Path, image_control: Path | None = None) -> tuple[dict, str]:
    manifest_bytes = (pilot / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest["stage"] != "disjoint_visual_pilot":
        raise ValueError("Expected the disjoint four-arm visual pilot.")
    cases, baselines = {}, {}
    for case, arms in manifest["arms_by_case"].items():
        baselines[case] = json.loads((pilot / case / "starting" / "checks.json").read_bytes())
        cases[case] = {arm: visual_result(pilot / case / arm) for arm in arms}
    rows = [row for arms in cases.values() for row in arms.values()]
    passes = sum(row["functional_pass"] for row in rows)
    changes = sum(row["changed"] for row in rows)
    failures = sum(row["error"] is not None for row in rows)
    note = (f"The disjoint pilot rendered {len(rows)} outputs across {len(cases)} starting cases. "
            f"Browser checks passed for {passes} outputs, {changes} changed the page, "
            f"and {failures} had a recorded execution error. These checks cover page behavior.")
    evidence = {"manifest": manifest, "starting": baselines, "cases": cases, "first_click": None}
    if image_control is not None:
        control = json.loads((image_control / "manifest.json").read_bytes())
        results = {case: visual_result(image_control / case / "luna_with_image") for case in cases}
        count = sum(result["functional_pass"] for result in results.values())
        evidence["supplemental_image_control"] = {"manifest": control, "cases": results}
        note += (f" A supplemental Luna-Max-with-image arm passed the browser checks on {count} "
                 f"of {len(results)} original starting pages. This arm was added after the pilot. "
                 "A fresh study should compare both models with the same image available before attributing "
                 "visual improvement to the advisor.")
    return evidence, note


def archive_visual_artifacts(output: Path, evidence: dict, pilot: Path, control: Path | None) -> None:
    with ZipFile(output, "x", ZIP_DEFLATED) as archive:
        archive.write(pilot / "manifest.json", "manifest.json")
        for case, arms in evidence["manifest"]["arms_by_case"].items():
            roots = {arm: pilot for arm in ("starting", *arms)}
            if control is not None:
                roots["luna_with_image"] = control
            for arm, root in roots.items():
                for name in ("index.html", "mobile.png"):
                    relative = f"{case}/{arm}/{name}"
                    archive.write(root / relative, relative)
    verify_visual_artifacts(output, evidence)


def checkpoint_evidence(folder: Path, by_task: dict | None = None, *, deadline: int) -> tuple[dict, str]:
    plan = json.loads((folder / "pilot-plan.json").read_bytes())
    registered = json.loads((ROOT / "eval/study22-tasks.json").read_bytes())
    if (plan.get("cases") != registered["tasks"] or plan.get("source_revision") != registered["source_revision"]
            or plan.get("natural_trigger") is not True or plan.get("require_behavioral_check") is not True
            or plan.get("compare_questions") is not True):
        raise ValueError("Expected the frozen public 2.2 checkpoint plan.")
    summary = matched_run(folder)
    for task, row in summary["prefix_dispositions"].items():
        category = prefix_stop_category(row.get("error") or "") if row["status"] == "prefix_failed" else None
        summary["prefix_dispositions"][task] = {
            **{name: row.get(name) for name in ("status", "reason", "accepted", "verdict", "workspace_sha256")},
            "stop_category": category,
        }
    assignments = {path.parent.name: json.loads(path.read_bytes())["order"]
                   for path in sorted(folder.glob("*/checkpoint-summary.json"))}
    eligible = set(assignments)
    dispositions = summary["prefix_dispositions"]
    screened = eligible | dispositions.keys()
    if eligible & dispositions.keys() or not screened <= set(plan["cases"]):
        raise ValueError("Unexpected public checkpoint disposition.")
    summary["eligible_checkpoint_tasks"] = sorted(eligible)
    summary["checkpoint_assignments"] = assignments
    assigned = checkpoint_assignment_outcomes(summary["continuations"], assignments, deadline)
    summary["assignment_outcomes"] = assigned
    summary["screening_checks"] = {}
    for task in sorted(screened):
        events = json.loads((folder / task / "prefix-checks.json").read_bytes())
        command_ids = {}
        summary["screening_checks"][task] = [
            {"default_command": event["command"] == "git diff --check HEAD",
             "command_id": command_ids.setdefault(event["command"], len(command_ids)),
             "passed": event["passed"], "exit_code": event["exit_code"], "trigger": event["trigger"]}
            for event in events
        ]
    checks = screening_check_counts(summary["screening_checks"])
    summary["screening_check_counts"] = checks
    receipts = {task: archived_receipts(folder / task / "prefix.jsonl", "executor") for task in sorted(screened)}
    prefix_usage = observed_request_usage([row for case in receipts.values() for row in case])
    summary["screening_prefix_receipts"] = receipts
    summary["screening_prefix_usage"] = prefix_usage
    diagnostics = screening_diagnostics(dispositions, eligible, by_task or {})
    summary["screening_diagnostics"] = diagnostics
    prefix_budget = plan.get("prefix_seconds")
    if prefix_budget is not None:
        timeouts = {}
        for task, row in dispositions.items():
            if row.get("stop_category") == "transport_timeout":
                elapsed = prefix_elapsed_seconds(folder / task / "prefix.jsonl")
                if elapsed is not None:
                    timeouts[task] = elapsed
        summary["screening_timeout_timing"] = screening_timeout_timing(dispositions, timeouts, prefix_budget)
    paired = summary["question_comparison"]
    note = (
        f"At this cutoff, the screening report covers {len(screened)} of {len(plan['cases'])} planned issues. "
        f"The counts were {len(eligible)} for saved checkpoints, "
        f"{sum(row['status'] == 'no_checkpoint' for row in dispositions.values())} for completed prefixes without "
        f"an eligible checkpoint, and {sum(row['status'] == 'prefix_failed' for row in dispositions.values())} "
        "for prefix failures. "
        f"There are {len(summary['complete_tasks'])} complete three-arm comparisons "
        f"and {paired['pairs']} matched question-format pairs."
    )
    if dispositions:
        note += (
            " Among screened issues ending before a saved checkpoint, "
            f"{sum(row['accepted'] is True for row in dispositions.values())} of {len(dispositions)} passed "
            "independent grading with the prefix patch."
        )
    if screened:
        note += (
            f" Verification commands other than the default diff check ran in "
            f"{checks['with_nondefault_checks']} of the {len(screened)} screened cases. "
            f"Of those cases, {checks['with_failed_nondefault_checks']} had a failed command and "
            f"{checks['with_nondefault_triggers']} had a recorded trigger."
        )
        distinct_failures = checks.get("with_distinct_failed_nondefault_commands")
        if distinct_failures is not None:
            note += (" Failures on multiple distinct nondefault command strings occurred in "
                     f"{distinct_failures} screened cases.")
        if checks["with_nondefault_exit_124"]:
            note += (f" A nondefault check returned exit code 124, the sandbox timeout code, in "
                     f"{checks['with_nondefault_exit_124']} screened cases.")
    if diagnostics["prefix_failures"]:
        labels = {"tool_allowance": "tool allowance", "request_allowance": "request allowance",
                  "prefix_deadline": "prefix deadline", "transport_timeout": "transport-reported timeout",
                  "tls_error": "TLS error", "transport_error": "other transport error", "other_timeout": "other timeout",
                  "other_prefix_failure": "other prefix failure"}
        breakdown = ", ".join(f"{labels[key]} ({count})" for key, count in diagnostics["stop_categories"].items())
        note += (
            f" Failed-prefix reasons were {breakdown}. Of the {diagnostics['prefix_failures']} failed prefixes, "
            f"{diagnostics['verified_prefix_failures']} still supplied a verified patch."
        )
    timeouts = summary.get("screening_timeout_timing")
    if timeouts and timeouts["range_seconds"]:
        timed = len(timeouts["elapsed_by_task"])
        earliest, latest = timeouts["range_seconds"]
        note += (
            f" For {timed} of {timeouts['recorded_timeouts']} transport-reported timeouts, the journal times from "
            f"first dispatch to final receipt ranged from {earliest:.1f} to {latest:.1f} seconds. "
            f"In {timeouts['at_or_after_budget']} cases this was at least the {prefix_budget}-second prefix budget. "
            "The request timeout is capped by the remaining prefix time, so the error label alone cannot "
            "distinguish a network fault from budget expiration."
        )
    if diagnostics["coding_runs_with_repeated_failure"]:
        note += (
            f" The corresponding full 2.2 runs recorded repeated failures on "
            f"{diagnostics['coding_runs_with_repeated_failure']} of {len(diagnostics['coding_task_ids'])} issues. "
            "The two protocols differ in budget and instructions."
        )
    if prefix_usage["token_receipts"]:
        note += (f" The screened prefixes logged {prefix_usage['token_receipts']} attributable token receipts "
                 f"from {prefix_usage['requests_observed']} dispatches, totaling {prefix_usage['observed_tokens']:,} "
                 "observed tokens.")
        if prefix_usage["priced_requests"]:
            note += (f" The available receipts covered {prefix_usage['priced_requests']} priceable requests "
                     f"with a subtotal equivalent to USD {prefix_usage['priced_subtotal_usd']:.3f} at API rates.")
    if paired["pairs"]:
        note += (
            f" The focused and generic questions had {paired['focused_passes']} and {paired['generic_passes']} "
            f"verified passes, respectively. The respective sole-pass counts were "
            f"{paired['focused_only_passes']} and {paired['generic_only_passes']}."
        )
    if eligible:
        outcomes = assigned["arms"]
        counts = ", ".join(f"{row['verified_by_deadline']}/{len(eligible)}" for row in outcomes.values())
        unknown = ", ".join(str(row["missing_deadline_outcomes"]) for row in outcomes.values())
        note += (f" Of all assigned continuations, verified grades within {deadline} seconds of the checkpoint "
                 f"were {counts} for Luna alone, focused advice, Astra takeover and generic advice, respectively. "
                 f"The numbers of unknown deadline outcomes in that order were {unknown}.")
    return {"plan": plan, "summary": summary}, note


def checkpoint_calibration(roots: list[Path], public_cases: list[str]) -> dict:
    trials = []
    for folder in roots:
        plan = json.loads((folder / "pilot-plan.json").read_bytes())
        snapshots = {}
        for task in plan["cases"]:
            case = folder / task
            manifest = json.loads((case / "checkpoint-summary.json").read_bytes())
            for name, field in (("checkpoint-workspace.tar", "workspace_sha256"),
                                ("checkpoint-messages.json", "messages_sha256")):
                if hashlib.sha256((case / name).read_bytes()).hexdigest() != manifest[field]:
                    raise ValueError(f"Calibration artifact no longer matches the checkpoint: {task}/{name}")
            snapshots[task] = {key: manifest[key] for key in
                               ("trigger", "order", "workspace_sha256", "messages_sha256")}
        trials.append({"plan": plan, "snapshots": snapshots, "rows": summarize_pilot(folder)})
    return {"trials": trials, "counts": calibration_counts(trials, public_cases)}


def redis_sensitivity(folder: Path, by_task: dict, peers: dict,
                      checkpoint_audit: dict | None) -> tuple[list[dict], str]:
    rows = []
    for path in sorted(folder.glob("*/*/*/sensitivity.json")):
        row = json.loads(path.read_bytes())
        if row["arm"] == "checkpoint-prefix" and checkpoint_audit is None:
            continue
        verify_redis_origin(row, by_task, peers, checkpoint_audit)
        if row["status"] != "graded":
            continue
        filtered = path.with_name("without-redis-runtime.patch").read_bytes()
        if hashlib.sha256(filtered).hexdigest() != row["filtered_sha256"]:
            raise ValueError(f"The filtered patch was changed: {path}")
        rows.append({key: value for key, value in row.items() if key != "application_errors"})
    primary = [row for row in rows if row["arm"] != "checkpoint-prefix"]
    prefixes = [row for row in rows if row["arm"] == "checkpoint-prefix"]
    note = (
        "A post-hoc replay regraded rejected patch artifacts after removing only "
        "the untracked Redis runtime sections that collided in fresh grading containers. "
        f"The filtered patches passed independent grading in {sum(row['accepted'] for row in primary)} of "
        f"{len(primary)} coding and peer replays. "
        "The primary table retains the original patch outcomes. The replay measures sensitivity to "
        "including generated Redis state in the submitted diff."
    )
    replay_passes = {(row["arm"], row["task_id"]) for row in primary if row["accepted"]}
    for peer, peer_data in peers["peers"].items():
        matched = peer_data["matched_metrics"]
        cases = matched["task_ids"]
        peer_gain = sum((peer, task) in replay_passes for task in cases)
        candidate_gain = sum(("spot_advisor_22", task) in replay_passes for task in cases)
        if peer_gain or candidate_gain:
            note += (
                f" With these regrades, the {peer_data['label']} comparison has "
                f"{matched['peer']['verified'] + peer_gain} peer passes and "
                f"{matched['spot_22']['verified'] + candidate_gain} 2.2 passes on {len(cases)} paired issues."
            )
    if prefixes:
        note += (f" Separately, {sum(row['accepted'] for row in prefixes)} of {len(prefixes)} screened "
                 "prefix patches passed after removing the same files. This replay supplies no continuation result.")
    return rows, note


def build(snapshot: Path, output: Path, run: Path | None = None, vision: Path | None = None,
          visual_pilot: Path | None = None, peer_roots: dict | None = None,
          visual_image_control: Path | None = None, checkpoint: Path | None = None,
          redis_audit: Path | None = None, calibration_roots: list[Path] | None = None,
          gate_probe: Path | None = None, visual_review: Path | None = None,
          visual_responses: list[Path] | None = None, manuscript: Path | None = None) -> None:
    manuscript = manuscript if manuscript is not None else ROOT / "paper/study22.tex"
    if not manuscript.is_file():
        raise FileNotFoundError("Provide the LaTeX manuscript with --manuscript PATH.")
    evidence = (snapshot / "evidence.jsonl").read_bytes()
    metrics = json.loads((snapshot / "scorecard.json").read_bytes())
    manifest = json.loads((snapshot / "manifest.json").read_bytes())
    if metrics["study"] != "coding" or manifest["stage"] != "coding":
        raise ValueError("A coding snapshot is required.")
    n = len(manifest["tasks"])
    by_task = {}
    expected = {(task["id"], arm) for task in manifest["tasks"] for arm in task["arms"]}
    for line in evidence.splitlines():
        row = json.loads(line)
        arms = by_task.setdefault(row["task_id"], {})
        if ((row["task_id"], row["arm"]) not in expected or row["arm"] in arms
                or row["source_sha256"] != manifest["source_sha256"]):
            raise ValueError("Evidence has an unassigned arm, duplicate arm, or different frozen source.")
        arms[row["arm"]] = row
    matched = [arms for arms in by_task.values() if arms.keys() == LABELS.keys()]
    observed = sum(len(arms) for arms in by_task.values())
    if not 0 < observed <= 4*n:
        raise ValueError("Unexpected number of completed arms.")
    audit = assignment_audit(manifest, by_task, run)
    optimistic_note = ""
    if run is not None and (run / "run-source.zip").is_file():
        optimistic = optimistic_quality_counts(manifest, by_task)
        lower = (1 - binomial_upper(n - optimistic["possible_wins"], n)
                 - binomial_upper(optimistic["recorded_losses"], n))
        audit["optimistic_policy_quality"] = {**optimistic, "best_case_quality_lower95": lower}
        if optimistic["missing_pairs"]:
            optimistic_note = (
                "Assigning the missing grades in the most favorable way for 2.2 yields a maximum "
                f"95\\% lower quality bound of {100 * lower:+.1f} percentage points. "
                f"The policy-change rule requires a bound above {100 * manifest['quality_margin']:+g} percentage points."
            )
    funnel = consultation_funnel(by_task)
    deadline = manifest["review_deadline_seconds"]
    resources = matched_resource_audit(by_task, metrics["pricing"], deadline)
    peers = peer_audit(peer_roots or {}, by_task, deadline)
    checkpoint_audit, checkpoint_note = (checkpoint_evidence(checkpoint, by_task, deadline=manifest["review_deadline_seconds"])
                                         if checkpoint is not None else
                                         (None, "The public checkpoint comparisons are pending at this cutoff."))
    calibration = None
    if calibration_roots:
        study_plan = json.loads((ROOT / "eval/study22-tasks.json").read_bytes())
        calibration = checkpoint_calibration(calibration_roots, study_plan["tasks"])
        local_notes = [
            f"The manual-trigger {stage} calibration verified {result['passed']} of "
            f"{result['graded']} continuations and recorded "
            f"{result['advice_deliveries']} successful advisor deliveries."
            for stage, result in calibration["counts"].items()
        ]
        checkpoint_note = " ".join(local_notes) + "\n\n" + checkpoint_note
    gate_qualification = gate_events = None
    if gate_probe is not None:
        gate_qualification = json.loads(gate_probe.read_bytes())
        gate_events = gate_journal(gate_probe.parent, gate_qualification)
        verify_gate_qualification(gate_qualification, manifest, gate_events)
        gate_usage = gate_qualification["reported_attempts"][0].get("usage") or {}
        receipt_note = ""
        if all(gate_usage.get(key) is not None for key in ("input_tokens", "output_tokens")):
            receipt_note = (f" The receipt reported {gate_usage['input_tokens']} input and "
                            f"{gate_usage['output_tokens']} output tokens.")
        checkpoint_note = (
            "A separate manual gate test on a local expiry fixture rejected stale evidence, a remaining local "
            "probe and a duplicate request. The admitted request received a usable Astra reply. The known repair "
            "passed independent grading." + receipt_note + "\n\n" + checkpoint_note
        )
    vision_note = "The local fixture has yet to qualify a live image route."
    if vision is not None:
        proof = json.loads((vision / "qualification.json").read_bytes())
        verify_vision_probe(proof, (vision / "mobile.png").read_bytes())
        vision_note = (
            "In a tool-free subscription probe, GPT-6.2 Astra correctly read a randomized code available "
            "only in the rendered PNG. This qualifies image ingestion for the tested model and route. "
            "The current skill routes images through a gateway. Whether a screenshot improves visual "
            "decisions requires the blinded outcome comparison."
        )
    visual_evidence = None
    if visual_pilot is not None:
        visual_evidence, visual_note = visual_pilot_audit(visual_pilot, visual_image_control)
        if visual_review is not None:
            review = collect_review(visual_review, visual_responses, visual_evidence)
            visual_evidence["first_click"] = review
            tally = review["tally"]
            panels = ", ".join(str(tally["panel_response_counts"][str(i)]) for i in range(1, 6))
            visual_note += (f" The pilot received {tally['reviewers']} anonymous first-click responses. "
                            f"Panels 1 through 5 received {panels} responses, respectively. "
                            "The visual audit keeps counts for each starting case and workflow. "
                            "These pilot counts are descriptive.")
        else:
            visual_note += " First-click outcomes remain pending."
        vision_note += "\n\n" + visual_note
    output.mkdir(parents=True, exist_ok=False)
    if run is not None and (run / "run-source.zip").is_file():
        verify_coding_source(run / "run-source.zip", manifest)
        shutil.copyfile(run / "run-source.zip", output / "coding-run-source.zip")
    if run is not None:
        submitted = output / "coding-patches.zip"
        with ZipFile(submitted, "x", ZIP_DEFLATED) as archive:
            for task, arms in sorted(by_task.items()):
                for arm in sorted(arms):
                    archive.write(run / task / arm / "model.patch", f"{task}/{arm}.patch")
        verify_coding_patches(submitted, by_task)
    recovery_records = [row for row in audit["assignments"] if row["state"] == "regraded_after_interruption"]
    if recovery_records:
        with ZipFile(output / "interrupted-source-patches.zip", "x", ZIP_DEFLATED) as archive:
            for row in recovery_records:
                task, arm = row["task_id"], row["arm"]
                patch = (run / task / arm / "recovered/model.patch").read_bytes()
                verify_recovery(row, patch)
                archive.writestr(f"{task}/{arm}.patch", patch)
    redis_note = ""
    if redis_audit is not None:
        replay, redis_note = redis_sensitivity(redis_audit, by_task, peers, checkpoint_audit)
        (output / "redis-sensitivity.json").write_text(json.dumps(replay, indent=2) + "\n", encoding="utf-8")
        with ZipFile(output / "redis-source-patches.zip", "x", ZIP_DEFLATED) as archive:
            for row in replay:
                stem = f"{row['run']}/{row['task_id']}/{row['arm']}"
                archive.write(redis_audit / stem / "without-redis-runtime.patch", f"{stem}.patch")
    (output / "redis-sensitivity.tex").write_text(redis_note + "\n", encoding="utf-8")
    (output / "checkpoint-status.tex").write_text(checkpoint_note + "\n", encoding="utf-8")
    if checkpoint_audit is not None:
        (output / "checkpoint-audit.json").write_text(json.dumps(checkpoint_audit, indent=2) + "\n", encoding="utf-8")
        dispositions = checkpoint_audit["summary"]["prefix_dispositions"]
        if dispositions:
            prefix_patches = output / "checkpoint-prefix-patches.zip"
            with ZipFile(prefix_patches, "x", ZIP_DEFLATED) as archive:
                for task in sorted(dispositions):
                    archive.write(checkpoint / task / "model.patch", f"{task}.patch")
            verify_patch_archive(prefix_patches, {f"{task}.patch": row["workspace_sha256"]
                                                  for task, row in dispositions.items()})
        runner = (ROOT / "eval" / "checkpoint_pilot.py").read_bytes()
        if hashlib.sha256(runner).hexdigest() == checkpoint_audit["plan"].get("runner_sha256"):
            (output / "checkpoint-runner.py").write_bytes(runner)
    if calibration is not None:
        (output / "checkpoint-calibration.json").write_text(json.dumps(calibration, indent=2) + "\n", encoding="utf-8")
    if gate_qualification is not None:
        (output / "gate-qualification.json").write_text(json.dumps(gate_qualification, indent=2) + "\n", encoding="utf-8")
    if gate_events is not None:
        (output / "gate-attempt-journal.json").write_text(json.dumps(gate_events, indent=2) + "\n", encoding="utf-8")
    (output / "matched-resource-audit.json").write_text(json.dumps(resources, indent=2) + "\n", encoding="utf-8")
    appendix_items = []
    if resources["matched_tasks"]:
        plot_matched_resources(resources, output / "matched-resources.pdf")
        appendix_items.append("matched-resources.pdf")
        cache_reads = []
        for arm, label in LABELS.items():
            usage = resources["arms"][arm]
            share = usage["cache_read_share"]
            if share is not None:
                cache_reads.append(f"{label} {usage['cache_read_tokens'] / 1e6:.2f} million ({100 * share:.1f}\\%)")
        match_text = (
            r"\begin{figure}[htbp]" "\n"
            r"\centering" "\n"
            r"\includegraphics[width=\linewidth]{matched-resources.pdf}" "\n"
            f"\\caption{{Descriptive comparison on {len(resources['matched_tasks'])} issues with recorded outcomes "
            "and fully priceable usage for all four workflows. Failed repairs count toward token and cost totals. "
            "Dollar labels are API-equivalent estimates per verified repair.}\n"
            r"\end{figure}" "\n"
        )
        if cache_reads:
            match_text += "\nOn these same issues, cache reads and their share of input were " + ", ".join(cache_reads) + ".\n"
        five_minute = [resources["arms"][arm]["delivery_by_review_seconds"].get("300") for arm in LABELS]
        if all(five_minute):
            count = len(resources["matched_tasks"])
            verified = ", ".join(f"{row['verified']}/{count}" for row in five_minute)
            prices = ", ".join(
                f"{row['estimated_usd_per_timely_verification']:.3f}"
                if row["estimated_usd_per_timely_verification"] is not None else "unavailable" for row in five_minute
            )
            match_text += (
                f"At an exploratory five-minute cutoff, the verified counts were {verified} and the estimated "
                f"costs per timely verification were {prices} USD, both in figure order. "
                "Costs include the full observed runs, including late and failed attempts.\n"
            )
        uncached_estimates = [resources["arms"][arm]["estimated_uncached_input_usd_per_pass"] for arm in LABELS]
        if all(estimate is not None for estimate in uncached_estimates):
            estimates = ", ".join(f"{estimate:.3f}" for estimate in uncached_estimates)
            match_text += (
                "Pricing all observed input at the regular rate gives API-equivalent costs per verified repair "
                f"of USD {estimates}, in figure order. This calculation holds the observed token counts fixed.\n"
            )
    else:
        match_text = "No four-arm issue at this cutoff has fully priceable usage.\n"
    omitted = resources["excluded_for_usage"]
    if omitted:
        reasons = Counter(reason for arms in omitted.values() for reason in arms.values())
        labels = {"missing_executor_attempt": "missing executor attempts", "rerouted_work": "rerouted work",
                  "missing_request_usage": "missing request usage", "unpriceable_request": "unpriceable receipts"}
        reason_text = ", ".join(f"{labels[key]} ({count})" for key, count in sorted(reasons.items()))
        passes = sum(by_task[task]["spot_advisor_22"]["accepted"] for task in omitted)
        match_text += (
            f"Pricing covers {len(resources['matched_tasks'])} of {len(matched)} fully paired issues. "
            f"Excluded arms had {reason_text}. Among the omitted issues, {passes} passed under 2.2.\n"
        )
    sensitivity = resources["missing_cost_sensitivity"]
    if sensitivity:
        break_even = sensitivity["unpriced_usd_to_equal_cost_per_verified"]
        cost_ties = [
            f"USD {break_even[arm]:.{digits}f} against {label}"
            for arm, label, digits in (("astra_only", "Astra alone", 2), ("spot_advisor", "2.1", 3))
            if break_even.get(arm, 0) > 0
        ]
        if cost_ties:
            match_text += (
                "Across all pairs, the unpriced 2.2 cost needed to tie estimated cost per verified repair is "
                + " and ".join(cost_ties) + ".\n"
            )
        if break_even.get("luna_only", 0) < 0:
            match_text += "The known cost per verified repair is already lower for Luna alone.\n"
    (output / "matched-resources.tex").write_text(match_text, encoding="utf-8")
    overhead = resources["first_request_overhead"]
    luna_note = ""
    if overhead["paired_issues"]:
        luna_note = (
            f"Across {overhead['paired_issues']} issues with paired first-request Luna Max receipts, "
            f"the 2.2 policy added a median of {overhead['median']:g} input tokens "
            f"(range {overhead['minimum']} to {overhead['maximum']}). "
            "The first request includes the added decision instructions and tool schema, before any consultation. "
        )
    luna = resources["arms"]["luna_only"]
    policy = resources["arms"]["spot_advisor_22"]
    if all(row["estimated_api_usd_per_pass"] is not None for row in (luna, policy)):
        luna_note += (
            f"In the fully priced four-workflow subset of {len(resources['matched_tasks'])} issues, "
            f"Luna alone passed {luna['passed']} and the 2.2 policy passed {policy['passed']}. "
            f"Estimated API-equivalent cost per verified repair was USD {luna['estimated_api_usd_per_pass']:.3f} "
            f"for Luna alone and USD {policy['estimated_api_usd_per_pass']:.3f} for the 2.2 policy."
        )
    (output / "same-executor.tex").write_text(luna_note + "\n", encoding="utf-8")
    (output / "consultation-funnel.json").write_text(json.dumps(funnel, indent=2) + "\n", encoding="utf-8")
    if visual_evidence is not None:
        archive_visual_artifacts(output / "visual-artifacts.zip", visual_evidence, visual_pilot, visual_image_control)
        (output / "visual-pilot.json").write_text(json.dumps(visual_evidence, indent=2) + "\n", encoding="utf-8")
    if any(row["graded"] for row in peers["peers"].values()):
        peer_archive = output / "peer-artifacts.zip"
        with ZipFile(peer_archive, "x", ZIP_DEFLATED) as archive:
            for peer, record in peers["peers"].items():
                for task in record["graded_rows"]:
                    case_root = peer_roots[peer] / task / peer
                    for name, contents in peer_evidence_files(case_root, peer).items():
                        archive.writestr(f"{peer}/{task}/{peer}/{name}", contents)
        verify_peer_artifacts(peer_archive, peers, by_task, deadline)
    (output / "peer-audit.json").write_text(json.dumps(peers, indent=2) + "\n", encoding="utf-8")
    peer_table = [r"\begin{center}", r"\begin{tabular}{lrrrr}", r"\toprule",
                  r"Workflow & Graded / assigned & Paired & Peer passes & 2.2 passes \\", r"\midrule"]
    for row in peers["peers"].values():
        paired = row["paired"]
        matched_peer = row["matched_metrics"]
        peer_passes = f"{matched_peer['peer']['verified']}/{paired}" if paired else "--"
        spot_passes = f"{matched_peer['spot_22']['verified']}/{paired}" if paired else "--"
        peer_table.append(f"{row['label']} & {row['graded']}/{row['assigned']} & {paired} & "
                          f"{peer_passes} & {spot_passes} " + r"\\")
    peer_table.extend([r"\bottomrule", r"\end{tabular}", r"\end{center}", ""])
    peer_notes = []
    for row in peers["peers"].values():
        label = row["label"]
        matched_peer = row["matched_metrics"]
        tokens = matched_peer["tokens"]
        price = matched_peer["api_usd"]
        waits = matched_peer["deadline_wait"]
        notes = []
        if all(tokens[arm]["per_verified"] is not None for arm in ("peer", "spot_22")):
            notes.append(
                f"On {len(tokens['task_ids'])} paired issues with complete token counts, {label} and 2.2 "
                f"used {tokens['peer']['per_verified'] / 1e6:.3f} and "
                f"{tokens['spot_22']['per_verified'] / 1e6:.3f} million reported tokens per verified repair."
            )
        if all(price[arm]["per_verified"] is not None for arm in ("peer", "spot_22")):
            notes.append(
                f"On {len(price['task_ids'])} paired issues with fully priced receipts, {label} and 2.2 "
                f"used an estimated USD {price['peer']['per_verified']:.3f} and "
                f"USD {price['spot_22']['per_verified']:.3f} per verified repair."
            )
        if all(waits[arm]["mean_wait_seconds"] is not None for arm in ("peer", "spot_22")):
            notes.append(
                f"Mean capped wait across all {row['paired']} pairs was "
                f"{waits['peer']['mean_wait_seconds'] / 60:.1f} and "
                f"{waits['spot_22']['mean_wait_seconds'] / 60:.1f} minutes, respectively."
            )
        if notes:
            peer_notes.append(" ".join(notes))
    if any(row["graded"] < row["assigned"] for row in peers["peers"].values()):
        peer_notes.append("The registered peer comparison remains open.")
    (output / "peer-status.tex").write_text(
        "\n".join(peer_table) + "Paired pass counts use the same issues for both workflows. "
        "The PAL configuration uses its chat tool with a local adapter for the same Astra model, "
        "followed by Luna reconciliation. Second-opinion uses a read-only Claude review followed "
        "by Luna reconciliation. Both workflows attempt a review on each assigned issue. "
        "The 2.2 policy may continue without a consultation. The comparison therefore measures the "
        "full workflows at their respective invocation rates. The receipt audit keeps CLI cost "
        "estimates separate when the API model cannot be identified. Each provider reports tokens "
        "using its own tokenizer.\n\n"
        + "\n\n".join(peer_notes) + "\n", encoding="utf-8")
    (output / "vision-status.tex").write_text(vision_note + "\n", encoding="utf-8")
    if vision is not None:
        shutil.copyfile(vision / "qualification.json", output / "vision-qualification.json")
        shutil.copyfile(vision / "mobile.png", output / "vision-mobile.png")
    (output / "assignment-audit.json").write_text(json.dumps(audit, indent=2) + "\n", encoding="utf-8")
    abstract_note = (f"The snapshot contains {observed} of {4*n} arms and {len(matched)} "
                     f"of {n} fully matched issues.")
    candidate_cost = resources["arms"]["spot_advisor_22"]
    direct_cost = resources["arms"]["astra_only"]
    luna_cost = resources["arms"]["luna_only"]
    endpoints = ("total_tokens_per_pass", "estimated_api_usd_per_pass")
    if all(row[key] is not None and row[key] > 0 for row in (candidate_cost, direct_cost) for key in endpoints):
        token_ratio, cost_ratio = (candidate_cost[key] / direct_cost[key] for key in endpoints)
        abstract_note += (
            f" On {len(resources['matched_tasks'])} issues with complete priceable receipts for all four workflows, "
            f"the 2.2 policy and Astra alone passed {candidate_cost['passed']} and {direct_cost['passed']} issues, "
            f"respectively. Per verified repair, 2.2 used {token_ratio:.2g} times the tokens and "
            f"{cost_ratio:.2g} times the estimated standard API cost of Astra alone."
        )
        if luna_cost["estimated_api_usd_per_pass"] is not None:
            abstract_note += (
                f" Luna alone passed {luna_cost['passed']} of those issues. The estimated costs per verified "
                f"repair were USD {luna_cost['estimated_api_usd_per_pass']:.3f} for Luna alone and "
                f"USD {candidate_cost['estimated_api_usd_per_pass']:.3f} for 2.2."
            )
    if funnel["recorded_arms"] and not metrics["metrics"]["spot_advisor_22"]["consultation_dispatches"]:
        abstract_note += (
            f" The candidate invoked its consultation tool in {funnel['arms_using_ask_tool']} of "
            f"{funnel['recorded_arms']} recorded runs. No advisor dispatch was recorded. "
            "The logs do not explain why invocations ended before dispatch, and the effect of advice remains unresolved."
        )
    if checkpoint_audit is not None:
        screen = checkpoint_audit["summary"]
        if not screen["planned_tasks_without_results"] and not screen["eligible_checkpoint_tasks"]:
            screened = len(checkpoint_audit["plan"]["cases"])
            abstract_note += (f" The complete exploratory screen of {screened} issues saved zero checkpoints "
                              "eligible for a matched advice comparison.")
    quality = audit.get("optimistic_policy_quality")
    if quality and quality["best_case_quality_lower95"] <= manifest["quality_margin"]:
        abstract_note += (" Even the most favorable completion of the planned grades falls short of the "
                          "prespecified quality margin for replacing the 2.1 consultation rule.")
    (output / "abstract-state.tex").write_text(abstract_note + "\n", encoding="utf-8")
    table = [r"\begin{center}", r"\begin{tabular}{lrrrr}", r"\toprule",
             r"Workflow & Saved & Pass & By 15 min & Matched pass \\", r"\midrule"]
    for key, label in LABELS.items():
        item = metrics["metrics"][key]
        if item["records"] != sum(key in arms for arms in by_task.values()) or item["assigned"] != n:
            raise ValueError("Scorecard counts disagree with evidence.")
        count = sum(arms[key]["accepted"] for arms in matched)
        table.append(f"{label} & {item['records']}/{n} & {item['verified']} & "
                     f"{item['verified_by_15_minutes']} & {count}/{len(matched)} " + r"\\")
    completion = ", ".join(f"{audit['counts'][key]['completed_executor_turns']}/"
                           f"{audit['counts'][key]['recorded']}" for key in LABELS)
    accepted_early = sum(item["accepted_without_completed_turn"] for item in audit["counts"].values())
    local_pass = audit["counts"]["spot_advisor_22"]["verifier_failed_after_local_pass"]
    undispatched = funnel["policy_pairs_without_recorded_dispatch"]
    paired_dispatches = funnel["paired_policy_dispatches"]
    only_21 = funnel["policy_pairs_with_only_21_dispatch"]
    dispatch_note = ""
    if paired_dispatches["task_ids"]:
        prior, candidate = (paired_dispatches["arms"][arm] for arm in ("spot_advisor", "spot_advisor_22"))
        dispatch_note = (
            f"Across {len(paired_dispatches['task_ids'])} issues observed under both policies, "
            f"2.1 recorded {prior['dispatches']} advisor dispatches on {prior['issues_with_dispatch']} issues "
            f"and 2.2 recorded {candidate['dispatches']} on {candidate['issues_with_dispatch']}. "
        )
        if only_21["task_ids"]:
            dispatch_note += (
                f"Among the {len(only_21['task_ids'])} issues where only 2.1 dispatched, "
                f"2.1 and 2.2 passed {only_21['passes']['spot_advisor']} and "
                f"{only_21['passes']['spot_advisor_22']}, respectively."
            )
    policy_comparison = metrics["candidate_comparisons"]["spot_advisor"]
    policy_bound = ""
    if policy_comparison:
        policy_bound = (
            f"Across {policy_comparison['pairs']} observed pairs against 2.1, the 95\\% lower confidence bound "
            f"on the pass-rate difference was {100 * policy_comparison['pass_difference_lower95']:+.1f} "
            "percentage points."
        )
    deadline_wait_summary = ""
    wait_audit = resources["matched_deadline_wait"]
    if wait_audit["task_ids"] and all(
            row["mean_wait_seconds"] is not None for row in wait_audit["arms"].values()):
        waits = ", ".join(f"{wait_audit['arms'][arm]['mean_wait_seconds'] / 60:.1f}" for arm in LABELS)
        deadline_wait_summary = (
            f"Across {len(wait_audit['task_ids'])} fully paired issues, mean time to verification "
            f"capped at {deadline / 60:g} minutes was {waits} minutes in table order. "
            "Failures and late verifications contribute the full wait."
        )
    table.extend([r"\bottomrule", r"\end{tabular}", r"\end{center}",
                  "Pass and deadline columns count every recorded arm that met the endpoint. "
                  "The matched column restricts the count to issues with all four observed arms.",
                  deadline_wait_summary,
                  f"Executor turns ended normally on {completion} recorded arms in table order. "
                  f"Saved patches from incomplete turns accounted for {accepted_early} independently verified repairs.",
                  f"In {local_pass} of the recorded 2.2 runs, the last local check passed and the independent "
                  "test suite failed. A local check may exercise a different condition than the independent suite.",
                  "", r"\begin{center}", r"\begin{tabular}{lrrrrr}", r"\toprule",
                  r"Workflow & Calls & Total M & Astra M & Cache reads M & Est. USD \\", r"\midrule"])
    for key, label in LABELS.items():
        item = metrics["metrics"][key]
        cache_reads = [model["cache_read_tokens"] for model in item["models"].values()
                       if model["cache_read_tokens"] is not None]
        cache = f"{sum(cache_reads) / 1e6:.2f}" if cache_reads else "--"
        price = f"{item['priced_request_subtotal_usd']:.2f}" if item["priced_requests"] else "--"
        table.append(f"{label} & {item['consultation_dispatches']} & "
                     f"{item['observed_total_tokens'] / 1e6:.2f} & "
                     f"{item['observed_frontier_tokens'] / 1e6:.4f} & {cache} & {price} " + r"\\")
    digest = hashlib.sha256(evidence).hexdigest()
    recovered = sum(item["regraded_after_interruption"] for item in audit["counts"].values())
    recovered_passes = sum(item["regraded_passes"] for item in audit["counts"].values())
    interrupted_usage = audit["interrupted_usage"]
    interrupted_note = ""
    if interrupted_usage["token_receipts"]:
        subtotal = interrupted_usage["priced_subtotal_usd"]
        price_note = f"The archived receipt subtotal is USD {subtotal:.3f}. " if subtotal is not None else ""
        interrupted_note = (
            f"Their partial journals contain {interrupted_usage['observed_tokens']:,} tokens across "
            f"{interrupted_usage['token_receipts']} of {interrupted_usage['requests_observed']} recorded dispatches. "
            f"{price_note}"
            f"{interrupted_usage['unresolved_dispatches']} dispatches have no terminal receipt. "
            "The main tables require an original arm record."
        )
    bounds = audit["original_pass_difference_bounds"]["spot_advisor"]
    difference = (f"{100 * bounds['difference_lower']:+.1f} to "
                  f"{100 * bounds['difference_upper']:+.1f} percentage points")
    table.extend([r"\bottomrule", r"\end{tabular}", r"\end{center}",
                  "Calls counts recorded advisor dispatches. Usage and prices are observed subtotals. M denotes millions of tokens. "
                  "Cache reads are included in total tokens. Estimated USD uses eligible requests "
                  "and API-equivalent rates. Arms differ in completion and receipt coverage.",
                  "", "Attributable usage receipts by workflow were "
                  + ", ".join(f"{metrics['metrics'][k]['attributed_requests']}/"
                              f"{metrics['metrics'][k]['requests']} ({label})" for k, label in LABELS.items()) + ". "
                  + "Priced request counts in table order were "
                  + ", ".join(str(metrics["metrics"][k]["priced_requests"]) for k in LABELS) + ".",
                  "", f"Among {funnel['recorded_arms']} recorded 2.2 runs, "
                  f"{funnel['arms_with_failed_checks']} had a failed checkpoint, "
                  f"{funnel['arms_with_repeat_failure']} had a repeated-failure trigger, "
                  f"{funnel['arms_using_ask_tool']} invoked the consultation tool ({funnel['ask_tool_calls']} calls), "
                  f"and {funnel['arms_opening_consultation']} opened a consultation. "
                  f"The checkpoints recorded {funnel['gate_admissions']} admissions to the advisor path. "
                  "The recorded tool events do not identify specific rejection conditions.",
                  (f"Among the {funnel['arms_using_ask_tool']} runs that invoked the tool, "
                   f"{funnel['verified_arms_using_ask_tool']} patches passed independent grading. "
                   f"In {funnel['arms_using_ask_tool_with_timeout_code']} of these runs, a check returned "
                   "the sandbox timeout exit code. The logs cannot link those exits to individual admission decisions."
                   if funnel["arms_using_ask_tool"] else ""),
                  dispatch_note,
                  f"On {len(undispatched['task_ids'])} paired issues with no recorded dispatch in either advisor policy, "
                  f"only 2.1 passed on {undispatched['only_21_passed']} and "
                  f"only 2.2 passed on {undispatched['only_22_passed']}.",
                  "", policy_bound,
                  "", f"Post-interruption grading recovered {recovered} assigned artifacts, of which "
                  f"{recovered_passes} passed. These grades are outside the table and the timing analysis. "
                  f"The possible full-cohort pass-rate difference for 2.2 versus 2.1 spans {difference}, "
                  "allowing every missing original outcome to pass or fail. This range describes missing "
                  "outcomes and has no sampling confidence level.",
                  optimistic_note,
                  interrupted_note,
                  "", r"Evidence SHA-256 prefix: \texttt{" + digest[:12] + "}.",
                  "The default-policy gate requires complete original records."
                  if not metrics["complete"] else
                  ("The frozen data clear the default-policy gate."
                   if metrics["default_change_supported"] else
                   "The frozen data do not clear the default-policy gate.")])
    (output / "interim-results.tex").write_text("\n".join(table) + "\n", encoding="utf-8")
    shutil.copyfile(manuscript, output / "main.tex")
    arxiv_metadata(output)
    subprocess.run(["tectonic", "--keep-logs", "main.tex"], cwd=output, check=True)
    with tarfile.open(output / "study22-draft-source.tar.gz", "w:gz") as archive:
        for name in ("main.tex", "abstract-state.tex", "interim-results.tex", "vision-status.tex", "peer-status.tex",
                     "checkpoint-status.tex", "redis-sensitivity.tex", "matched-resources.tex",
                     "same-executor.tex", *appendix_items):
            archive.add(output / name, arcname=name)
    for name in ("manifest.json", "evidence.jsonl", "scorecard.json"):
        shutil.copyfile(snapshot / name, output / name)
    print(output / "main.pdf")
    print(create_package(output))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--manuscript", type=Path, help="LaTeX source for the study manuscript.")
    parser.add_argument("--run", type=Path, help="Original run directory containing independent recovery records.")
    parser.add_argument("--vision", type=Path, help="Qualified direct Codex image probe directory.")
    parser.add_argument("--visual-pilot", type=Path, help="Completed disjoint visual pilot directory.")
    parser.add_argument("--visual-image-control", type=Path, help="Supplemental Luna-with-image run directory.")
    parser.add_argument("--visual-review", type=Path, help="Directory containing prepared blind review panels and key.")
    parser.add_argument("--visual-response", type=Path, action="append", help="One completed blind panel response.")
    parser.add_argument("--second-opinion", type=Path, help="Frozen second-opinion peer run directory.")
    parser.add_argument("--pal", type=Path, help="Frozen PAL MCP peer run directory.")
    parser.add_argument("--claude-native", type=Path, help="Frozen native Advisor peer run directory.")
    parser.add_argument("--checkpoint", type=Path, help="Frozen exploratory public checkpoint run directory.")
    parser.add_argument("--checkpoint-calibration", type=Path, action="append",
                        help="Completed disjoint manual-trigger checkpoint calibration.")
    parser.add_argument("--gate-qualification", type=Path, help="Receipt from an independent live 2.2 gate qualification.")
    parser.add_argument("--redis-audit", type=Path, help="Post-hoc independent NodeBB patch regrades.")
    args = parser.parse_args()
    if args.manuscript is None and not (ROOT / "paper/study22.tex").is_file():
        parser.error("Provide the LaTeX manuscript with --manuscript PATH.")
    if args.visual_image_control and not args.visual_pilot:
        parser.error("--visual-image-control requires --visual-pilot.")
    if (args.visual_review or args.visual_response) and not all(
            (args.visual_review, args.visual_response, args.visual_pilot, args.visual_image_control)):
        parser.error("Visual responses require the prepared panels, visual pilot, and image control.")
    roots = {key: path for key, path in {"second-opinion": args.second_opinion, "pal-mcp": args.pal,
                                        "claude_native": args.claude_native}.items() if path is not None}
    build(args.snapshot.resolve(), args.output.resolve(), args.run, args.vision, args.visual_pilot,
          roots, args.visual_image_control, args.checkpoint, args.redis_audit, args.checkpoint_calibration,
          args.gate_qualification, args.visual_review, args.visual_response, manuscript=args.manuscript)
