"""Exercise three live continuations on benign local diagnostic tasks."""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import subprocess
import time
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

from checkpoint import Checkpoint, repository_snapshot
from direct_session import DirectSession, ExecutorError
from run import ADVISOR, ASTRA, LUNA, TOOLS, TaskExecution, dump, ensure_image, grade, prepare_inputs
from sandbox import Sandbox, SandboxError
from suites import diagnostic_tasks, load_swe_instances, pilot_diagnostics

TOOL_ALLOWANCE = 40
EXECUTOR_SECONDS = 120


def run_case(task, folder: Path, seed: int, *, natural_trigger: bool, prefix_seconds: int,
             continuation_seconds: int, compare_questions: bool = False,
             require_behavioral_check: bool = False) -> None:
    with ExitStack() as snapshots:
        run_continuations(task, folder, seed, natural_trigger=natural_trigger, snapshots=snapshots,
                          prefix_seconds=prefix_seconds, continuation_seconds=continuation_seconds,
                          compare_questions=compare_questions, require_behavioral_check=require_behavioral_check)


def run_continuations(task, folder: Path, seed: int, *, natural_trigger: bool, snapshots: ExitStack,
                      prefix_seconds: int, continuation_seconds: int, compare_questions: bool,
                      require_behavioral_check: bool) -> None:
    folder.mkdir()
    work = prepare_inputs(folder, task)
    verified_task = task if task.upstream else replace(task, grader=task.inputs["check.py"] + "\n" + task.grader)
    artifact_name = "model.patch" if task.upstream else "workspace.tar"
    prefix = DirectSession(folder / "prefix.jsonl")
    with Sandbox(task.image) as sandbox:
        sandbox.initial(task, work)
        execution = TaskExecution(task, "luna_only", sandbox, folder,
                                  time.monotonic() + prefix_seconds, tool_limit=TOOL_ALLOWANCE)
        prompt = (
            f"{task.goal}\nConstraints: {task.constraints}\nAcceptance: {task.acceptance}\n"
            f"First run verify_checkpoint with {task.public_check!r}, then repair and verify."
        )
        if require_behavioral_check:
            prompt += (
                " Use verify_checkpoint for a focused behavior test or a local reproduction of the task's "
                "acceptance condition. Check named dependencies and fixtures first. If the same behavior "
                "still fails after a repair attempt, rerun that focused command. Use run_command to inspect "
                "files and environment. A diff check only verifies patch formatting."
            )
        prefix_checks = []
        last_verify_executed = False

        def handle_prefix(call):
            nonlocal last_verify_executed
            previous_count = len(execution.checkpoints)
            answer = execution.handle(call)
            if call["tool"] == "verify_checkpoint":
                last_verify_executed = len(execution.checkpoints) > previous_count
            if call["tool"] == "verify_checkpoint" and last_verify_executed:
                command = call["arguments"].get("command") or task.public_check
                prefix_checks.append({"command": command.strip(), **execution.checkpoints[-1]})
            return answer

        def checkpoint_ready():
            if not prefix_checks:
                return False
            latest = prefix_checks[-1]
            eligible = bool(latest["trigger"]) if natural_trigger else not latest["passed"]
            if not eligible or not require_behavioral_check:
                return eligible
            return (last_verify_executed and latest["command"] != task.public_check
                    and latest["revision"] == execution.revision())

        try:
            turn = prefix.continue_run(
                LUNA, "max", [{"role": "user", "content": prompt}], TOOLS, handle_prefix, prefix_seconds,
                stop_after_batch=checkpoint_ready,
            )
        except (TimeoutError, ExecutorError, subprocess.TimeoutExpired) as exc:
            turn = {"status": "prefix_failed", "error": str(exc)}
        dump(folder / "prefix-checks.json", prefix_checks)
        if turn["status"] != "checkpoint" or execution.tools == TOOL_ALLOWANCE:
            workspace = sandbox.patch() if task.upstream else sandbox.archive()
            (folder / artifact_name).write_bytes(workspace)
            accepted, verdict = grade(verified_task, work, workspace, folder)
            dump(folder / "result.json", {
                "status": "prefix_failed" if turn["status"] == "prefix_failed" else "no_checkpoint",
                "prefix_usage": prefix.usage(),
                "reason": "tool_allowance_exhausted" if execution.tools == TOOL_ALLOWANCE else "no_eligible_trigger",
                "error": turn.get("error"),
                "accepted": accepted, "verdict": verdict,
                "workspace_sha256": hashlib.sha256(workspace).hexdigest(),
            })
            return
        trigger = execution.checkpoints[-1]["trigger"] if natural_trigger else "manual"
        remaining_tools = TOOL_ALLOWANCE - execution.tools
        if task.upstream:
            checkpoint_image = snapshots.enter_context(repository_snapshot(sandbox))
            base_commit = sandbox.base_commit
            checkpoint = None
        else:
            checkpoint_image = task.image
            checkpoint = Checkpoint.capture(sandbox, turn, remaining_tools)
    if checkpoint is not None:
        (folder / "checkpoint-workspace.tar").write_bytes(checkpoint.workspace)
    message_path = folder / "checkpoint-messages.json"
    dump(message_path, turn["messages"])
    order = ["luna_only", "luna_advice", "astra_takeover"]
    if compare_questions:
        order.append("luna_generic_question")
    random.Random(seed).shuffle(order)
    dump(folder / "checkpoint-summary.json", {
        "task": task.id, "order": order,
        "workspace_sha256": hashlib.sha256(checkpoint.workspace).hexdigest() if checkpoint else None,
        "checkpoint_image": checkpoint_image if task.upstream else None,
        "messages_sha256": hashlib.sha256(message_path.read_bytes()).hexdigest(),
        "grader_sha256": hashlib.sha256(verified_task.grader.encode()).hexdigest() if not task.upstream else None,
        "remaining_tools": remaining_tools, "prefix_usage": prefix.usage(),
        "trigger": trigger,
    })
    for arm in order:
        started = time.monotonic()
        output = folder / arm
        output.mkdir()
        session = DirectSession(output / "attempts.jsonl")
        with Sandbox(checkpoint_image) as sandbox:
            if checkpoint is None:
                sandbox.initial(task, work)
                sandbox.base_commit = base_commit
                messages = deepcopy(turn["messages"])
            else:
                messages = checkpoint.restore(sandbox)
            execution = TaskExecution(task, arm, sandbox, output, time.monotonic() + 60,
                                      tool_limit=remaining_tools)
            if arm in {"luna_advice", "luna_generic_question"}:
                checks = {item["call_id"] for item in messages
                          if item.get("type") == "function_call" and item.get("name") == "verify_checkpoint"}
                evidence = next(
                    item["output"] for item in reversed(messages)
                    if item.get("type") == "function_call_output" and item["call_id"] in checks
                )
                if compare_questions:
                    question = ("What should I do?" if arm == "luna_generic_question" else
                                "Which next repair is best supported by the failed check, and which single "
                                "local assertion would distinguish it from the strongest alternative?")
                    advice = execution.consult(trigger, evidence, execution.revision(), question=question)
                else:
                    advice = execution.consult(trigger, evidence, execution.revision())
                if advice.strip():
                    messages.append({"role": "user", "content": advice})
            if arm == "astra_takeover":
                # Astra receives visible evidence, not Luna's model-specific reasoning items.
                messages = [item for item in messages if item.get("type") != "reasoning"]
            model, effort = (ASTRA, "medium") if arm == "astra_takeover" else (LUNA, "max")
            execution.deadline = (started if compare_questions else time.monotonic()) + continuation_seconds
            try:
                outcome = session.continue_run(model, effort, messages, TOOLS, execution.handle,
                                               execution.remaining(continuation_seconds))
            except (TimeoutError, ExecutorError, subprocess.TimeoutExpired) as exc:
                outcome = {"status": "executor_failed", "error": str(exc)}
            patch = sandbox.patch() if task.upstream else sandbox.archive()
        (output / artifact_name).write_bytes(patch)
        verification_error = None
        try:
            accepted, verdict = grade(verified_task, work, patch, output)
        except (SandboxError, subprocess.SubprocessError) as exc:
            accepted, verdict = None, "verifier_infrastructure_error"
            verification_error = str(exc)
        dump(output / "result.json", {
            "arm": arm, "accepted": accepted, "verdict": verdict, "turn": outcome,
            "verification_error": verification_error,
            "usage": session.usage(), "consultations": execution.consultations,
            "executor_tools": min(execution.tools, execution.tool_limit),
            "wall_seconds": round(time.monotonic() - started, 3),
            "workspace_sha256": hashlib.sha256(patch).hexdigest(),
        })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    pilots = pilot_diagnostics()
    cases = {task.id: task for task in [*pilots, *diagnostic_tasks()]}
    parser.add_argument("--output", type=Path, required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--limit", type=int, choices=range(1, len(pilots) + 1))
    selection.add_argument("--case", choices=cases, action="append")
    selection.add_argument("--study22", action="store_true",
                           help="Screen the frozen 2.2 cohort in order for up to 12 comparison checkpoints.")
    selection.add_argument("--public-qutebrowser", dest="public_case", action="store_const",
                           const="qutebrowser", help="Use the screened color-helper refactor.")
    selection.add_argument("--public-openlibrary", dest="public_case", action="store_const",
                           const="openlibrary", help="Use the screened ISBNdb import helper task.")
    selection.add_argument("--qutebrowser-process-replay", dest="public_case", action="store_const",
                           const="process_replay", help="Replay the 2.0 process cleanup regression case.")
    parser.add_argument("--natural-trigger", action="store_true",
                        help="Fork only on the skill's repeated-failure or conflicting-evidence trigger.")
    parser.add_argument("--compare-questions", action="store_true",
                        help="Add the generic-question arm when qualifying a local checkpoint.")
    args = parser.parse_args()
    folder = args.output.resolve()
    private_runs = Path(__file__).resolve().parent / "runs"
    if private_runs not in folder.parents:
        parser.error("Use an output directory under eval/runs/ for model transcripts and workspaces.")
    prefix_seconds = continuation_seconds = EXECUTOR_SECONDS
    if args.study22:
        manifest = json.loads((Path(__file__).with_name("checkpoint_tasks.json")).read_bytes())
        registered = json.loads((Path(__file__).with_name("study22-tasks.json")).read_bytes())
        prefix_seconds, continuation_seconds = manifest["prefix_seconds"], manifest["continuation_seconds"]
        upstream = private_runs / "swe-bench-pro-source"
        revision = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
        changes = subprocess.check_output(["git", "-C", str(upstream), "status", "--porcelain"], text=True).strip()
        if revision != registered["source_revision"] or changes:
            parser.error("The public checkpoint run requires the clean, pinned upstream verifier checkout.")
        selected = load_swe_instances(upstream, registered["tasks"])
        args.natural_trigger = True
        args.compare_questions = True
    elif args.public_case:
        manifest = json.loads((Path(__file__).with_name("checkpoint_tasks.json")).read_text(encoding="utf-8"))
        prefix_seconds = manifest["prefix_seconds"]
        continuation_seconds = manifest["continuation_seconds"]
        screened = {
            "qutebrowser": (manifest["tasks"][0], "Consolidate color interpolation into `qtutils`",
                            "QT_QPA_PLATFORM=offscreen python -c 'from qutebrowser.utils.qtutils import interpolate_color'"),
            "openlibrary": (manifest["tasks"][2], "Add ISBNdb line parsing and non-book format detection",
                            "python -c 'import runpy; m = runpy.run_path(\"scripts/providers/isbndb.py\"); "
                            "assert all(name in m for name in (\"get_line\", \"is_nonbook\", \"NONBOOK\"))'"),
            "process_replay": (
                "instance_qutebrowser__qutebrowser-c09e1439f145c66ee3af574386e277dd2388d094-v2ef375ac784985212b1805e1d0431dc8f1b3c171",
                "Successful process data is retained indefinitely",
                "QT_QPA_PLATFORM=offscreen python -c 'from PyQt5.QtCore import QCoreApplication; "
                "from qutebrowser.misc.guiprocess import GUIProcess; app = QCoreApplication([]); "
                "assert GUIProcess(\"cleanup-check\")._cleanup_timer.interval() == 3600000'",
            ),
        }
        instance, title, public_check = screened[args.public_case]
        task, = load_swe_instances(private_runs / "swe-bench-pro-source", [instance])
        if title not in task.goal:
            parser.error("The public instance differs from the screened task.")
        selected = [replace(task, public_check=public_check)]
    else:
        selected = [cases[case] for case in dict.fromkeys(args.case)] if args.case else pilots[:args.limit]
    folder.mkdir(parents=True)
    shutil.copyfile(ADVISOR, folder / "advisor-frozen.py")
    dump(folder / "pilot-plan.json", {
        "cases": [task.id for task in selected],
        "seed": 20261121,
        "tool_allowance": TOOL_ALLOWANCE,
        "prefix_seconds": prefix_seconds,
        "continuation_seconds": continuation_seconds,
        "purpose": "Exploratory 2.2 maintenance continuations." if args.study22 else {
            "process_replay": "Regression replay of a 2.0 consultation case.",
            None: "Diagnostic qualification.",
        }.get(args.public_case, "Public repository qualification."),
        "natural_trigger": args.natural_trigger,
        "compare_questions": args.compare_questions,
        "eligible_checkpoint_cap": 12 if args.study22 else None,
        "require_behavioral_check": args.study22,
        "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "source_revision": registered["source_revision"] if args.study22 else None,
    })
    checkpoints = 0
    for index, task in enumerate(selected):
        if args.study22:
            if checkpoints == 12:
                break
            ensure_image(task)
        run_case(task, folder / task.id, 20261121 + index, natural_trigger=args.natural_trigger,
                 prefix_seconds=prefix_seconds, continuation_seconds=continuation_seconds,
                 compare_questions=args.compare_questions, require_behavioral_check=args.study22)
        checkpoints += int((folder / task.id / "checkpoint-summary.json").is_file())


if __name__ == "__main__":
    main()
