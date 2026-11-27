"""Verify and summarize the archived 2.4 diagnostics without model access."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
from pathlib import Path
from statistics import median
import tempfile

from coding_control24 import check, files
from diagnostic_cases import diagnostic_tasks
from export_gap_study24 import admits
from gap_study24 import ROOT, digest, review_files
from sandbox import Sandbox
from suites import Task


def regrade(row, task):
    with tempfile.TemporaryDirectory() as temp:
        work = files(Path(temp), task)
        (work / "core.py").write_bytes(row["submitted_source"].encode())
        (work / "independent.py").write_text(task.grader, encoding="utf-8")
        with Sandbox(task.image) as box:
            box.initial(task, work)
            assert check(box, task.public_check)["passed"] == row["public_pass"]
            assert check(box, "python -B independent.py")["passed"] == row["heldout_pass"]


def verify(folder, run_docker=False):
    index = json.loads((folder / "manifest.json").read_bytes())
    archive = folder / "observations.jsonl"
    assert digest(archive) == index["observations_sha256"]
    groups = defaultdict(list)
    for line in archive.read_bytes().splitlines():
        row = json.loads(line)
        groups[row["cohort"]].append(row)
    assert groups.keys() == index["manifests"].keys() == index["cohorts"].keys()
    for key, rows in groups.items():
        assignments = index["manifests"][key]["assignments"]
        assert len({r["id"] for r in rows}) == len(rows) == index["cohorts"][key]["assigned"]
        for condition, result in zip(assignments, rows, strict=True):
            assert all(result[name] == value for name, value in condition.items())
        print(key, len(rows), "assigned,", sum(bool(r.get("error")) for r in rows), "errors")

    policies = groups["policy"]
    with tempfile.TemporaryDirectory() as temp:
        fixture = Path(temp)
        review_files(fixture, "roster", True, True)
        for row in policies:
            names = [call["tool"] for call in row["calls"]]
            action_ok = names == [row["gold"]]
            assert action_ok == row["action_ok"]
            if names == ["consult"]:
                args = row["calls"][0]["arguments"]
                assert admits(fixture, args, False) == row["form_ok"]
                assert admits(fixture, args, True) == row["fullwidth_replay_ok"]
            assert (action_ok and row["form_ok"] is not False) == row["joint_ok"]
    for wait in (False, True):
        for hint in (False, True):
            trials = [r for r in policies if (r["wait"], r["hint"]) == (wait, hint)]
            print("pending rule", wait, "feedback", hint, "actions", sum(r["action_ok"] for r in trials),
                  "joint", sum(r["joint_ok"] for r in trials), "of", len(trials))
    for lang in ("en", "zh"):
        calls = [r for r in policies if r["lang"] == lang and r["gold"] == "consult" and r["action_ok"]]
        print(lang, "admitted", sum(r["form_ok"] for r in calls), "after punctuation replay",
              sum(r["fullwidth_replay_ok"] for r in calls), "of", len(calls))
    for case in sorted({r["case"] for r in policies}):
        for hint in (False, True):
            trials = [r for r in policies if r["case"] == case and r["hint"] == hint]
            print(case, "feedback", hint, "actions", sum(r["action_ok"] for r in trials),
                  "joint", sum(r["joint_ok"] for r in trials), "of", len(trials))

    for cohort in ("review", "provenance"):
        counts = Counter()
        for row in groups[cohort]:
            with tempfile.TemporaryDirectory() as temp:
                requirement, revision = review_files(Path(temp), row["case"], row["valid"], row["quote"])
            packet = row["packet"]
            assert revision == packet["revision"] == row["artifact_hash"] and row["revision_bound"]
            assert (requirement in packet["scope"]) == row["quote"] == row["requirement_in_packet"]
            labeled = "Parsed artifact SHA-256=" + revision in packet["evidence"][0]
            assert labeled == row.get("label", False)
            assert row["verdict"] == row["review"]["decision"]
            counts[row["valid"], row["quote"], row.get("label", False), row["verdict"]] += 1
        print(cohort, "(valid, quoted, labeled, verdict)")
        for cell, n in sorted(counts.items()):
            print(" ", cell, n)

    cases = {t.id: t for t in diagnostic_tasks(Task)}
    for row in groups["coding"]:
        assert sha256(row["submitted_source"].encode()).hexdigest() == row["patch_sha256"]
        if run_docker:
            regrade(row, cases[row["case"]])
    for arm in ("local", "optional"):
        rows = [r for r in groups["coding"] if r["arm"] == arm]
        tokens = sum(r["executor_usage"][key] for r in rows for key in ("input_tokens", "output_tokens"))
        print(arm, "verified", sum(r["joint"] for r in rows), "of", len(rows), "tokens", tokens,
              "dispatches", sum(r["dispatches"] for r in rows), "median seconds", median(r["seconds"] for r in rows))
    if run_docker:
        print("Fresh Docker regrades matched the stored public and held-out results for", len(groups["coding"]), "submissions.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, nargs="?", default=ROOT / "eval/results/spot24-gap-audit")
    parser.add_argument("--docker", action="store_true", help="Re-run all submitted modules in fresh verifier containers.")
    options = parser.parse_args()
    verify(options.archive, options.docker)
