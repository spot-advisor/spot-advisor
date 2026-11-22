"""Regrade observed NodeBB patch-application failures caused by Redis runtime files."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path

from run import ROOT, dump, grade
from sandbox import Sandbox, SandboxError, checked, docker
from suites import load_swe_instances

RUNTIME = {
    "appendonlydir/appendonly.aof.1.base.rdb",
    "appendonlydir/appendonly.aof.1.incr.aof",
    "appendonlydir/appendonly.aof.manifest",
}


def replay(run: Path, record_path: Path, output: Path) -> None:
    row = json.loads(record_path.read_bytes())
    prefix = record_path.parent.parent == run
    if prefix:
        case, arm = record_path.parent.name, "checkpoint-prefix"
        recorded_hash = row["workspace_sha256"]
    else:
        case, arm = record_path.parent.parent.name, record_path.parent.name
        if (row.get("task_id", row.get("task")), row.get("arm", row.get("peer"))) != (case, arm):
            raise ValueError(f"Unexpected case or arm: {record_path}")
        recorded_hash = row["artifact_sha256"]
    patch = (record_path.parent / "model.patch").read_bytes()
    original_hash = hashlib.sha256(patch).hexdigest()
    if recorded_hash != original_hash:
        raise ValueError(f"Artifact differs from recorded identity: {record_path}")
    target = output / run.name / case / arm
    if target.is_dir():
        receipt = target / "sensitivity.json"
        if not receipt.is_file():
            raise ValueError(f"Replay has an incomplete output directory: {target}")
        saved = json.loads(receipt.read_bytes())
        if (saved["run"], saved["task_id"], saved["arm"], saved["original_sha256"]) != (
                run.name, case, arm, original_hash):
            raise ValueError(f"Existing replay differs from current assignment: {receipt}")
        if saved["status"] == "graded":
            restaged = (target / "without-redis-runtime.patch").read_bytes()
            if hashlib.sha256(restaged).hexdigest() != saved["filtered_sha256"]:
                raise ValueError(f"Existing filtered patch changed: {target}")
        print(f"{run.name} / {case} / {arm}: preserved completed replay", flush=True)
        return
    task, = load_swe_instances(ROOT / "eval/runs/swe-bench-pro-source", [case])
    target.mkdir(parents=True, exist_ok=False)
    image_id = json.loads(checked(docker("image", "inspect", task.image)))[0]["Id"]
    result = {"run": run.name, "task_id": case, "arm": arm, "original_accepted": False,
              "original_sha256": original_hash, "local_image_id": image_id}
    with Sandbox(task.image) as verifier:
        verifier.initial(task, target)
        original = verifier.exec("git apply --check --binary -", data=patch)
        errors = [line for line in original.stderr.decode(errors="replace").splitlines() if line.startswith("error:")]
        collisions = [re.fullmatch(r"error: (appendonlydir/[^:]+): already exists in working directory", line)
                      for line in errors]
        result.update(original_application_exit=original.returncode, application_errors=errors)
        if (not original.returncode or not collisions or not all(collisions)
                or not {item[1] for item in collisions} <= RUNTIME):
            dump(target / "sensitivity.json", {**result, "status": "no_redis_only_collision_reproduced"})
            return
        if checked(verifier.exec("git ls-files -- appendonlydir")):
            raise ValueError("The Redis directory contains tracked files in the base image.")
        offsets = [match.start() for match in re.finditer(rb"^diff --git ", patch, re.M)]
        if not offsets or offsets[0] != 0:
            raise ValueError("Expected a captured Git binary diff.")
        targets = {f"diff --git a/{path} b/{path}".encode(): path for path in RUNTIME}
        sections = [patch[start:end] for start, end in zip(offsets, [*offsets[1:], len(patch)])]
        dropped = {targets[header] for section in sections
                   if (header := section.split(b"\n", 1)[0]) in targets}
        if dropped != {item[1] for item in collisions}:
            raise ValueError("Patch sections do not match the reproduced Redis collisions.")
        normalized = b"".join(section for section in sections if section.split(b"\n", 1)[0] not in targets)
        checked(verifier.exec("git apply --check --binary -", data=normalized))
        result.update(excluded_runtime_paths=sorted({item[1] for item in collisions}),
                      filtered_sha256=hashlib.sha256(normalized).hexdigest())
    (target / "without-redis-runtime.patch").write_bytes(normalized)
    try:
        accepted, verdict = grade(task, target, normalized, target)
    except (SandboxError, subprocess.TimeoutExpired) as exc:
        dump(target / "sensitivity.json", {**result, "status": "grader_error", "error_type": type(exc).__name__})
        return
    dump(target / "sensitivity.json", {**result, "status": "graded", "accepted": accepted, "verdict": verdict})
    print(f"{run.name} / {case} / {arm}: {verdict} after filtering generated Redis files", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="append", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT / "eval/runs" not in output.parents:
        parser.error("Use an output directory under eval/runs.")
    for run in map(Path.resolve, args.run):
        if ROOT / "eval/runs" not in run.parents or not run.is_dir() or run == output:
            parser.error("Use an existing recorded run under eval/runs, separate from the regrade output.")
        pattern = "instance_NodeBB__*/result.json" if (run / "pilot-plan.json").is_file() else "instance_NodeBB__*/**/result.json"
        for record_path in sorted(run.glob(pattern)):
            row = json.loads(record_path.read_bytes())
            if row.get("accepted") is False and row.get("verdict") == "patch_rejected":
                replay(run, record_path, output)


if __name__ == "__main__":
    main()
