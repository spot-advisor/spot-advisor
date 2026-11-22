"""Regrade an observed peer workflow from the frozen 2.2 subset."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

from run import ROOT, dump, ensure_image, grade
from suites import load_swe_instances


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--peer", choices=("second-opinion", "pal-mcp"), required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--patch", type=Path, required=True, help="Final binary Git diff from the executor.")
    parser.add_argument("--trace", type=Path, required=True,
                        help="JSON with workflow_seconds, product_revision, and real receipts, or null usage.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = json.loads((ROOT / "eval/study22-tasks.json").read_bytes())
    if args.case not in manifest["peer_tasks"]:
        parser.error("Choose an issue from the frozen peer subset.")
    output = args.output.resolve()
    if ROOT / "eval/runs" not in output.parents:
        parser.error("Use a named output under eval/runs/.")
    trace = json.loads(args.trace.read_bytes())
    duration = trace.get("workflow_seconds")
    revision = trace.get("product_revision")
    if (type(duration) not in (float, int) or not math.isfinite(duration) or duration <= 0
            or not isinstance(revision, str) or not revision.strip()
            or "receipts" not in trace or trace["receipts"] is not None and not isinstance(trace["receipts"], list)):
        parser.error("Include observed workflow_seconds, product_revision, and receipts (null when unavailable).")
    task, = load_swe_instances(ROOT / "eval/runs/swe-bench-pro-source", [args.case])
    patch = args.patch.read_bytes()
    target = output / args.case / args.peer
    target.mkdir(parents=True, exist_ok=False)
    (target / "model.patch").write_bytes(patch)
    (target / "workflow-trace.json").write_bytes(args.trace.read_bytes())
    ensure_image(task)
    started = time.monotonic()
    accepted, verdict = grade(task, target, patch, target)
    grader_seconds = time.monotonic() - started
    dump(target / "result.json", {
        "peer": args.peer, "task": args.case, "product_revision": revision, "accepted": accepted,
        "verdict": verdict, "wall_seconds": duration + grader_seconds, "grading_seconds": grader_seconds,
        "image": task.image, "upstream_revision": manifest["source_revision"],
        "artifact_sha256": hashlib.sha256(patch).hexdigest(),
        "receipt_count": len(trace["receipts"]) if trace["receipts"] is not None else None,
    })
    print(f"{args.peer}: {verdict} ({duration + grader_seconds:.1f} s including independent grading)")


if __name__ == "__main__":
    main()
