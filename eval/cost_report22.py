"""Summarize costs on a common, fully priced subset of recorded four-arm issues."""

from pathlib import Path
from statistics import mean, median

from report import ASTRA, BILLING, estimate, request_tokens, requests

LABELS = {"luna_only": "Luna alone", "spot_advisor": "Luna with 2.1 policy",
          "astra_only": "Astra alone", "spot_advisor_22": "Luna with 2.2 policy"}


def deadline_wait(outcomes: list[tuple[bool, float | None]], deadline: float) -> dict:
    if not outcomes or any(passed and seconds is None for passed, seconds in outcomes):
        return {"verified_by_deadline": None, "mean_wait_seconds": None}
    waits = [min(seconds, deadline) if passed else deadline for passed, seconds in outcomes]
    return {"verified_by_deadline": sum(passed and seconds <= deadline for passed, seconds in outcomes),
            "mean_wait_seconds": mean(waits)}


def priced_requests(row: dict) -> tuple[list[dict], str | None]:
    if not row["executor_attempts"]:
        return [], "missing_executor_attempt"
    if row["reroutes"]:
        return [], "rerouted_work"
    receipts = requests(row)
    if any(request_tokens(receipt) is None for receipt in receipts):
        return [], "missing_request_usage"
    if any(estimate(receipt) is None for receipt in receipts):
        return [], "unpriceable_request"
    return receipts, None


def first_request_overhead(by_task: dict) -> dict:
    """Compare the first observed Luna Max input before the workflow branches."""
    deltas = {}
    for case, arms in by_task.items():
        attempts = [arms.get(arm, {}).get("executor_attempts") for arm in ("spot_advisor_22", "luna_only")]
        if not all(attempts):
            continue
        a, b = (rows[0] for rows in attempts)
        if not all(row.get("model") == "gpt-6-luna" and row.get("reasoning_effort") == "max"
                   for row in (a, b)):
            continue
        candidate, baseline = ((row.get("usage") or {}).get("input_tokens") for row in (a, b))
        if candidate is not None and baseline is not None:
            deltas[case] = candidate - baseline
    values = list(deltas.values())
    return {"paired_issues": len(deltas), "candidate_minus_luna_input_tokens": dict(sorted(deltas.items())),
            "median": median(values) if values else None,
            "minimum": min(values) if values else None, "maximum": max(values) if values else None}


def missing_cost_sensitivity(matched: dict) -> dict:
    totals = {}
    for arm in LABELS:
        rows = [arms[arm] for arms in matched.values()]
        if any(not row["executor_attempts"] or row["reroutes"] for row in rows):
            continue
        prices = [estimate(receipt) for row in rows for receipt in requests(row)]
        totals[arm] = {"verified": sum(row["accepted"] for row in rows),
                       "observed_usd": sum(price for price in prices if price is not None),
                       "unpriced_requests": prices.count(None)}
    candidate = totals.get("spot_advisor_22")
    if not candidate or not candidate["unpriced_requests"] or not candidate["verified"]:
        return {}
    break_even = {
        arm: baseline["observed_usd"] * candidate["verified"] / baseline["verified"] - candidate["observed_usd"]
        for arm, baseline in totals.items()
        if arm != "spot_advisor_22" and baseline["verified"] and not baseline["unpriced_requests"]
    }
    return {"task_ids": sorted(matched), "totals": totals, "unpriced_usd_to_equal_cost_per_verified": break_even}


def matched_resource_audit(by_task: dict, pricing: dict, deadline: int = 900) -> dict:
    if pricing != BILLING:
        raise ValueError("The snapshot and analysis checkout use different pricing. Restore the pinned pricing file.")
    eligible, omitted = {}, {}
    matched = {task: arms for task, arms in by_task.items() if arms.keys() == LABELS.keys()}
    for task, arms in matched.items():
        by_arm = {arm: priced_requests(arms[arm]) for arm in LABELS}
        incomplete = {arm: reason for arm, (_, reason) in by_arm.items() if reason}
        if incomplete:
            omitted[task] = incomplete
        else:
            eligible[task] = {arm: receipts for arm, (receipts, _) in by_arm.items()}
    summary = {}
    for arm in LABELS:
        rows = [by_task[task][arm] for task in eligible]
        receipts = [receipt for task_receipts in eligible.values() for receipt in task_receipts[arm]]
        passes = sum(row["accepted"] for row in rows)
        n = len(rows)
        inputs = sum(r["usage"]["input_tokens"] for r in receipts)
        reads = sum(r["usage"]["cached_input_tokens"] for r in receipts)
        writes = sum(r["usage"]["cache_write_input_tokens"] for r in receipts)
        outputs = sum(r["usage"]["output_tokens"] for r in receipts)
        total = inputs + outputs
        frontier = sum(request_tokens(r) for r in receipts if r["model"] == ASTRA)
        price = sum(estimate(r) for r in receipts)
        uncached_rate_price = sum(
            estimate({
                **receipt,
                "usage": {**receipt["usage"], "cached_input_tokens": 0, "cache_write_input_tokens": 0},
            })
            for receipt in receipts
        )
        delivery = {}
        for limit in sorted({300, 600, deadline}):
            if limit <= deadline:
                timely = sum(row["accepted"] and row["wall_seconds"] <= limit for row in rows)
                delivery[str(limit)] = {
                    "verified": timely,
                    "tokens_per_timely_verification": total / timely if timely else None,
                    "estimated_usd_per_timely_verification": price / timely if timely else None,
                }
        summary[arm] = {
            "observed_tasks": n, "passed": passes, "receipts": len(receipts),
            "input_tokens": inputs, "uncached_input_tokens": inputs - reads - writes,
            "cache_read_tokens": reads, "cache_read_share": reads / inputs if inputs else None,
            "cache_write_tokens": writes, "output_tokens": outputs,
            "total_tokens": total, "frontier_tokens": frontier, "estimated_api_usd": price if n else None,
            "total_tokens_per_pass": total / passes if passes else None,
            "frontier_tokens_per_pass": frontier / passes if passes else None,
            "estimated_api_usd_per_pass": price / passes if passes else None,
            "estimated_uncached_input_usd_per_pass": uncached_rate_price / passes if passes else None,
            "passed_by_15_minutes": delivery[str(deadline)]["verified"],
            "delivery_by_review_seconds": delivery,
        }
    timing = {arm: deadline_wait([(pair[arm]["accepted"], pair[arm]["wall_seconds"]) for pair in matched.values()],
                                 deadline) for arm in LABELS}
    return {"matched_tasks": sorted(eligible), "excluded_for_usage": omitted, "pricing": pricing, "arms": summary,
            "first_request_overhead": first_request_overhead(by_task),
            "missing_cost_sensitivity": missing_cost_sensitivity(matched),
            "matched_deadline_wait": {"seconds": deadline, "task_ids": sorted(matched), "arms": timing}}


def plot_matched_resources(audit: dict, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    rows = [audit["arms"][arm] for arm in LABELS]
    n = len(audit["matched_tasks"])
    fig, (grades, usage) = plt.subplots(1, 2, figsize=(6.5, 2.35), layout="constrained",
                                       gridspec_kw={"width_ratios": [1, 1.5]})
    y = range(len(rows))
    on_time = [row["passed_by_15_minutes"] for row in rows]
    late = [row["passed"] - passed for row, passed in zip(rows, on_time)]
    grades.barh(y, on_time, color="#246365", label="By 15 minutes")
    grades.barh(y, late, left=on_time, color="#d5e5e3", edgecolor="#246365",
                linewidth=.6, hatch="///", label="After 15 minutes")
    grades.set(yticks=list(y), yticklabels=list(LABELS.values()), xlabel="Verified repairs", xlim=(0, n * 1.3))
    for index, row in enumerate(rows):
        grades.text(row["passed"] + n * .02, index, f"{row['passed']}/{n}", va="center", fontsize=9)
    frontier = [r["frontier_tokens_per_pass"] or 0 for r in rows]
    other = [(r["total_tokens_per_pass"] or 0) - t for r, t in zip(rows, frontier)]
    usage.barh(y, other, color="#246365", label="Luna")
    usage.barh(y, frontier, left=other, color="#dc9a73", edgecolor="#843f21",
               linewidth=.6, hatch="///", label="Astra")
    max_tokens = max(1, max(a + b for a, b in zip(other, frontier)))
    usage.set(yticks=list(y), yticklabels=[], xlabel="Tokens per verified repair", xlim=(0, max_tokens * 1.38))
    usage.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value / 1000:,.0f}k"))
    for index, row in enumerate(rows):
        cost = row["estimated_api_usd_per_pass"]
        label = f"${cost:.3f}/pass" if cost is not None else "Unavailable"
        usage.text(other[index] + frontier[index] + max_tokens * .02, index, label, va="center", fontsize=9)
    for axes in (grades, usage):
        axes.invert_yaxis()
        axes.spines[["top", "right", "left"]].set_visible(False)
        axes.tick_params(axis="y", length=0)
        axes.tick_params(labelsize=8)
        axes.legend(loc="lower center", bbox_to_anchor=(.5, -.43), ncols=2, frameon=False, fontsize=8)
    with plt.rc_context({"pdf.fonttype": 42}):
        fig.savefig(path, facecolor="white")
    plt.close(fig)
