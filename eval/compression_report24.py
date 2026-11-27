"""Export, verify and rebuild the 2.4 compression tables from paired receipts."""

import argparse
from collections import Counter, defaultdict
from hashlib import sha256
import json
import math
from pathlib import Path
import statistics
import subprocess

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from compression_study24 import ARMS, grade_output, read
from gap_study24 import ROOT, digest
from report import SCHEMA, estimate


def write(path, data):
    path.write_bytes(
        (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    )


def export(run, inputs, output, revision):
    manifest = read(run / "manifest.json")
    cases = {r["id"]: r for r in read(inputs / "selection.json")}
    if digest(inputs / "selection.json") != manifest["selection_sha256"]:
        raise ValueError("Selection differs from the original run.")
    results = read(run / "outcomes.json")
    output.mkdir(parents=True, exist_ok=False)
    rows = []
    for job, result in zip(manifest["assignments"], results, strict=True):
        folder = run / job["id"]
        assert all(result[k] == v for k, v in job.items()) and result == read(
            folder / "outcome.json"
        )
        case = cases[job["case"]]
        events_file = folder / "executor.jsonl"
        events = (
            [json.loads(line) for line in events_file.read_bytes().splitlines()]
            if events_file.exists()
            else []
        )
        calls = {}
        for event in events:
            calls[event["attempt_id"]] = {
                k: event[k]
                for k in (
                    "model",
                    "requested_model",
                    "status",
                    "usage",
                    "elapsed_seconds",
                )
                if k in event
            }
        row = dict(
            result,
            schema_version=3,
            phase=manifest["phase"],
            checkpoint_sha256=case["checkpoint_sha256"],
            executor_attempts=list(calls.values()),
        )
        row.setdefault("executor_usage", {"input_tokens": None, "output_tokens": None})
        for k in ("error", "handoff_error"):
            if k in row:
                row[k] = row[k].split(":")[0]
        if (folder / "advisor.json").exists():
            rec = read(folder / "advisor.json")
            row["advisor"] = {
                k: rec[k]
                for k in (
                    "model",
                    "requested_model",
                    "usage",
                    "possible_dispatch",
                    "status",
                    "advice",
                )
                if k in rec
            }
        elif (folder / "dispatch.json").exists():
            row["advisor"] = dict(read(folder / "dispatch.json"), status="interrupted")
        if (folder / "packet.txt").is_file():
            row["packet"] = (folder / "packet.txt").read_text(encoding="utf-8")
        if row["submitted"]:
            source = (folder / "submission").read_bytes()
            assert sha256(source).hexdigest() == row["submission_sha256"]
            row["submission"] = source.decode()
        SCHEMA.validate(row)
        rows.append(row)
    archive = output / "observations.jsonl"
    archive.write_bytes(
        b"".join(
            (json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            for r in rows
        )
    )
    order = list(dict.fromkeys(r["case"] for r in rows))
    index = dict(
        manifest,
        candidate_commit=revision,
        observations_sha256=digest(archive),
        cases=[cases[k] for k in order],
    )
    write(output / "manifest.json", index)


def quality_lower(wins, losses, n):
    """Invert Tango's constrained paired score. Concordant outcomes share the remaining mass."""
    observed = (wins - losses) / n
    if observed == -1:
        return -1.0
    fraction = (wins + losses) / n

    def residual(delta):
        b = fraction + observed * delta
        c = observed * delta - (1 - fraction) * delta**2
        q = (b + math.sqrt(max(0, b * b - 4 * c))) / 2
        variance = max(abs(delta), q) - delta * delta
        return math.sqrt(n) * (observed - delta) / math.sqrt(
            max(1e-20, variance)
        ) - norm.ppf(0.95)

    return float(brentq(residual, -1 + 1e-12, observed))


def cost(row):
    attempts = list(row["executor_attempts"])
    advisor = row.get("advisor", {})
    if advisor.get("possible_dispatch"):
        attempts.append(advisor)
    parts = [
        estimate({"model": a.get("model"), "usage": a.get("usage", {})})
        for a in attempts
    ]
    return (
        sum(parts)
        if parts
        and all(x is not None for x in parts)
        and row.get("error") != "interrupted"
        else None
    )


def summarize(archive):
    index = read(archive / "manifest.json")
    assert digest(archive / "observations.jsonl") == index["observations_sha256"]
    rows = [
        json.loads(line)
        for line in (archive / "observations.jsonl").read_bytes().splitlines()
    ]
    assert len({r["id"] for r in rows}) == len(rows)
    triples = defaultdict(dict)
    for assignment, row in zip(index["assignments"], rows, strict=True):
        SCHEMA.validate(row)
        assert all(row[k] == v for k, v in assignment.items())
        if row["submitted"]:
            assert (
                sha256(row["submission"].encode()).hexdigest()
                == row["submission_sha256"]
            )
        assert not row["verified_by_deadline"] or (
            row["correct"] and row["seconds"] <= index["deadline_seconds"]
        )
        triples[row["case"]][row["arm"]] = row
    assert all(set(v) == set(ARMS) for v in triples.values())
    summaries = {}
    contrasts = {}
    for kind in ("all", "text", "image", "audio"):
        group = [
            v
            for v in triples.values()
            if kind == "all" or v["full"]["modality"] == kind
        ]
        n = len(group)
        wins = sum(
            v["scoped"]["verified_by_deadline"]
            and not v["full"]["verified_by_deadline"]
            for v in group
        )
        losses = sum(
            v["full"]["verified_by_deadline"]
            and not v["scoped"]["verified_by_deadline"]
            for v in group
        )
        lower = quality_lower(wins, losses, n)
        contrasts[kind] = {
            "n": n,
            "scoped_only": wins,
            "full_only": losses,
            "difference": (wins - losses) / n,
            "lower_95": lower,
            "noninferior": lower > -0.05,
        }
        summaries[kind] = {}
        for arm in ARMS:
            selected = [v[arm] for v in group]
            costs = [cost(r) for r in selected]
            summary = {
                "n": n,
                "verified": sum(r["verified_by_deadline"] for r in selected),
                "correct": sum(r["correct"] for r in selected),
                "submitted": sum(r["submitted"] for r in selected),
                "advisor_status": dict(
                    Counter(
                        r.get("advisor", {}).get("status", "no_receipt")
                        for r in selected
                    )
                ),
                "cost_known": sum(x is not None for x in costs),
                "known_dollars": sum(x for x in costs if x is not None),
                "median_seconds": statistics.median(
                    r["seconds"] for r in selected if r["seconds"] is not None
                ),
            }
            for who in ("executor", "advisor"):
                usage = [
                    (
                        r["executor_usage"]
                        if who == "executor"
                        else r.get("advisor", {}).get("usage", {})
                    )
                    for r in selected
                ]
                summary[who] = {
                    k: {
                        "known_total": sum(u[k] for u in usage if u.get(k) is not None),
                        "known_rows": sum(u.get(k) is not None for u in usage),
                    }
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "cached_input_tokens",
                        "reasoning_output_tokens",
                    )
                }
            summaries[kind][arm] = summary
        paired_costs = [[cost(v[a]) for a in ("full", "scoped")] for v in group]
        ratio_upper = None
        if all(x is not None for pair in paired_costs for x in pair):
            prices = np.array(paired_costs)
            passes = np.array(
                [
                    [v[a]["verified_by_deadline"] for a in ("full", "scoped")]
                    for v in group
                ]
            )
            rng = np.random.default_rng(2411)
            strata = [
                [i for i, v in enumerate(group) if v["full"]["modality"] == m]
                for m in ("text", "image", "audio")
            ]
            samples = np.concatenate(
                [
                    rng.choice(idx, (10000, len(idx)), replace=True)
                    for idx in strata
                    if idx
                ],
                axis=1,
            )
            sums, success = prices[samples].sum(axis=1), passes[samples].sum(axis=1)
            with np.errstate(divide="ignore", invalid="ignore"):
                ratios = (sums[:, 1] / success[:, 1]) / (sums[:, 0] / success[:, 0])
            ratios[~np.isfinite(ratios)] = np.inf
            bound = float(np.quantile(ratios, 0.95, method="higher"))
            ratio_upper = bound if math.isfinite(bound) else None
        contrasts[kind]["cost_ratio_upper_95"] = ratio_upper
    return {
        "phase": index["phase"],
        "summary": summaries,
        "paired_scoped_minus_full": contrasts,
    }


def regrade(archive, inputs):
    index = read(archive / "manifest.json")
    rows = [
        json.loads(line)
        for line in (archive / "observations.jsonl").read_bytes().splitlines()
    ]
    keys = {r["id"]: r for r in index["cases"]}
    for r in rows:
        if r["submitted"]:
            key = inputs / r["case"] / "grade.json"
            assert digest(key) == keys[r["case"]]["grade_sha256"]
            passed, _ = grade_output(r["modality"], r["submission"], read(key))
            assert passed == r["correct"], r["id"]
    return sum(r["submitted"] for r in rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    ex = sub.add_parser("export")
    ex.add_argument("run", type=Path)
    ex.add_argument("inputs", type=Path)
    ex.add_argument("output", type=Path)
    ex.add_argument("--candidate", required=True)
    audit = sub.add_parser("verify")
    audit.add_argument("archive", type=Path)
    audit.add_argument("--regrade", type=Path)
    args = parser.parse_args()
    if args.action == "export":
        revision = subprocess.check_output(
            ["git", "rev-parse", "--verify", args.candidate + "^{commit}"],
            cwd=ROOT,
            text=True,
        ).strip()
        export(args.run, args.inputs, args.output, revision)
        write(args.output / "metrics.json", summarize(args.output))
    else:
        result = summarize(args.archive)
        assert result == read(args.archive / "metrics.json")
        print(json.dumps(result["paired_scoped_minus_full"], indent=2))
        if args.regrade:
            print(
                "Independently regraded",
                regrade(args.archive, args.regrade),
                "submitted file(s).",
            )
