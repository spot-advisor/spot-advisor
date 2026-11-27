"""Run a frozen paired rule experiment on fresh decisions and live fixtures."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shutil
import time

from trigger_pilot23 import ADVISOR, AUDIO_CLIPS, CASES, LUNA, ROOT, SKILL, DirectSession, dump, tool, trial

HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "trigger23-decisions.json"
PROPOSAL = HERE / "trigger23-proposal.txt"
PROTOCOL = ROOT / "docs/spot-advisor-2.3-rule-experiment.md"
DECISION_TOOLS = [
    tool("inspect", "Collect a local observation.", ()),
    tool("apply", "Make a local change.", ()),
    tool("consult", "Submit a decision to the external reviewer.", ("question", "evidence")),
    tool("verify", "Run the local final acceptance check.", ()),
    tool("report", "Return a status to the owner.", ()),
]


def decision(folder, brief, expected, instruction):
    folder.mkdir()
    session = DirectSession(folder / "executor.jsonl")
    prompt = f"{brief}\n\nChoose your next action using one tool.\n\n{instruction}"
    response = session.request(LUNA, "max", [{"role": "user", "content": prompt}],
                               DECISION_TOOLS, time.monotonic() + 60)
    choices = [item for item in response.get("output", []) if item.get("type") == "function_call"]
    calls = [{"tool": c["name"], "arguments": json.loads(c["arguments"])} for c in choices]
    dump(folder / "choices.json", calls)
    passed = len(calls) == 1 and calls[0]["tool"] == expected
    if passed and expected == "consult":
        args = calls[0]["arguments"]
        question = args.get("question", "").strip()
        passed = len(question) >= 16 and question.endswith("?") and bool(args.get("evidence", "").strip())
    return {"pass": passed, "tools": [c["tool"] for c in calls], "executor_usage": session.usage()}


def paired_schedule(scenarios, seed=971):
    pairs = [("decision", key, 1) for key in scenarios]
    pairs += [("live", key, repeat) for key in CASES for repeat in (1, 2)]
    rng = random.Random(seed)
    rng.shuffle(pairs)
    jobs = []
    for cohort, key, repeat in pairs:
        arms = ["current", "revised"]
        rng.shuffle(arms)
        for arm in arms:
            jobs.append({"cohort": cohort, "key": key, "repeat": repeat, "arm": arm,
                         "id": f"s{len(jobs) + 1:03}"})
    return jobs


def freeze(folder, previous=None):
    folder.mkdir(parents=True)
    snapshot = folder / "snapshot"
    snapshot.mkdir()
    sources = [SKILL, SCENARIOS, PROPOSAL, PROTOCOL, Path(__file__), HERE / "trigger_pilot23.py",
               HERE / "direct_session.py", HERE / "run.py", ADVISOR]
    sources.extend(AUDIO_CLIPS)
    if previous:
        sources.append(ROOT / "docs/spot-advisor-2.3-rule-results.md")
    hashes = {}
    for source in sources:
        dest = snapshot / source.name
        shutil.copyfile(source, dest)
        hashes[source.relative_to(ROOT).as_posix()] = hashlib.sha256(dest.read_bytes()).hexdigest()
    original = (snapshot / SKILL.name).read_text(encoding="utf-8")
    start, end = original.index("\n1. ") + 4, original.index("\n2. ")
    revised = original[:start] + (snapshot / PROPOSAL.name).read_text(encoding="utf-8").strip() + original[end:]
    (snapshot / "candidate.md").write_text(revised, encoding="utf-8")
    hashes["candidate.md"] = hashlib.sha256((snapshot / "candidate.md").read_bytes()).hexdigest()
    rules = {"current": original, "revised": revised}
    scenarios = {row[0]: row for row in json.loads((snapshot / SCENARIOS.name).read_bytes())}
    seed = 972 if previous else 971
    if previous:
        prior_hashes = json.loads((previous / "manifest.json").read_bytes())["sha256"]
        old = {key.replace("\\", "/"): digest for key, digest in prior_hashes.items()}
        for path in (SKILL, PROPOSAL, *AUDIO_CLIPS):
            key = path.relative_to(ROOT).as_posix()
            previous_key = key
            if key not in old and path in AUDIO_CLIPS:
                previous_key = f"eval/runs/spot23-audio-final-live/{path.name}"
            if old.get(previous_key) != hashes[key]:
                raise ValueError(f"Replication input changed: {key}")
        if old["candidate.md"] != hashes["candidate.md"]:
            raise ValueError("Candidate changed since original comparison.")
    jobs = paired_schedule({} if previous else scenarios, seed)
    dump(folder / "manifest.json", {"created": datetime.now(timezone.utc).isoformat(), "seed": seed,
         "replicates": str(previous) if previous else None,
         "assignments": jobs, "sha256": hashes, "model": LUNA, "reasoning": "max", "workers": 4})
    return rules, scenarios, jobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--replicate-live", type=Path, metavar="ORIGINAL_RUN")
    args = parser.parse_args()
    folder = args.output
    rules, scenarios, jobs = freeze(folder, args.replicate_live)
    smoke = trial(folder / "smoke", CASES["repair"], rules["current"])
    if smoke["error"] or not smoke["executor_verified"] or not smoke["independent_pass"]:
        raise SystemExit("Live adapter smoke failed; inspect smoke/grade.json before starting assignments.")

    def run(job):
        target = folder / job["id"]
        instruction = rules[job["arm"]]
        try:
            if job["cohort"] == "decision":
                _, _, brief, expected = scenarios[job["key"]]
                result = decision(target, brief, expected, instruction)
            else:
                result = trial(target, CASES[job["key"]], instruction)
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {exc}"}
        row = {**job, **result}
        dump(target / "outcome.json", row)
        return row

    # Keep paired arms close in time and execute them sequentially within a pair.
    def run_pair(pair):
        return [run(job) for job in pair]

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = [row for pair in pool.map(run_pair, zip(jobs[::2], jobs[1::2])) for row in pair]
    dump(folder / "comparison.json", results)


if __name__ == "__main__":
    main()
