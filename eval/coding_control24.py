"""Matched local-only versus optional Spot Advisor on three real file repairs."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import time

from diagnostic_cases import diagnostic_tasks
from gap_study24 import ASTRA, LUNA, SKILL, cli, digest
from direct_session import DirectSession
from run import dump
from sandbox import Sandbox, checked
from suites import Task
from trigger_pilot23 import tool


def files(folder, task):
    work = folder / "work"
    work.mkdir(parents=True)
    for filename, source in task.inputs.items():
        (work / filename).write_text(source, encoding="utf-8")
    return work


def check(box, cmd):
    p = box.exec(cmd, timeout=6)
    return {"passed": p.returncode == 0, "output": (p.stdout + p.stderr).decode("utf-8", "replace")[-1600:]}


def repair(folder, task, arm):
    work = files(folder, task)
    core = work / "core.py"
    session = DirectSession(folder / "executor.jsonl")
    tools = [tool("inspect", "Read current source and the public check.", ()),
             tool("write", "Replace core.py with the supplied Python module.", ("source",)),
             tool("verify", "Run the public check on the current files.", ())]
    prompt = task.goal + "\n" + " ".join(task.constraints) + "\nMake the repair and verify the result."
    if arm == "optional":
        tools.append(tool("consult", "Ask the reviewer after checking locally.", ("question", "evidence")))
        prompt += "\n\n" + SKILL.read_text(encoding="utf-8")
    events = []
    verified = None
    attempts = 0
    dispatches = 0
    observed = ""
    observed_revision = None
    error = None
    with Sandbox(task.image) as box:
        box.initial(task, work)
        baseline = check(box, task.public_check)
        if baseline["passed"]:
            raise ValueError("Pristine fixture passed its failing check.")

        def handle(event):
            nonlocal verified, attempts, dispatches, observed, observed_revision
            name, args = event["tool"], event["arguments"]
            if name == "inspect":
                result = {"source": core.read_text(encoding="utf-8"), "public": task.inputs["check.py"]}
            elif name == "write":
                core.write_text(args["source"], encoding="utf-8")
                box.copy(core, "/work/core.py")
                verified = observed_revision = None
                result = {"revision": digest(core)}
            elif name == "verify":
                result = check(box, task.public_check)
                observed_revision = digest(core)
                observed = (result["output"] or "Public check passed.")[-600:]
                verified = observed_revision if result["passed"] else None
            elif name == "consult" and arm == "optional":
                attempts += 1
                if attempts > 1 or observed_revision != digest(core):
                    result = {"status": "unavailable", "reason": "Requires current check and unused consultation."}
                else:
                    (folder / "brief.txt").write_text(task.goal, encoding="utf-8")
                    dump(folder / "state.json", {"task_id": folder.name, "revision": digest(core),
                         "goal": task.goal, "phase": "decision", "constraints": list(task.constraints),
                         "acceptance": [task.acceptance], "evidence": [observed], "intent_sources": {"brief": "brief.txt"}})
                    dump(folder / "handoff.json", {"revision": digest(core), "phase": "decision",
                         "quotes": [{"source": "brief", "first": 1, "last": 1}], "checks": [observed],
                         "evidence": [args["evidence"]]})
                    reply = cli(folder, args["question"])
                    dump(folder / "advice.json", reply)
                    dispatches += sum(bool(a.get("dispatch_possible")) for a in reply.get("attempts", []))
                    result = {"status": reply.get("status"), "advice": reply.get("advice") if reply.get("status") == "ok" else None}
            else:
                result = {"error": "Unknown tool."}
            events.append({"name": name, "result": result})
            return result

        try:
            completion = session.continue_run(LUNA, "max", [{"role": "user", "content": prompt}],
                                              tools, handle, 150, stop_after_batch=lambda: len(session.attempts) >= 8)
            if completion["status"] != "completed":
                error = "executor_response_limit"
        except (OSError, RuntimeError, ValueError) as exc:
            error = str(exc)
    dump(folder / "events.json", events)
    fresh = files(folder / "independent", task)
    (fresh / "core.py").write_bytes(core.read_bytes())
    (fresh / "independent.py").write_text(task.grader, encoding="utf-8")
    with Sandbox(task.image) as box:
        box.initial(task, fresh)
        public = check(box, task.public_check)["passed"]
        held = check(box, "python -B independent.py")["passed"]
    return {"error": error, "public_pass": public, "heldout_pass": held, "executor_verified": verified == digest(core),
            "joint": public and held and verified == digest(core) and error is None, "patch_sha256": digest(core),
            "consult_attempts": attempts, "dispatches": dispatches, "executor_requests": len(session.attempts),
            "executor_usage": session.usage()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    out = parser.parse_args().output.resolve()
    out.mkdir(parents=True)
    tasks = {t.id: t for t in diagnostic_tasks(Task) if t.id in {
        "diagnostic-expiry-boundary", "diagnostic-overlapping-ranges", "diagnostic-nested-config-copy"}}
    assert len(tasks) == 3
    rng = random.Random(2403)
    blocks = [(key, repeat) for key in tasks for repeat in (1, 2)]
    rng.shuffle(blocks)
    jobs = []
    for key, repeat in blocks:
        arms = ["local", "optional"]
        rng.shuffle(arms)
        jobs.extend({"case": key, "repeat": repeat, "arm": arm, "id": f"r{len(jobs):03}"} for arm in arms)
    dump(out / "manifest.json", {"seed": 2403, "assignments": jobs, "image": next(iter(tasks.values())).image,
         "model": LUNA, "advisor": ASTRA, "grader_source_sha256": digest(Path(__file__).with_name("diagnostic_cases.py")),
         "harness_sha256": digest(Path(__file__)), "protocol_sha256": digest(SKILL.parents[2] / "docs/spot-advisor-2.4-coding-control.md")})

    def run(job):
        start = time.monotonic()
        try:
            result = repair(out / job["id"], tasks[job["case"]], job["arm"])
        except Exception as exc:
            result = {"error": type(exc).__name__ + ": " + str(exc), "joint": False}
        row = {**job, **result, "seconds": round(time.monotonic() - start, 3)}
        dump(out / job["id"] / "outcome.json", row)
        return row

    smoke = run({"case": "diagnostic-expiry-boundary", "arm": "local", "repeat": 0, "id": "smoke"})
    if smoke.get("error") or not smoke["heldout_pass"]:
        raise SystemExit("File repair smoke failed; inspect smoke/outcome.json.")
    with ThreadPoolExecutor(max_workers=2) as pool:
        dump(out / "outcomes.json", list(pool.map(run, jobs)))
