"""Verify a frozen offline replication package and rebuild the matched resource figure."""

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile

from checkpoint_report import (calibration_counts, checkpoint_assignment_outcomes, matched_summary,
                               screening_check_counts, screening_diagnostics, screening_timeout_timing)
from cost_report22 import LABELS, matched_resource_audit, plot_matched_resources
from peer_report22 import PEERS, PEER_RECEIPT_FILES, peer_audit, summarize_peer, usage_totals
from report import ASTRA, SCHEMA, advisor_requests, estimate, request_tokens, requests, sum_known
from visual_ratings22 import verify_review

CORE_DATA = ("evidence.jsonl", "manifest.json", "scorecard.json", "assignment-audit.json",
             "consultation-funnel.json", "matched-resource-audit.json", "peer-audit.json")
OPTIONAL_DATA = ("vision-mobile.png", "vision-qualification.json", "visual-pilot.json", "visual-artifacts.zip",
                 "checkpoint-audit.json", "checkpoint-runner.py", "checkpoint-prefix-patches.zip",
                 "checkpoint-calibration.json", "redis-sensitivity.json", "redis-source-patches.zip", "coding-run-source.zip",
                 "coding-patches.zip", "peer-artifacts.zip",
                 "interrupted-source-patches.zip", "gate-qualification.json", "gate-attempt-journal.json")
SOURCES = ("replication22.py", "checkpoint_report.py", "cost_report22.py", "peer_report22.py", "report.py", "pricing.json",
           "telemetry_schema.json", "study22-tasks.json", "visual_ratings22.py")
PAPER_FILES = ("main.pdf", "study22-draft-source.tar.gz", "arxiv-metadata.txt")
CODING_SOURCES = (
    "eval/browser.Dockerfile", "eval/browser_driver.py", "eval/browser_fixture.html", "eval/codex_server.py",
    "eval/diagnostic_cases.py", "eval/direct_session.py", "eval/formal-exclusions.json", "eval/run.py",
    "eval/sandbox.py", "eval/suites.py", "eval/swe_tasks.json", "skills/advisor/scripts/ask_expert.py",
)
SCORING_IMPORTS = (
    "eval/study22.py", "eval/report.py", "eval/pricing.json", "eval/telemetry_schema.json", "eval/run.py",
    "eval/formal-exclusions.json", "eval/codex_server.py", "eval/direct_session.py", "eval/sandbox.py",
    "eval/suites.py", "eval/diagnostic_cases.py", "skills/advisor/scripts/ask_expert.py",
)
REPLAY_SCORECARD = """
import json
import sys
from study22 import ARMS, BILLING, DEADLINE, binomial_upper, comparison, usage_row

by_arm = {arm: {} for arm in ARMS}
for line in sys.stdin.buffer:
    row = json.loads(line)
    by_arm[row["arm"]][row["task_id"]] = row
candidate = by_arm["spot_advisor_22"]
assigned, possible_wins, recorded_losses = map(int, sys.argv[1:])
print(json.dumps({
    "metrics": {arm: usage_row(list(rows.values()), assigned) for arm, rows in by_arm.items()},
    "candidate_comparisons": {arm: comparison(candidate, rows) for arm, rows in by_arm.items()
                              if arm != "spot_advisor_22"},
    "pricing": BILLING,
    "deadline": DEADLINE,
    "best_case_quality_lower95": (1 - binomial_upper(assigned - possible_wins, assigned)
                                   - binomial_upper(recorded_losses, assigned)),
}))
"""
REPLAY_GRADES = """
import json
import sys
from pathlib import Path

from run import grade
from sandbox import checked, docker
from suites import load_swe_instances

source = Path(sys.argv[1])
output = Path(sys.argv[2])
rows = json.load(sys.stdin)
tasks = {task.id: task for task in load_swe_instances(source, sorted({r['task_id'] for r in rows}))}
for row in rows:
    task = tasks[row['task_id']]
    folder = output / task.id / row['workflow']
    row['image_id'] = checked(docker('image', 'inspect', task.image, '--format', '{{.Id}}'))
    row['replay_accepted'], row['replay_verdict'] = grade(task, folder, (folder / 'model.patch').read_bytes(), folder)
    (folder / 'regrade.json').write_text(json.dumps(row, indent=2) + '\\n', encoding='utf-8')
    print(f"{row['workflow']} {task.id}: {row['original_accepted']} -> {row['replay_accepted']}", flush=True)
if any(r['original_accepted'] != r['replay_accepted'] for r in rows):
    raise SystemExit('At least one independent regrade differs from the recorded acceptance.')
"""
README = """# Spot Advisor 2.2 interim replication package

The data describe a frozen interim snapshot. Use Python 3.11 or later
to verify the file digests and rerun the receipt-based calculations from
the extracted directory:

```sh
python -m pip install jsonschema matplotlib
python -B analysis/replication22.py --rebuild restored
```

Analysis requires no model account, credentials, Docker, or network
once the Python analysis dependencies are installed. The verifier
reconciles coding arm counts and the availability of usage receipts,
then regenerates the matched cost audit and comparison figure. It also
recalculates peer and checkpoint comparisons from the case-level grade
and usage records.
When frozen coding source is available, it replays the full arm scorecard
and paired intervals using the original scoring functions.

The LaTeX input in `paper/study22-draft-source.tar.gz` builds separately
with two pdflatex passes from a fresh directory. The companion PDF is
the manuscript at this cutoff. The peer and visual audit files record
observed workflow dispositions. They do not include raw peer provider
transcripts or hidden tests. When supplied, anonymous first-click
choices are scoped to the exact screenshots and retallied offline.
When present, data/peer-artifacts.zip holds submitted peer patches and
the request fields needed to recalculate usage. The verifier rebuilds
the graded peer rows from these files.

When present, data/visual-artifacts.zip contains the starting pages,
edited pages and screenshots shown in the visual pilot. The verifier
checks the reported page changes and screenshot digests against those
files. Browser checks, image ingestion, and any first-click responses
have separate evidence.

When present, data/coding-run-source.zip holds the source snapshot for
the main coding comparison. The verifier checks its execution fingerprint
and both advisor versions against the run manifest. Model replay still
requires the specified benchmark images and authorized provider access.
The optional data/coding-patches.zip preserves the original submitted
patch for each recorded arm, including empty and rejected patches. The
verifier matches each patch to its receipt. Regrading needs the pinned
upstream grader and task images and does not require a model account.
With the clean SWE-bench checkout at the revision listed in
analysis/study22-tasks.json and the task image available locally, run:

```sh
python -B analysis/replication22.py --regrade TASK_ID WORKFLOW --swe-source /path/to/swe-bench --regrade-output regraded
```

Select a recorded task ID and workflow from data/evidence.jsonl or the
graded rows in data/peer-audit.json. For a screening patch in
data/checkpoint-prefix-patches.zip, use its task ID from
data/checkpoint-audit.json and the workflow `checkpoint-prefix`.
Repeat `--regrade` for multiple patches. The output stores the original
and replayed acceptance and the local grader image ID separately from
the timed benchmark records.

When present, data/interrupted-source-patches.zip contains the artifacts
from post-interruption grading. Their digests are checked separately
from the original timed outcomes. The assignment audit preserves the
available request receipts and counts unresolved dispatches separately.
The separate manual gate qualification includes a sanitized journal
when the original run is available. It checks the recorded dispatch and
receipt on the diagnostic fixture.
"""


def create_package(draft: Path) -> Path:
    base = Path(__file__).resolve().parent
    files = {f"data/{name}": (draft / name).read_bytes() for name in CORE_DATA}
    files.update({f"data/{name}": (draft / name).read_bytes() for name in OPTIONAL_DATA
                  if (draft / name).is_file()})
    files.update({f"analysis/{name}": (base / name).read_bytes() for name in SOURCES})
    files.update({f"paper/{name}": (draft / name).read_bytes() for name in PAPER_FILES})
    files["README.md"] = README.encode("utf-8")
    inventory = {name: hashlib.sha256(contents).hexdigest() for name, contents in sorted(files.items())}
    destination = draft / "study22-replication.zip"
    with ZipFile(destination, "x", ZIP_DEFLATED) as package:
        for name, contents in sorted(files.items()):
            package.writestr(name, contents)
        package.writestr("sha256.json", json.dumps(inventory, indent=2) + "\n")
    return destination


def checked_inventory(package: Path) -> None:
    inventory = json.loads((package / "sha256.json").read_bytes())
    required = ({f"data/{name}" for name in CORE_DATA}
                | {f"analysis/{name}" for name in SOURCES}
                | {f"paper/{name}" for name in PAPER_FILES}
                | {"README.md"})
    if not required <= inventory.keys() <= required | {f"data/{name}" for name in OPTIONAL_DATA}:
        raise ValueError("Unexpected replication package inventory.")
    for name, expected in inventory.items():
        path = PurePosixPath(name)
        data = package.joinpath(*path.parts).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"Checksum mismatch: {name}")


def verify_coding_source(path: Path, manifest: dict) -> None:
    with ZipFile(path) as source:
        names = source.namelist()
        if len(names) != len(set(names)) or any(
            PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "\\" in name
            for name in names
        ):
            raise ValueError("Unexpected inventory in the frozen code archive.")
        if json.loads(source.read("manifest.json")) != manifest:
            raise ValueError("The frozen code archive contains a different run manifest.")
        for name, key in (("skills/advisor/scripts/ask_expert.py", "candidate_advisor_sha256"),
                          ("advisor-21-frozen.py", "baseline_advisor_sha256")):
            if hashlib.sha256(source.read(name)).hexdigest() != manifest[key]:
                raise ValueError(f"Advisor source does not match the frozen record: {name}")
        digest = hashlib.sha256()
        for name in sorted(CODING_SOURCES):
            text = source.read(name).replace(b"\r\n", b"\n")
            digest.update(name.encode("utf-8") + b"\0" + text + b"\0")
        study = source.read("eval/study22.py").decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        combined = hashlib.sha256((digest.hexdigest() + study).encode("utf-8")).hexdigest()
        if combined != manifest["source_sha256"]:
            raise ValueError("The archived code does not match the recorded execution fingerprint.")


def verify_patch_archive(path: Path, expected: dict) -> None:
    with ZipFile(path) as archive:
        if sorted(archive.namelist()) != sorted(expected):
            raise ValueError(f"{path.name} differs from the recorded patch inventory.")
        for name, digest in expected.items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError(f"The archived patch differs from its receipt: {name}")


def verify_coding_patches(path: Path, by_task: dict) -> None:
    verify_patch_archive(path, {f"{task}/{arm}.patch": row["artifact_sha256"]
                                for task, arms in by_task.items() for arm, row in arms.items()})


def verify_gate_qualification(record: dict, manifest: dict, events: list[dict] | None = None) -> None:
    if (record["execution_source_sha256"] != manifest["source_sha256"]
            or record["candidate_advisor_sha256"] != manifest["candidate_advisor_sha256"]
            or record["task"] in {task["id"] for task in manifest["tasks"]}):
        raise ValueError("The gate qualification must use the frozen candidate on a disjoint fixture.")
    checks = ("fixture_initial_assertion_failed", "rejected_when_local_probe_remained", "rejected_stale_checkpoint",
              "blocked_duplicate", "advisor_reply_usable", "known_repair_passed_public_check", "accepted")
    attempts = record["reported_attempts"]
    if (any(record[key] is not True for key in checks) or record["advisor_status"] != "ok"
            or record["known_repair_independent_grade"] != "passed" or record["dispatches"] != 1
            or len(attempts) != 1 or attempts[0]["model"] != ASTRA or attempts[0]["status"] != "ok"):
        raise ValueError("The archived gate qualification does not verify a complete consultation path.")
    if events is not None:
        identities = {(row["task_id"], row["revision"], row["round"]) for row in events}
        if ([row["event"] for row in events] != ["started", "dispatch", "finished"]
                or any(row["dispatch_possible"] is not flag for row, flag in zip(events, (False, True, True)))
                or len(identities) != 1 or any(row["task_id"] != record["task"] or row["round"] != 1
                                              or row["transport"] != "codex" or row["requested_model"] != ASTRA
                                              for row in events)
                or {key: events[-1][key] for key in ("model", "status", "usage")} != attempts[0]):
            raise ValueError("The gate attempt journal does not reproduce the reported live consultation.")


def matches_report(actual, expected) -> bool:
    if isinstance(actual, float) and type(expected) in (float, int):
        return math.isclose(actual, expected, rel_tol=1e-10, abs_tol=1e-12)
    if isinstance(actual, dict) and isinstance(expected, dict):
        return actual.keys() == expected.keys() and all(matches_report(value, expected[key])
                                                        for key, value in actual.items())
    if isinstance(actual, list) and isinstance(expected, list):
        return len(actual) == len(expected) and all(matches_report(a, b) for a, b in zip(actual, expected))
    return type(actual) is type(expected) and actual == expected


def optimistic_quality_counts(manifest: dict, by_task: dict) -> dict:
    pairs = []
    for task in manifest["tasks"]:
        arms = by_task.get(task["id"], {})
        pairs.append(tuple(arms.get(key, {}).get("accepted") for key in ("spot_advisor_22", "spot_advisor")))
    return {"possible_wins": sum(candidate is not False and baseline is not True for candidate, baseline in pairs),
            "recorded_losses": pairs.count((False, True)), "missing_pairs": sum(None in pair for pair in pairs)}


def verify_frozen_scorecard(data: Path, score: dict, manifest: dict, by_task: dict) -> None:
    optimistic = optimistic_quality_counts(manifest, by_task)
    with TemporaryDirectory(prefix="spot-advisor-frozen-scoring-") as temporary:
        root = Path(temporary)
        with ZipFile(data / "coding-run-source.zip") as source:
            for name in SCORING_IMPORTS:
                source.extract(name, root)
        result = subprocess.run(
            [sys.executable, "-B", "-c", REPLAY_SCORECARD, str(len(manifest["tasks"])),
             str(optimistic["possible_wins"]), str(optimistic["recorded_losses"])],
            cwd=root / "eval", input=(data / "evidence.jsonl").read_bytes(),
            capture_output=True, check=True,
        )
    replayed = json.loads(result.stdout)
    if replayed["pricing"] != score["pricing"] or replayed["deadline"] != manifest["review_deadline_seconds"]:
        raise ValueError("Report inputs disagree with the frozen pricing or deadline.")
    if not matches_report(replayed["metrics"], score["metrics"]):
        raise ValueError("The arm scorecard disagrees with the frozen receipt accounting.")
    if not matches_report(replayed["candidate_comparisons"], score["candidate_comparisons"]):
        raise ValueError("Paired comparison intervals disagree with the frozen scoring code.")
    audit = json.loads((data / "assignment-audit.json").read_bytes())
    optimistic["best_case_quality_lower95"] = replayed["best_case_quality_lower95"]
    if not matches_report(optimistic, audit["optimistic_policy_quality"]):
        raise ValueError("The best-case quality bound disagrees with the original outcomes.")
    print("Recalculated the arm scorecard, cache accounting and paired intervals with the frozen scoring code.")


def consultation_funnel(by_task: dict) -> dict:
    rows = []
    for task, arms in by_task.items():
        if "spot_advisor_22" in arms:
            row = arms["spot_advisor_22"]
            rows.append({"task_id": task, "failed_check": any(not c["passed"] for c in row["checkpoints"]),
                         "repeat_failure": any(c["trigger"] == "two_failed_checks" for c in row["checkpoints"]),
                         "check_with_timeout_code": any(c["exit_code"] == 124 for c in row["checkpoints"]),
                         "ask_tool_calls": sum(e["tool"] == "ask_at_checkpoint" for e in row["tool_events"]),
                         "gate_admissions": sum(c.get("consultation_decision") == "requested"
                                                for c in row["checkpoints"]),
                         "opened_consultation": bool(row["consultations"]), "verified": row["accepted"]})
    policies = ("spot_advisor", "spot_advisor_22")
    pairs = {task: arms for task, arms in by_task.items() if all(arm in arms for arm in policies)}
    dispatches = {arm: {task: advisor_requests(task_arms[arm]) for task, task_arms in pairs.items()}
                  for arm in policies}
    counts = {}
    for arm, cases in dispatches.items():
        sent = [attempt for attempts in cases.values() for attempt in attempts]
        counts[arm] = {"issues_with_dispatch": sum(bool(attempts) for attempts in cases.values()),
                       "dispatches": len(sent),
                       "successful_replies": sum(call["status"] == "ok" for task_arms in pairs.values()
                                                 for call in task_arms[arm]["consultations"]),
                       "frontier_tokens": (sum_known(request_tokens(a) for a in sent if a["model"] == ASTRA)
                                            if all(a.get("model") for a in sent) else None)}
    no_dispatch = [task for task in pairs if not any(dispatches[arm][task] for arm in policies)]
    only_21_dispatched = [task for task in pairs
                          if dispatches["spot_advisor"][task] and not dispatches["spot_advisor_22"][task]]
    outcomes = [(pairs[task]["spot_advisor"]["accepted"], pairs[task]["spot_advisor_22"]["accepted"])
                for task in no_dispatch]
    asked = [row for row in rows if row["ask_tool_calls"]]
    return {
        "recorded_arms": len(rows),
        "arms_with_failed_checks": sum(row["failed_check"] for row in rows),
        "arms_with_repeat_failure": sum(row["repeat_failure"] for row in rows),
        "arms_using_ask_tool": len(asked),
        "verified_arms_using_ask_tool": sum(row["verified"] for row in asked),
        "arms_using_ask_tool_with_timeout_code": sum(row["check_with_timeout_code"] for row in asked),
        "ask_tool_calls": sum(row["ask_tool_calls"] for row in rows),
        "gate_admissions": sum(row["gate_admissions"] for row in rows),
        "arms_opening_consultation": sum(row["opened_consultation"] for row in rows),
        "rows": sorted(rows, key=lambda row: row["task_id"]),
        "paired_policy_dispatches": {"task_ids": sorted(pairs), "arms": counts},
        "policy_pairs_with_only_21_dispatch": {
            "task_ids": sorted(only_21_dispatched),
            "passes": {arm: sum(pairs[task][arm]["accepted"] for task in only_21_dispatched) for arm in policies},
        },
        "policy_pairs_without_recorded_dispatch": {
            "task_ids": sorted(no_dispatch), "only_21_passed": outcomes.count((True, False)),
            "only_22_passed": outcomes.count((False, True)),
        },
    }


def verify_peers(audit: dict, study_plan: bytes, by_task: dict, deadline: float = 900) -> None:
    if audit["assignment_sha256"] != hashlib.sha256(study_plan).hexdigest():
        raise ValueError("Peer audit does not use the packaged assignment plan.")
    cases = json.loads(study_plan)["peer_tasks"]
    if audit["peers"].keys() != PEERS.keys():
        raise ValueError("Peer audit is missing a registered workflow.")
    for peer, recorded in audit["peers"].items():
        rows = recorded["graded_rows"]
        for task, row in rows.items():
            baseline = by_task.get(task, {}).get("spot_advisor_22")
            accepted = baseline["accepted"] if baseline is not None else None
            elapsed = baseline["wall_seconds"] if baseline is not None else None
            delta = None if accepted is None else int(row["peer_pass"]) - int(accepted)
            usage = (usage_totals(requests(baseline)) if baseline and baseline["executor_attempts"]
                     and not baseline["reroutes"] else None)
            if (row["spot_22_pass"], row["spot_22_wall_seconds"], row["peer_minus_spot_22"], row["spot_22_usage"]) != (
                    accepted, elapsed, delta, usage):
                raise ValueError(f"Peer comparison disagrees with the matched coding record: {peer}/{task}")
        summary = summarize_peer(cases, rows, recorded["ungraded_assignments"], deadline)
        if {"label": PEERS[peer], **summary} != recorded:
            raise ValueError(f"Peer summary disagrees with the case-level records: {peer}")


def verify_peer_artifacts(path: Path, audit: dict, by_task: dict, deadline: int) -> int:
    required, optional = set(), set()
    for peer, data in audit["peers"].items():
        for task in data["graded_rows"]:
            prefix = f"{peer}/{task}/{peer}"
            required.update(f"{prefix}/{name}" for name in ("result.json", "model.patch"))
            optional.update(f"{prefix}/{name}" for name in PEER_RECEIPT_FILES[peer])
    with ZipFile(path) as archive, TemporaryDirectory(prefix="spot-peer-") as tmp:
        names = archive.namelist()
        if (len(names) != len(set(names)) or not required <= set(names) <= required | optional
                or any(PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts or "\\" in name
                       for name in names)):
            raise ValueError("Peer artifact inventory differs from the graded peer cases.")
        root = Path(tmp)
        for name in names:
            output = root.joinpath(*PurePosixPath(name).parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(archive.read(name))
        replayed = peer_audit({peer: root / peer for peer in PEERS}, by_task, deadline)
    for peer, row in audit["peers"].items():
        if not matches_report(replayed["peers"][peer]["graded_rows"], row["graded_rows"]):
            raise ValueError(f"Peer artifact or usage receipts disagree with the audit: {peer}")
    if not matches_report(replayed["native_pricing"], audit["native_pricing"]):
        raise ValueError("The peer audit does not use the packaged native pricing.")
    return len(required) // 2


def verify_checkpoints(audit: dict, study_plan: dict, by_task: dict | None = None, *, deadline: int) -> None:
    plan, reported = audit["plan"], audit["summary"]
    if (plan["cases"] != study_plan["tasks"] or plan["source_revision"] != study_plan["source_revision"]
            or any(plan.get(flag) is not True for flag in ("natural_trigger", "require_behavioral_check",
                                                           "compare_questions"))):
        raise ValueError("Checkpoint report uses a different plan.")
    eligible = reported["eligible_checkpoint_tasks"]
    dispositions = reported["prefix_dispositions"]
    saved = set(eligible)
    if (len(saved) != len(eligible) or saved & dispositions.keys()
            or (saved | dispositions.keys()) - set(plan["cases"])):
        raise ValueError("Checkpoint screening dispositions are inconsistent with the assignment plan.")
    screening_checks = reported["screening_checks"]
    if (screening_checks.keys() != saved | dispositions.keys()
            or screening_check_counts(screening_checks) != reported["screening_check_counts"]):
        raise ValueError("Checkpoint screening counts disagree with the audited checks.")
    if screening_diagnostics(dispositions, saved, by_task or {}) != reported["screening_diagnostics"]:
        raise ValueError("Checkpoint prefix diagnostics disagree with the audited dispositions.")
    timing = reported.get("screening_timeout_timing")
    if timing is not None:
        recomputed = screening_timeout_timing(dispositions, timing["elapsed_by_task"], plan["prefix_seconds"])
        if recomputed != timing:
            raise ValueError("Checkpoint timeout timing disagrees with the archived offsets.")
    prefix_receipts = reported["screening_prefix_receipts"]
    prefix_rows = [row for case in prefix_receipts.values() for row in case]
    if (prefix_receipts.keys() != saved | dispositions.keys()
            or any(row["role"] != "executor" for row in prefix_rows)
            or not matches_report(observed_request_usage(prefix_rows), reported["screening_prefix_usage"])):
        raise ValueError("Checkpoint screening usage disagrees with the archived prefix receipts.")
    rows = reported["continuations"]
    assignments = reported["checkpoint_assignments"]
    if (assignments.keys() != saved or
            checkpoint_assignment_outcomes(rows, assignments, deadline) != reported["assignment_outcomes"]):
        raise ValueError("Checkpoint assignment outcomes disagree with the saved continuations.")
    computed = matched_summary(rows, tuple(plan["cases"]))
    computed["planned_tasks_without_results"] = [task for task in computed["planned_tasks_without_results"]
                                                 if task not in dispositions]
    if any(reported[key] != value for key, value in computed.items()):
        raise ValueError("Checkpoint summary disagrees with the case-level records.")


def verify_vision_probe(proof: dict, image: bytes) -> None:
    if (proof.get("probe") != "direct_codex_image_ingestion" or proof.get("route") != "qualified"
            or proof.get("code_match") is not True or proof.get("request_status") != "completed"
            or proof.get("reported_model") != ASTRA or proof.get("requested_model") != ASTRA
            or proof.get("snapshot_sha256") != hashlib.sha256(image).hexdigest()
            or not proof["checks"] or not all(value is True for value in proof["checks"].values())):
        raise ValueError("Expected a qualified direct Codex image probe and matching screenshot.")


def verify_visual_artifacts(path: Path, evidence: dict) -> int:
    manifest = evidence["manifest"]
    orders = manifest["arms_by_case"]
    pilot_arms = {"luna_only", "luna_text_advice", "luna_image_advice", "astra_only"}
    control = evidence.get("supplemental_image_control")
    if (manifest["stage"] != "disjoint_visual_pilot" or not orders
            or any(group.keys() != orders.keys() for group in
                   (evidence["starting"], evidence["cases"], manifest["initial_sha256"]))
            or any(len(arms) != len(pilot_arms) or set(arms) != pilot_arms for arms in orders.values())):
        raise ValueError("Visual pilot results disagree with the workflow manifest.")
    expected = {"manifest.json"}
    checked_outputs = 0
    with ZipFile(path) as archive:
        raw_manifest = archive.read("manifest.json")
        if json.loads(raw_manifest) != manifest:
            raise ValueError("The archived visual manifest differs from the visual audit.")
        if control is not None:
            if (control["manifest"]["stage"] != "exploratory_visual_control"
                    or control["manifest"]["original_manifest_sha256"] != hashlib.sha256(raw_manifest).hexdigest()
                    or control["manifest"]["arm"] != "luna_with_image" or control["cases"].keys() != orders.keys()):
                raise ValueError("The supplemental visual control does not match the original pilot.")
        for case, arms in orders.items():
            starting_path = f"{case}/starting/index.html"
            starting_image = f"{case}/starting/mobile.png"
            expected.update((starting_path, starting_image))
            start = archive.read(starting_path).decode("utf-8").replace("\r\n", "\n")
            if hashlib.sha256(start.encode("utf-8")).hexdigest() != manifest["initial_sha256"][case]:
                raise ValueError(f"Original visual fixture changed: {case}")
            start_record = evidence["starting"][case]
            if (hashlib.sha256(archive.read(starting_image)).hexdigest() != start_record["snapshot_sha256"]
                    or start_record["viewport"] != manifest["viewport"] or not start_record["checks"]
                    or not all(value is True for value in start_record["checks"].values())):
                raise ValueError(f"Original visual screenshot changed: {case}")
            records = evidence["cases"][case]
            if records.keys() != set(arms):
                raise ValueError(f"Visual pilot workflow results are incomplete: {case}")
            if control is not None:
                records = {**records, "luna_with_image": control["cases"][case]}
            for arm, row in records.items():
                page, image = f"{case}/{arm}/index.html", f"{case}/{arm}/mobile.png"
                expected.update((page, image))
                changed = archive.read(page).decode("utf-8").replace("\r\n", "\n") != start
                if (row["arm"] != arm or row["changed"] is not changed or row["viewport"] != manifest["viewport"]
                        or row["snapshot_sha256"] != hashlib.sha256(archive.read(image)).hexdigest()
                        or row["checks"].keys() != start_record["checks"].keys()
                        or not all(type(value) is bool for value in row["checks"].values())
                        or row["functional_pass"] is not all(row["checks"].values())):
                    raise ValueError(f"Visual evidence differs from the archived output: {case}/{arm}")
                checked_outputs += 1
        if sorted(archive.namelist()) != sorted(expected):
            raise ValueError("Unexpected visual archive inventory.")
    if evidence["first_click"] is not None:
        if control is None:
            raise ValueError("First-click review requires the paired Luna image control.")
        verify_review(evidence, evidence["first_click"])
    return checked_outputs


def verify_redis_origin(row: dict, by_task: dict, peers: dict, checkpoint: dict | None) -> None:
    task, arm = row["task_id"], row["arm"]
    if arm == "checkpoint-prefix":
        source = checkpoint["summary"]["prefix_dispositions"] if checkpoint is not None else {}
        original, digest, accepted, verdict = source.get(task), "workspace_sha256", "accepted", "verdict"
    elif arm in peers["peers"]:
        source = peers["peers"][arm]["graded_rows"]
        original, digest, accepted, verdict = source.get(task), "artifact_sha256", "peer_pass", "peer_verdict"
    else:
        original = by_task.get(task, {}).get(arm)
        digest, accepted, verdict = "artifact_sha256", "accepted", "verdict"
    if (original is None or original[digest] != row["original_sha256"]
            or original[accepted] is not False or row["original_accepted"] is not False
            or original[verdict] != "patch_rejected"):
        raise ValueError(f"Redis replay does not match the originally rejected patch: {row['run']}/{task}/{arm}")


def original_assignment(task: str, arm: str, row: dict | None) -> dict:
    record = {"task_id": task, "arm": arm, "state": "unobserved", "original_accepted": None}
    if row is not None:
        record.update(state="recorded", original_accepted=row["accepted"],
                      original_turn_status=row["turn"].get("status"), original_verdict=row["verdict"],
                      last_local_check_passed=row["checkpoints"][-1]["passed"] if row["checkpoints"] else None)
    return record


def verify_recovery(record: dict, patch: bytes) -> None:
    capture, grade = record["recovery_capture"], record["recovery_grade"]
    identity = (record["task_id"], record["arm"], hashlib.sha256(patch).hexdigest())
    if ((capture["task"], capture["arm"], capture["sha256"]) != identity
            or capture["timed_outcome"] is not None or grade["is_post_interruption_regrade"] is not True
            or type(grade["accepted"]) is not bool):
        raise ValueError(f"Invalid post-interruption grade for {identity[0]}/{identity[1]}")


def observed_request_usage(attempts: list[dict]) -> dict:
    fields = {"role", "requested_model", "model", "status", "usage"}
    usage_fields = {"input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens",
                    "reasoning_output_tokens"}
    for row in attempts:
        usage = row["usage"]
        if (row.keys() != fields or row["role"] not in {"executor", "advisor"}
                or (usage is not None and (not usage.keys() <= usage_fields or any(
                    value is not None and (type(value) is not int or value < 0) for value in usage.values())))
                or (row["status"] == "dispatch_unresolved" and (row["model"] is not None or usage is not None))):
            raise ValueError("Invalid archived request receipt.")
    attributed = [row for row in attempts if row["model"] and request_tokens(row) is not None]
    prices = [price for row in attributed if (price := estimate(row)) is not None]
    cache = [row["usage"]["cached_input_tokens"] for row in attributed
             if row["usage"].get("cached_input_tokens") is not None]
    return {"requests_observed": len(attempts), "token_receipts": len(attributed),
            "unresolved_dispatches": sum(row["status"] == "dispatch_unresolved" for row in attempts),
            "observed_tokens": sum(request_tokens(row) for row in attributed) if attributed else None,
            "observed_input_tokens": sum(row["usage"]["input_tokens"] for row in attributed) if attributed else None,
            "observed_output_tokens": sum(row["usage"]["output_tokens"] for row in attributed) if attributed else None,
            "cache_read_receipts": len(cache), "observed_cache_read_tokens": sum(cache) if cache else None,
            "priced_requests": len(prices), "priced_subtotal_usd": sum(prices) if prices else None}


def assignment_totals(manifest: dict, assignments: list[dict]) -> dict:
    counts = {}
    for arm in LABELS:
        rows = [row for row in assignments if row["arm"] == arm]
        counts[arm] = {state: sum(row["state"] == state for row in rows) for state in
                       ("recorded", "regraded_after_interruption", "unobserved")}
        counts[arm]["original_passes"] = sum(row["original_accepted"] is True for row in rows)
        counts[arm]["completed_executor_turns"] = sum(row.get("original_turn_status") == "completed" for row in rows)
        counts[arm]["accepted_without_completed_turn"] = sum(
            row["original_accepted"] is True and row.get("original_turn_status") != "completed" for row in rows)
        counts[arm]["verifier_failed_after_local_pass"] = sum(
            row.get("original_verdict") == "verifier_failed" and row.get("last_local_check_passed") is True for row in rows)
        counts[arm]["regraded_passes"] = sum(row.get("recovery_grade", {}).get("accepted") is True for row in rows)
    bounds = {}
    n = len(manifest["tasks"])
    candidate = counts["spot_advisor_22"]
    for arm, baseline in counts.items():
        if arm != "spot_advisor_22":
            difference = candidate["original_passes"] - baseline["original_passes"]
            bounds[arm] = {
                "difference_lower": (difference - (n - baseline["recorded"])) / n,
                "difference_upper": (difference + (n - candidate["recorded"])) / n,
            }
    attempts = [attempt for row in assignments for attempt in row.get("interrupted_attempts", [])]
    return {"counts": counts, "original_pass_difference_bounds": bounds,
            "interrupted_usage": observed_request_usage(attempts)}


def verify_assignments(data: Path, manifest: dict, by_task: dict, registered: set) -> None:
    audit = json.loads((data / "assignment-audit.json").read_bytes())
    records = audit["assignments"]
    keys = {(record["task_id"], record["arm"]) for record in records}
    if len(keys) != len(records) or keys != registered:
        raise ValueError("Disposition records do not match the frozen coding assignments.")
    recoveries = {}
    for record in records:
        task, arm = record["task_id"], record["arm"]
        evidence = by_task.get(task, {}).get(arm)
        expected = original_assignment(task, arm, evidence)
        if evidence is None and record["state"] == "regraded_after_interruption":
            expected.update(state=record["state"], recovery_capture=record["recovery_capture"],
                            recovery_grade=record["recovery_grade"], interrupted_attempts=record["interrupted_attempts"])
            recoveries[f"{task}/{arm}.patch"] = record
        if expected != record:
            raise ValueError(f"Assignment audit disagrees with the original outcome: {task}/{arm}")
    archive = data / "interrupted-source-patches.zip"
    if recoveries or archive.is_file():
        with ZipFile(archive) as patches:
            if sorted(patches.namelist()) != sorted(recoveries):
                raise ValueError("Interrupted patch archive does not match the recovery audit.")
            for name, record in recoveries.items():
                verify_recovery(record, patches.read(name))
    if any(audit[key] != value for key, value in assignment_totals(manifest, records).items()):
        raise ValueError("Assignment totals or missing-outcome bounds disagree with the case records.")
    print(f"Verified {len(records)} frozen assignment dispositions, including {len(recoveries)} interruption regrades.")


def verify(package: Path, rebuilt: Path | None) -> None:
    checked_inventory(package)
    data = package / "data"
    manifest = json.loads((data / "manifest.json").read_bytes())
    if (data / "coding-run-source.zip").is_file():
        verify_coding_source(data / "coding-run-source.zip", manifest)
    gate = data / "gate-qualification.json"
    journal = data / "gate-attempt-journal.json"
    if gate.is_file():
        events = json.loads(journal.read_bytes()) if journal.is_file() else None
        verify_gate_qualification(json.loads(gate.read_bytes()), manifest, events)
        print("Verified the separate live gate qualification against the frozen candidate.")
    elif journal.is_file():
        raise ValueError("The gate attempt journal is missing its qualification record.")
    registered = {(task["id"], arm) for task in manifest["tasks"] for arm in task["arms"]}
    by_task = {}
    for line in (data / "evidence.jsonl").read_bytes().splitlines():
        row = json.loads(line)
        SCHEMA.validate(row)
        task, arm = row["task_id"], row["arm"]
        arms = by_task.setdefault(task, {})
        if (task, arm) not in registered or arm in arms or row["source_sha256"] != manifest["source_sha256"]:
            raise ValueError(f"Unexpected or duplicate outcome: {task}/{arm}")
        arms[arm] = row
    if (data / "coding-patches.zip").is_file():
        verify_coding_patches(data / "coding-patches.zip", by_task)
        print(f"Verified {sum(map(len, by_task.values()))} original submitted patches against the arm receipts.")
    if consultation_funnel(by_task) != json.loads((data / "consultation-funnel.json").read_bytes()):
        raise ValueError("Consultation counts disagree with the checkpoint and request records.")
    study_plan = (package / "analysis/study22-tasks.json").read_bytes()
    registered_study = json.loads(study_plan)
    peers = json.loads((data / "peer-audit.json").read_bytes())
    deadline = manifest["review_deadline_seconds"]
    verify_peers(peers, study_plan, by_task, deadline)
    if (data / "peer-artifacts.zip").is_file():
        checked_peers = verify_peer_artifacts(data / "peer-artifacts.zip", peers, by_task, deadline)
        print(f"Rebuilt {checked_peers} graded peer rows from submitted patches and sanitized usage receipts.")
    checkpoint = None
    if (data / "checkpoint-audit.json").is_file():
        checkpoint = json.loads((data / "checkpoint-audit.json").read_bytes())
        verify_checkpoints(checkpoint, registered_study, by_task, deadline=deadline)
        print("Recalculated checkpoint comparisons and verified screening dispositions.")
    checkpoint_runner = data / "checkpoint-runner.py"
    if checkpoint_runner.is_file():
        digest = hashlib.sha256(checkpoint_runner.read_bytes()).hexdigest()
        if checkpoint is None or checkpoint["plan"].get("runner_sha256") != digest:
            raise ValueError("Checkpoint runner source differs from the recorded plan.")
        print("Verified the checkpoint runner source against the recorded plan.")
    prefix_patches = data / "checkpoint-prefix-patches.zip"
    if prefix_patches.is_file():
        if checkpoint is None:
            raise ValueError("Checkpoint prefix patches require recorded dispositions.")
        dispositions = checkpoint["summary"]["prefix_dispositions"]
        verify_patch_archive(prefix_patches, {f"{task}.patch": row["workspace_sha256"]
                                              for task, row in dispositions.items()})
        print(f"Verified {len(dispositions)} original checkpoint prefix patches.")
    if (data / "checkpoint-calibration.json").is_file():
        calibration = json.loads((data / "checkpoint-calibration.json").read_bytes())
        if calibration_counts(calibration["trials"], registered_study["tasks"]) != calibration["counts"]:
            raise ValueError("Calibration summary disagrees with the case-level records.")
        print("Verified the separate diagnostic calibration cases and grades.")
    if (data / "vision-qualification.json").is_file():
        verify_vision_probe(json.loads((data / "vision-qualification.json").read_bytes()),
                            (data / "vision-mobile.png").read_bytes())
        print("Verified the vision probe receipt against its original screenshot.")
    if (data / "visual-pilot.json").is_file():
        count = verify_visual_artifacts(data / "visual-artifacts.zip", json.loads((data / "visual-pilot.json").read_bytes()))
        print(f"Verified the starting pages and {count} visual workflow outputs against their receipts.")
    if (data / "redis-sensitivity.json").is_file():
        audited = json.loads((data / "redis-sensitivity.json").read_bytes())
        expected = {f"{row['run']}/{row['task_id']}/{row['arm']}.patch": row["filtered_sha256"] for row in audited}
        if len(expected) != len(audited):
            raise ValueError("Duplicate Redis replay records.")
        with ZipFile(data / "redis-source-patches.zip") as patches:
            if sorted(patches.namelist()) != sorted(expected):
                raise ValueError("The Redis regrade patch archive has an unexpected inventory.")
            for name, digest in expected.items():
                if hashlib.sha256(patches.read(name)).hexdigest() != digest:
                    raise ValueError(f"Filtered patch does not match the Redis regrade: {name}")
        for row in audited:
            verify_redis_origin(row, by_task, peers, checkpoint)
        print(f"Verified original and filtered patch digests for {len(audited)} Redis replays.")
    score = json.loads((data / "scorecard.json").read_bytes())
    verify_assignments(data, manifest, by_task, registered)
    complete = sum(map(len, by_task.values())) == len(registered)
    compared = score["candidate_comparisons"]["spot_advisor"]
    supports_change = bool(
        manifest["stage"] == "coding" and complete and compared and compared["pass_difference_lower95"] > -.10
        and any(compared[key] is not None and compared[key] <= .90
                for key in ("total_ratio_upper95", "frontier_ratio_upper95"))
    )
    if score["complete"] is not complete or score["default_change_supported"] is not supports_change:
        raise ValueError("Reported default-policy decision disagrees with the frozen release criteria.")
    if (data / "coding-run-source.zip").is_file():
        verify_frozen_scorecard(data, score, manifest, by_task)
    else:
        for arm in LABELS:
            rows = [arms[arm] for arms in by_task.values() if arm in arms]
            attempts = [attempt for row in rows for attempt in requests(row)]
            known = [a for a in attempts if a.get("model") and request_tokens(a) is not None]
            priced = [price for attempt in known if (price := estimate(attempt)) is not None]
            actual = {"records": len(rows), "verified": sum(row["accepted"] for row in rows),
                      "verified_by_15_minutes": sum(row["accepted"] and row["wall_seconds"] <= deadline
                                                    for row in rows),
                      "requests": len(attempts), "attributed_requests": len(known), "priced_requests": len(priced),
                      "observed_total_tokens": sum(request_tokens(attempt) for attempt in known),
                      "priced_request_subtotal_usd": sum(priced)}
            expected = score["metrics"][arm]
            if any(not matches_report(value, expected[key]) for key, value in actual.items()):
                raise ValueError(f"The receipt-based scorecard disagrees for {arm}.")
    resources = matched_resource_audit(by_task, score["pricing"], deadline)
    saved = json.loads((data / "matched-resource-audit.json").read_bytes())
    if not matches_report(resources, saved):
        raise ValueError("The matched cost audit differs from the receipt-based result.")
    if rebuilt is not None:
        rebuilt.mkdir(parents=True, exist_ok=False)
        (rebuilt / "matched-resource-audit.json").write_text(json.dumps(resources, indent=2) + "\n", encoding="utf-8")
        if resources["matched_tasks"]:
            plot_matched_resources(resources, rebuilt / "matched-resources.pdf")
    print(f"Verified {sum(map(len, by_task.values()))}/{len(registered)} recorded arms and "
          f"{len(resources['matched_tasks'])} issues with fully priced usage across four workflows.")
    print(f"Recalculated paired comparisons for {len(PEERS)} registered peer workflows.")


def regrade_archived_patches(package: Path, selections: list, source: Path, output: Path) -> None:
    data = package / "data"
    manifest = json.loads((data / "manifest.json").read_bytes())
    study = json.loads((package / "analysis" / "study22-tasks.json").read_bytes())
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain"], text=True).strip()
    if revision != study["source_revision"] or dirty:
        raise ValueError("Use a clean SWE-bench checkout at the packaged source revision.")
    coding = {(row["task_id"], row["arm"]): row
              for row in map(json.loads, (data / "evidence.jsonl").read_bytes().splitlines())}
    peers = json.loads((data / "peer-audit.json").read_bytes())["peers"]
    prefixes = {}
    if any(workflow == "checkpoint-prefix" for _, workflow in selections):
        prefixes = json.loads((data / "checkpoint-audit.json").read_bytes())["summary"]["prefix_dispositions"]
    rows, patches = [], {}
    for task, workflow in selections:
        if (not re.fullmatch(r"instance_[A-Za-z0-9_.-]+", task)
                or workflow != "checkpoint-prefix" and workflow not in LABELS | PEERS):
            raise ValueError(f"Invalid regrade selection: {task}/{workflow}")
        if (task, workflow) in patches:
            raise ValueError(f"Duplicate regrade selection: {task}/{workflow}")
        if (task, workflow) in coding:
            row = coding[task, workflow]
            name, member = "coding-patches.zip", f"{task}/{workflow}.patch"
            accepted, verdict = row["accepted"], row["verdict"]
        elif workflow in peers and task in peers[workflow]["graded_rows"]:
            row = peers[workflow]["graded_rows"][task]
            name, member = "peer-artifacts.zip", f"{workflow}/{task}/{workflow}/model.patch"
            accepted, verdict = row["peer_pass"], row["peer_verdict"]
        elif workflow == "checkpoint-prefix" and task in prefixes:
            row = prefixes[task]
            name, member = "checkpoint-prefix-patches.zip", f"{task}.patch"
            accepted, verdict = row["accepted"], row["verdict"]
        else:
            raise ValueError(f"No original grade for {task}/{workflow}.")
        with ZipFile(data / name) as archive:
            patch = archive.read(member)
        digest = hashlib.sha256(patch).hexdigest()
        if digest != row["workspace_sha256" if workflow == "checkpoint-prefix" else "artifact_sha256"]:
            raise ValueError(f"Artifact digest differs for {task}/{workflow}.")
        patches[task, workflow] = patch
        rows.append({"task_id": task, "workflow": workflow, "original_accepted": accepted,
                     "original_verdict": verdict, "artifact_sha256": digest, "upstream_revision": revision})
    frozen = data / "coding-run-source.zip"
    verify_coding_source(frozen, manifest)
    output.mkdir(parents=True, exist_ok=False)
    for (task, workflow), patch in patches.items():
        folder = output / task / workflow
        folder.mkdir(parents=True)
        (folder / "model.patch").write_bytes(patch)
    with TemporaryDirectory(prefix="spot-advisor-grader-") as work, ZipFile(frozen) as archive:
        for name in SCORING_IMPORTS:
            archive.extract(name, work)
        subprocess.run([sys.executable, "-B", "-c", REPLAY_GRADES, str(source), str(output)],
                       cwd=Path(work) / "eval", input=json.dumps(rows), text=True, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--rebuild", type=Path, help="New directory for the regenerated figure and audit.")
    parser.add_argument("--regrade", nargs=2, metavar=("TASK_ID", "WORKFLOW"), action="append")
    parser.add_argument("--swe-source", type=Path, help="Clean pinned upstream SWE-bench checkout.")
    parser.add_argument("--regrade-output", type=Path, help="New directory for replayed grades.")
    args = parser.parse_args()
    if (args.regrade or args.swe_source or args.regrade_output) and not all(
            (args.regrade, args.swe_source, args.regrade_output)):
        parser.error("--regrade requires both --swe-source and --regrade-output.")
    verify(args.package, args.rebuild)
    if args.regrade:
        regrade_archived_patches(args.package.resolve(), args.regrade, args.swe_source.resolve(),
                                 args.regrade_output.resolve())
