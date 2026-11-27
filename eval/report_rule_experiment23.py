"""Recompute paired outcomes from a complete rule_experiment23 run directory."""

import argparse
from collections import Counter
import hashlib
import json
from math import comb
from pathlib import Path


def read(path):
    return json.loads(path.read_bytes())


def paired_outcomes(pairs, passed):
    counts = Counter((bool(passed(old)), bool(passed(new))) for old, new in pairs)
    wins, losses = counts[False, True], counts[True, False]
    discordant = wins + losses
    tail = sum(comb(discordant, k) for k in range(min(wins, losses) + 1))
    return {"n": len(pairs), "current": counts[True, True] + losses, "revised": counts[True, True] + wins,
            "revised_only": wins, "current_only": losses, "both_pass": counts[True, True],
            "both_fail": counts[False, False], "difference_pp": 100 * (wins - losses) / len(pairs),
            "paired_exact_p": min(1, 2 * tail / 2**discordant)}


def summarize(root):
    manifest = read(root / "manifest.json")
    comparison = read(root / "comparison.json")
    rows = []
    for job, row in zip(manifest["assignments"], comparison, strict=True):
        if any(row[k] != v for k, v in job.items()) or row != read(root / row["id"] / "outcome.json"):
            raise ValueError(f"Assignment or outcome mismatch for {job['id']}")
        rows.append(row)

    def pairs(cohort):
        selected = {(r["arm"], r["key"], r["repeat"]): r for r in rows if r["cohort"] == cohort}
        return [(r, selected["revised", key, repeat]) for (arm, key, repeat), r in selected.items()
                if arm == "current"]

    live = pairs("live")
    summary = {"manifest_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
               "all_assignments": len(rows), "errors": [r["id"] for r in rows if r.get("error")],
               "live_timing": paired_outcomes(live, lambda r: r.get("timing") == "correct"),
               "live_final_state": paired_outcomes(live, lambda r: r.get("independent_pass")),
               "live_terminal_verification": paired_outcomes(live, lambda r: r.get("executor_verified"))}
    if pairs("decision"):
        summary["decision"] = paired_outcomes(pairs("decision"), lambda r: r.get("pass"))
        cases = read(root / "snapshot" / "trigger23-decisions.json")
        expected = {key: action for key, _, _, action in cases}
        summary["exploratory_action_only"] = paired_outcomes(
            pairs("decision"), lambda r: r.get("tools") == [expected[r["key"]]])
    for arm in ("current", "revised"):
        counts = Counter(attempts=0, dispatches=0, local_format_rejections=0,
                         usable_advisor_replies=0, unknown_frontier_usage=0,
                         missing_traces=0, missing_executor_usage=0)
        for row in rows:
            if row["arm"] != arm or row["cohort"] != "live":
                continue
            folder = root / row["id"]
            usage = row.get("executor_usage") or {}
            if usage.get("input_tokens") is None or usage.get("output_tokens") is None:
                counts["missing_executor_usage"] += 1
            else:
                counts["known_executor_tokens"] += usage["input_tokens"] + usage["output_tokens"]
            trace_path = folder / "trace.json"
            if not trace_path.exists():
                counts["missing_traces"] += 1
            calls = [e for e in read(trace_path) if e["tool"] == "consult"] if trace_path.exists() else []
            counts["attempts"] += len(calls)
            counts["local_format_rejections"] += sum(bool(e.get("result", {}).get("local_question_rejection")) for e in calls)
            counts["usable_advisor_replies"] += sum(e.get("result", {}).get("status") == "ok" for e in calls)
            for journal in folder.rglob("session.json.attempts.jsonl"):
                events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
                sent = {e["attempt_id"] for e in events if e["event"] == "dispatch"}
                finished = {e["attempt_id"]: e for e in events if e["event"] == "finished"}
                counts["dispatches"] += len(sent)
                for attempt in sent:
                    receipt = finished.get(attempt, {})
                    usage = receipt.get("usage") or {}
                    if usage.get("input_tokens") is None or usage.get("output_tokens") is None:
                        counts["unknown_frontier_usage"] += 1
                    else:
                        counts["known_frontier_tokens"] += usage["input_tokens"] + usage["output_tokens"]
        summary[arm] = dict(counts)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.run), indent=2))
