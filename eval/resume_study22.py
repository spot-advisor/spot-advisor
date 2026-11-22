"""Continue an interrupted 2.2 run without resending an ambiguous attempt."""

from __future__ import annotations

import argparse
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import study22
from report import requests
from run import ROOT, dump, ensure_image, run_arm, source_digest


def resume(folder: Path) -> int:
    manifest = json.loads((folder / "manifest.json").read_bytes())
    source = hashlib.sha256((source_digest() + Path(study22.__file__).read_text(encoding="utf-8"))
                            .encode()).hexdigest()
    if manifest["study"] != "spot-advisor-2.2" or manifest["source_sha256"] != source:
        raise ValueError("The execution source differs from the frozen 2.2 manifest.")
    for name, field in (("advisor-frozen.py", "candidate_advisor_sha256"),
                        ("advisor-21-frozen.py", "baseline_advisor_sha256")):
        if hashlib.sha256((folder / name).read_bytes()).hexdigest() != manifest[field]:
            raise ValueError(f"The frozen model helper differs: {name}")
    pilot = manifest["stage"] == "shakedown"
    tasks = study22.planned_tasks(ROOT / "eval/runs/swe-bench-pro-source", pilot)
    if [(t.id, t.image, t.timeout) for t in tasks] != [
            (t["id"], t["image"], t["seconds"]) for t in manifest["tasks"]]:
        raise ValueError("The selected tasks differ from the frozen manifest.")
    stop = threading.Event()

    def continue_case(task, arms):
        for arm in arms:
            if stop.is_set():
                return
            target = folder / task.id / arm
            if target.exists():
                if not (target / "result.json").is_file():
                    print(f"Preserved interrupted arm: {task.id} / {arm}", flush=True)
                continue
            ensure_image(task)
            print(f"Resuming {task.id} / {arm}", flush=True)
            row = run_arm(task, arm, target, "pilot" if pilot else "formal", manifest["seed"],
                          manifest["git_commit"], source)
            dump(target / "result.json", row)
            print(f"{task.id} / {arm}: {row['verdict']} accepted={row['accepted']}", flush=True)
            if any(item["status"] == "rate_limited" for item in requests(row)):
                stop.set()

    errors = 0
    with ThreadPoolExecutor(max_workers=manifest["workers"]) as pool:
        futures = [pool.submit(continue_case, task, item["arms"])
                   for task, item in zip(tasks, manifest["tasks"])]
        for future in as_completed(futures):
            try:
                future.result()
            except (OSError, RuntimeError, ValueError) as exc:
                errors += 1
                print(f"Case incomplete: {exc}", flush=True)
    return int(bool(errors) or stop.is_set())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    folder = parser.parse_args().run.resolve()
    if ROOT / "eval/runs" not in folder.parents:
        parser.error("Use a named directory under eval/runs.")
    raise SystemExit(resume(folder))
