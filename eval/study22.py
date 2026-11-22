"""Freeze, run, and score the Spot Advisor 2.2 paired coding comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from report import BILLING, SCHEMA, advisor_requests, clean, estimate, request_tokens, requests, sum_known
from run import ADVISOR, ASTRA, ROOT, STUDY_ARMS, dump, ensure_image, run_arm, source_digest
from suites import diagnostic_tasks, load_swe_instances

SEED = 20261121
BASELINE = "e1acc3ccf382e4f284a940b69c5db6ef47b2cab0"
WORKERS = 2
PILOT = ("diagnostic-expiry-boundary", "diagnostic-lru-read-touch", "diagnostic-patch-removal-order")
DEADLINE = 900
ARMS = tuple(STUDY_ARMS)


def freeze(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"Frozen input differs: {path}")
    else:
        with path.open("xb") as output:
            output.write(data)


def planned_tasks(source: Path, pilot: bool):
    if pilot:
        cases = {t.id: t for t in diagnostic_tasks()}
        return [cases[k] for k in PILOT]
    registered = json.loads((ROOT / "eval/study22-tasks.json").read_bytes())
    return load_swe_instances(source, registered["tasks"])


def arm_order(index: int) -> list[str]:
    # A four-treatment Williams design balances both positions and immediate predecessors.
    return [ARMS[(offset + index) % 4] for offset in (0, 1, 3, 2)]


def run_study(output: Path, source: Path, pilot: bool) -> int:
    tasks = planned_tasks(source, pilot)
    if len({task.id for task in tasks}) != len(tasks) or len(tasks) > 40:
        raise ValueError("Expected at most forty distinct screened tasks.")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    old_advisor = subprocess.check_output(
        ["git", "show", f"{BASELINE}:skills/advisor/scripts/ask_expert.py"], cwd=ROOT)
    current_advisor = ADVISOR.read_bytes()
    code = hashlib.sha256((source_digest() + Path(__file__).read_text(encoding="utf-8")).encode()).hexdigest()
    manifest = {
        "study": "spot-advisor-2.2", "stage": "shakedown" if pilot else "coding",
        "git_commit": commit, "source_sha256": code, "baseline_commit": BASELINE,
        "candidate_advisor_sha256": hashlib.sha256(current_advisor).hexdigest(),
        "baseline_advisor_sha256": hashlib.sha256(old_advisor).hexdigest(),
        "seed": SEED, "review_deadline_seconds": DEADLINE, "tool_limit": 100,
        "quality_margin": -.10, "maximum_token_ratio": .90, "workers": WORKERS,
        "models": STUDY_ARMS, "tasks": [
            {"id": task.id, "image": task.image, "seconds": task.timeout, "arms": arm_order(i)}
            for i, task in enumerate(tasks)],
    }
    output.mkdir(parents=True, exist_ok=True)
    freeze(output / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode())
    freeze(output / "advisor-frozen.py", current_advisor)
    freeze(output / "advisor-21-frozen.py", old_advisor)
    stop = threading.Event()

    def paired(task, order):
        if stop.is_set():
            return None
        ensure_image(task)
        for arm in order:
            if stop.is_set():
                return None
            folder = output / task.id / arm
            result = folder / "result.json"
            if result.is_file():
                continue
            if folder.exists():
                raise RuntimeError(f"Preserved interrupted arm for reconciliation: {folder}")
            print(f"Starting {task.id} / {arm}", flush=True)
            row = run_arm(task, arm, folder, "pilot" if pilot else "formal", SEED, commit, code)
            dump(result, row)
            print(f"{task.id} / {arm}: {row['verdict']} accepted={row['accepted']}", flush=True)
            if any(a["status"] == "rate_limited" for a in requests(row)):
                stop.set()
                print("Subscription rate limit reached. Preserved receipts.", flush=True)
        return None

    errors = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(paired, t, item["arms"]) for t, item in zip(tasks, manifest["tasks"])]
        for future in futures:
            try:
                future.result()
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                errors += 1
                print(f"Case incomplete: {exc}", flush=True)
    return int(bool(errors) or stop.is_set())


def usage_row(rows: list[dict], assigned: int) -> dict:
    observed = [attempt for row in rows for attempt in requests(row)]
    known = [a for a in observed if a.get("model") and request_tokens(a) is not None]
    accepted = sum(r["accepted"] for r in rows)
    model_totals = {}
    for name in sorted({a["model"] for a in known}):
        model_rows = [a["usage"] for a in known if a["model"] == name]
        cache_rows = [u for u in model_rows if u.get("cached_input_tokens") is not None]
        cache_input = sum(u["input_tokens"] for u in cache_rows)
        hits = sum(u["cached_input_tokens"] for u in cache_rows)
        input_rows = [u for u in cache_rows if u.get("cache_write_input_tokens") is not None]
        model_totals[name] = {
            "requests": len(model_rows), "input_tokens": sum(u["input_tokens"] for u in model_rows),
            "output_tokens": sum(u["output_tokens"] for u in model_rows),
            "requests_with_cache_reads": len(cache_rows), "cache_read_tokens": hits if cache_rows else None,
            "cache_read_share": hits / cache_input if cache_input else None,
            "cache_write_tokens": sum_known(u.get("cache_write_input_tokens") for u in model_rows),
            "uncached_input_tokens": sum(
                u["input_tokens"] - u["cached_input_tokens"] - u["cache_write_input_tokens"]
                for u in input_rows) if input_rows else None,
            "requests_with_complete_input_breakdown": len(input_rows),
        }
    complete = (len(rows) == assigned and len(known) == len(observed)
                and all(r["executor_attempts"] and not r["reroutes"] for r in rows))
    total = sum(request_tokens(a) for a in known)
    frontier = sum(request_tokens(a) for a in known if a["model"] == ASTRA)
    prices = [estimate(a) for a in known]
    cost = sum_known(prices) if complete else None
    ordinary_prices = [estimate({**a, "usage": {**a["usage"], "cached_input_tokens": 0,
                                                 "cache_write_input_tokens": 0}}) for a in known]
    return {
        "assigned": assigned, "records": len(rows),
        "gradable": sum(r["verdict"] not in {"infrastructure_error", "patch_rejected"} for r in rows),
        "verified": accepted, "verified_by_15_minutes": sum(
            r["accepted"] and r["wall_seconds"] <= DEADLINE for r in rows),
        "observed_total_tokens": total, "observed_frontier_tokens": frontier,
        "attributed_requests": len(known), "requests": len(observed),
        "consultation_dispatches": sum(len(advisor_requests(row)) for row in rows),
        "total_tokens_per_verified": total / accepted if accepted and complete else None,
        "frontier_tokens_per_verified": frontier / accepted if accepted and complete else None,
        "estimated_usd": cost, "estimated_usd_per_verified": cost / accepted if cost is not None and accepted else None,
        "ordinary_input_priced_subtotal_usd": sum(p for p in ordinary_prices if p is not None),
        "priced_request_subtotal_usd": sum(p for p in prices if p is not None),
        "priced_requests": sum(p is not None for p in prices), "models": model_totals,
    }


def score(run: Path, output: Path) -> None:
    manifest = json.loads((run / "manifest.json").read_bytes())
    output.mkdir(parents=True, exist_ok=True)
    by_arm = {arm: {} for arm in ARMS}
    for task in manifest["tasks"]:
        for arm in task["arms"]:
            path = run / task["id"] / arm / "result.json"
            if path.is_file():
                row = json.loads(path.read_bytes())
                SCHEMA.validate(row)
                if (row["task_id"], row["arm"], row["source_sha256"]) != (task["id"], arm, manifest["source_sha256"]):
                    raise ValueError(f"Result differs from the frozen manifest: {path}")
                by_arm[arm][task["id"]] = row
    n = len(manifest["tasks"])
    summaries = {arm: usage_row(list(rows.values()), n) for arm, rows in by_arm.items()}
    complete = all(len(rows) == n for rows in by_arm.values())
    comparisons = {baseline: comparison(by_arm["spot_advisor_22"], rows) for baseline, rows in by_arm.items()
                   if baseline != "spot_advisor_22"}
    gate = comparisons["spot_advisor"]
    dump(output / "scorecard.json", {"study": manifest["stage"], "metrics": summaries, "complete": complete,
                                    "pricing": BILLING,
                                    "candidate_comparisons": comparisons, "default_change_supported": bool(
        manifest["stage"] == "coding" and complete and gate and gate["pass_difference_lower95"] > -.10
        and any(gate[key] is not None and gate[key] <= .90 for key in
                ("total_ratio_upper95", "frontier_ratio_upper95")))})
    dump(output / "manifest.json", manifest)
    (output / "evidence.jsonl").write_text("".join(
        json.dumps(clean(row), sort_keys=True) + "\n" for rows in by_arm.values() for row in rows.values()),
        encoding="utf-8", newline="\n")
    plot_scorecard(summaries, output / "scorecard.png")


def plot_scorecard(summaries: dict, output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = ("2.1 consultation", "Astra alone", "Luna alone", "2.2 consultation")
    rows = list(summaries.values())
    fig, (quality, use) = plt.subplots(1, 2, figsize=(11, 3.5), layout="constrained")
    positions = range(len(rows))
    quality.barh(positions, [row["verified"] for row in rows], color="#176879")
    quality.set(xlim=(0, max(row["assigned"] for row in rows) * 1.28),
                yticks=positions, yticklabels=labels, title="Independent verification")
    for index, row in enumerate(rows):
        quality.text(row["verified"], index, f"  {row['verified']}/{row['assigned']}", va="center")
    frontier = [row["frontier_tokens_per_verified"] or 0 for row in rows]
    other = [(row["total_tokens_per_verified"] or 0) - count for row, count in zip(rows, frontier)]
    use.barh(positions, other, color="#68a392", label="Other models")
    use.barh(positions, frontier, left=other, color="#ae5732", label="Astra")
    use.set(yticks=positions, yticklabels=[], title="Tokens per verified repair")
    for index, row in enumerate(rows):
        if row["total_tokens_per_verified"] is None:
            use.text(0, index, f"Unavailable ({row['attributed_requests']}/{row['requests']} receipts)", va="center")
    for axes in (quality, use):
        axes.invert_yaxis()
        axes.spines[["top", "right"]].set_visible(False)
    use.legend(loc="lower right")
    fig.savefig(output, dpi=160)
    plt.close(fig)


def binomial_upper(events: int, n: int) -> float:
    """One-sided 97.5% Clopper–Pearson upper bound on a discordance probability."""
    if events == n:
        return 1.0
    low, high = 0., 1.
    for _ in range(55):
        p = (low + high) / 2
        cdf = sum(math.comb(n, k) * p**k * (1 - p)**(n - k) for k in range(events + 1))
        if cdf > .025:
            low = p
        else:
            high = p
    return high


def comparison(candidate: dict, baseline: dict) -> dict | None:
    ids = sorted(candidate.keys() & baseline.keys())
    if not ids:
        return None
    pairs = [(candidate[k], baseline[k]) for k in ids]
    wins = sum(a["accepted"] and not b["accepted"] for a, b in pairs)
    losses = sum(b["accepted"] and not a["accepted"] for a, b in pairs)
    out = {"pairs": len(pairs), "wins": wins, "losses": losses,
           "pass_difference_lower95": 1 - binomial_upper(len(pairs) - wins, len(pairs))
           - binomial_upper(losses, len(pairs)), "total_ratio_upper95": None, "frontier_ratio_upper95": None}
    groups = list(zip(*pairs))
    summaries = [usage_row(list(rows), len(pairs)) for rows in groups]
    if any(row["total_tokens_per_verified"] is None for row in summaries):
        return out
    for name in ("total_tokens_per_verified", "frontier_tokens_per_verified", "estimated_usd_per_verified"):
        candidate_value, reference_value = (row[name] for row in summaries)
        if candidate_value is not None and reference_value:
            out[name + "_savings"] = 1 - candidate_value / reference_value
    rng = random.Random(SEED)
    weights = [([sum(request_tokens(a) for a in requests(row) if not frontier or a["model"] == ASTRA)
                 for row in rows], [row["accepted"] for row in rows])
               for frontier in (False, True) for rows in groups]
    ratios = [[], []]
    for _ in range(10_000):
        sample = rng.choices(range(len(pairs)), k=len(pairs))
        for index in range(2):
            (a, ag), (b, bg) = weights[2*index:2*index+2]
            pa, pb, ta, tb = (sum(seq[i] for i in sample) for seq in (ag, bg, a, b))
            ratios[index].append((ta * pb) / (tb * pa) if pa and pb and tb else math.inf)
    for key, values in zip(("total_ratio_upper95", "frontier_ratio_upper95"), ratios):
        upper = sorted(values)[9499]
        out[key] = upper if math.isfinite(upper) else None
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("shakedown", "coding", "score"))
    parser.add_argument("--run", required=True, type=Path)
    parser.add_argument("--swe-source", type=Path, default=ROOT / "eval/runs/swe-bench-pro-source")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run = args.run.resolve()
    if ROOT / "eval/runs" not in run.parents:
        parser.error("Use a named run directory under eval/runs.")
    if args.action == "score":
        if args.output is None or args.output.resolve() == run:
            parser.error("Supply a separate scorecard output.")
        score(run, args.output)
    else:
        raise SystemExit(run_study(run, args.swe_source.resolve(), args.action == "shakedown"))
