"""Run the Codex-to-Claude second-opinion workflow on a frozen repair issue."""

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import tarfile
import tempfile
import time
from pathlib import Path

from direct_session import DirectSession, ExecutorError
from run import LUNA, ROOT, TOOLS, TaskExecution, dump, ensure_image, grade
from sandbox import Sandbox
from suites import load_swe_instances

PEER_REVISION = "d73fea96a33821bfc75e097d832eeb421057545c"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--peer-source", type=Path, required=True)
    args = parser.parse_args()
    selected = json.loads((ROOT / "eval/study22-tasks.json").read_bytes())
    output = args.output.resolve()
    if args.case not in selected["peer_tasks"] or ROOT / "eval/runs" not in output.parents:
        parser.error("Use a frozen peer case and an output under eval/runs.")
    revision = subprocess.check_output(["git", "-C", str(args.peer_source), "rev-parse", "HEAD"],
                                       text=True).strip()
    claude = shutil.which("claude")
    if revision != PEER_REVISION or claude is None:
        parser.error("Use the frozen second-opinion checkout and an installed Claude CLI.")
    task, = load_swe_instances(ROOT / "eval/runs/swe-bench-pro-source", [args.case])
    ensure_image(task)
    folder = output / task.id / "second-opinion"
    folder.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    deadline = started + task.timeout
    with Sandbox(task.image) as workspace:
        workspace.initial(task, folder)
        archive = workspace.exec("git archive --format=tar HEAD", timeout=90)
        archive.check_returncode()
        with tempfile.TemporaryDirectory(prefix="sa22-") as review:
            with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
                tar.extractall(review, filter="data")
            brief = (
                "This is a code review request. Independently inspect the public worktree for the task below. "
                "Use only local read tools. Do not invoke any other agent or workflow. Return file and line "
                "references, the highest-value next action and verification steps. Do not edit files.\n\n"
                f"{task.goal}\nConstraints: {task.constraints}\nAcceptance: {task.acceptance}"
            )
            # Codex is the coordinating executor, so the peer skill selects Claude as its reviewer.
            review_cmd = [claude, "-p", "--no-session-persistence", "--output-format", "json",
                          "--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob",
                          "--disable-slash-commands", "--strict-mcp-config"]
            done = subprocess.run(review_cmd, cwd=review, input=brief, text=True, encoding="utf-8",
                                  capture_output=True, timeout=min(600, max(1, deadline-time.monotonic())))
        if done.returncode:
            dump(folder / "attempt.json", {"status": "review_failed", "returncode": done.returncode})
            return 1
        body = json.loads(done.stdout)
        dump(folder / "review-receipt.json", {k: body.get(k) for k in
             ("usage", "modelUsage", "duration_ms", "num_turns", "total_cost_usd", "is_error")})
        if body.get("is_error") or not body.get("result"):
            return 1
        execution = TaskExecution(task, "luna_only", workspace, folder, deadline)
        session = DirectSession(folder / "executor-attempts.jsonl")
        prompt = (
            "Apply the relevant findings from an independent blind review. Verify each against the workspace "
            "and report any disagreement. Submit a patch that meets the task acceptance criteria.\n\n"
            f"Task: {task.goal}\nConstraints: {task.constraints}\nAcceptance: {task.acceptance}\n\n"
            f"Independent review:\n{body['result']}"
        )
        try:
            turn = session.run(LUNA, "max", prompt, TOOLS, execution.handle, execution.remaining(task.timeout))
        except (TimeoutError, ExecutorError, subprocess.TimeoutExpired):
            turn = {"status": "executor_interrupted"}
        patch = workspace.patch()
    (folder / "model.patch").write_bytes(patch)
    accepted, verdict = grade(task, folder, patch, folder)
    dump(folder / "result.json", {
        "task": task.id, "peer": "second-opinion", "product_revision": revision,
        "workflow": "blind read-only Claude review followed by common Luna executor reconciliation",
        "baseline": "adapted repair workflow", "executor_usage": session.usage(),
        "accepted": accepted, "verdict": verdict, "turn_status": turn["status"],
        "wall_seconds": round(time.monotonic() - started, 3),
        "artifact_sha256": hashlib.sha256(patch).hexdigest(),
    })
    print(f"second-opinion / {task.id}: {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
