"""Run either product against the same independent local diagnostic grader."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from urllib.error import HTTPError, URLError

from claude_advisor_probe import send_native
from claude_native_session import run_native
from direct_session import DirectSession, ExecutorError
from run import ADVISOR, LUNA, ROOT, TOOLS, TaskExecution, dump, ensure_image, grade, prepare_inputs
from sandbox import Sandbox
from suites import diagnostic_tasks, load_swe_instances


def main() -> None:
    cases = {task.id: task for task in diagnostic_tasks()}
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, help="Diagnostic ID or a frozen 2.2 peer issue with --public.")
    parser.add_argument("--public", action="store_true", help="Use the pinned 2.2 SWE-bench peer subset.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--arm", choices=("claude_native", "spot_advisor"), default="claude_native")
    args = parser.parse_args()
    key = os.environ.get("ANTHROPIC_API_KEY")
    if args.arm == "claude_native" and not key:
        parser.error("ANTHROPIC_API_KEY is required for native Advisor.")
    folder = args.output.resolve()
    if Path(__file__).resolve().parent / "runs" not in folder.parents:
        parser.error("Use an output directory under eval/runs/.")
    if args.public:
        peers = json.loads((ROOT / "eval/study22-tasks.json").read_bytes())["peer_tasks"]
        if args.case not in peers:
            parser.error("The issue is outside the frozen 2.2 peer subset.")
        task, = load_swe_instances(ROOT / "eval/runs/swe-bench-pro-source", [args.case])
        ensure_image(task)
    elif args.case in cases:
        task = cases[args.case]
    else:
        parser.error("Unknown diagnostic case.")
    tool_limit = 100 if args.public else 40
    seconds = task.timeout if args.public else 240
    frozen = folder / "advisor-frozen.py"
    advisor_digest = None
    if args.arm == "spot_advisor":
        source = ADVISOR.read_bytes()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            with frozen.open("xb") as destination:
                destination.write(source)
        except FileExistsError:
            if frozen.read_bytes() != source:
                parser.error("This run uses a different frozen Spot Advisor revision. Choose a new output directory.")
        advisor_digest = hashlib.sha256(source).hexdigest()
    folder = folder / task.id / args.arm
    folder.mkdir(parents=True, exist_ok=False)
    verified_task = task if task.upstream else replace(task, grader=task.inputs["check.py"] + "\n" + task.grader)
    dump(folder / "manifest.json", {
        "task": task.id, "arm": args.arm, "image": task.image,
        "tool_limit": tool_limit, "executor_seconds": seconds,
        "inputs_sha256": hashlib.sha256(json.dumps(
            [task.goal, task.constraints, task.acceptance, task.inputs], sort_keys=True).encode()).hexdigest(),
        "grader_sha256": hashlib.sha256(verified_task.grader.encode()).hexdigest() if not task.upstream else None,
        "upstream_revision": subprocess.check_output(
            ["git", "-C", str(task.upstream.parents[2]), "rev-parse", "HEAD"], text=True).strip()
        if task.upstream else None,
        "spot_advisor_sha256": advisor_digest,
    })
    work = prepare_inputs(folder, task)
    started = time.monotonic()
    with Sandbox(task.image) as sandbox, (folder / "receipts.jsonl").open("x", encoding="utf-8") as journal:
        sandbox.initial(task, work)
        execution = TaskExecution(task, args.arm, sandbox, folder, time.monotonic() + seconds,
                                  tool_limit=tool_limit)

        def record(receipt):
            journal.write(json.dumps(receipt) + "\n")
            journal.flush()

        prompt = (
            f"{task.goal}\nConstraints: {task.constraints}\nAcceptance: {task.acceptance}\n"
            f"First run verify_checkpoint with {task.public_check!r}, then repair and verify."
        )
        try:
            if args.arm == "claude_native":
                turn = run_native(lambda payload, timeout: send_native(key, payload, timeout), prompt, TOOLS,
                                  execution.handle, execution.remaining, on_receipt=record)
            else:
                session = DirectSession(folder / "executor-attempts.jsonl")
                turn = session.run(LUNA, "max", prompt, TOOLS, execution.handle, execution.remaining(seconds))
        except HTTPError as exc:
            turn = {"status": "anthropic_http_error", "code": exc.code}
        except (URLError, TimeoutError, ExecutorError, subprocess.TimeoutExpired):
            turn = {"status": "executor_interrupted"}
        patch = sandbox.patch() if task.upstream else sandbox.archive()
    (folder / ("model.patch" if task.upstream else "workspace.tar")).write_bytes(patch)
    accepted, verdict = grade(verified_task, work, patch, folder)
    dump(folder / "result.json", {"task": task.id, "arm": args.arm, "accepted": accepted,
                                  "verdict": verdict, "turn": turn, "consultations": execution.consultations,
                                  "wall_seconds": round(time.monotonic() - started, 3)})


if __name__ == "__main__":
    main()
