"""Audit the frozen peer cases and join them to the matching 2.2 coding arms."""

import hashlib
import json
import math
from pathlib import Path
from statistics import median

from cost_report22 import deadline_wait
from report import ASTRA, estimate, request_tokens, requests, sum_known

PEERS = {
    "second-opinion": "second-opinion",
    "pal-mcp": "PAL MCP chat",
    "claude_native": "Anthropic native Advisor",
}
CLAUDE_STANDARD_RATES = {
    "as_of": "2026-11-21",
    "source": "https://platform.claude.com/docs/en/about-claude/pricing",
    "basis": "Standard USD per million tokens, assuming five-minute cache writes",
    "models": {"claude-sonnet-5": [2, .20, 2.5, 10], "claude-opus-5-5": [4, .20, 5, 20]},
}
CLAUDE_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")
CODEX_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_write_input_tokens")
PEER_RECEIPT_FILES = {
    "second-opinion": ("executor-attempts.jsonl", "review-receipt.json"),
    "pal-mcp": ("pal-advisor-attempts.jsonl", "executor-attempts.jsonl"),
    "claude_native": ("receipts.jsonl",),
}


def codex_usage(folders: list[Path]) -> dict:
    """Reconcile every recorded dispatch without assuming missing usage is zero."""
    finished = []
    dispatched = 0
    incomplete = False
    for journal in folders:
        if not journal.is_file():
            incomplete = True
            continue
        events = [json.loads(line) for line in journal.read_bytes().splitlines()]
        ids = [line["attempt_id"] for line in events if line["event"] == "dispatched"]
        receipts = [line for line in events if line["event"] == "finished"]
        settled = [line["attempt_id"] for line in receipts]
        if len(ids) != len(set(ids)) or len(settled) != len(set(settled)) or set(settled) - set(ids):
            raise ValueError(f"Invalid attempt journal: {journal}")
        dispatched += len(ids)
        finished.extend(receipts)
        incomplete = incomplete or set(settled) != set(ids)
    summary = usage_totals(finished) if dispatched and not incomplete else usage_totals([])
    summary.update(dispatches=dispatched, returned_receipts=len(finished))
    return summary


def usage_totals(receipts: list[dict]) -> dict:
    if not receipts:
        return dict.fromkeys(("tokens", "astra_tokens", "cache_read_tokens", "cache_write_tokens", "api_usd"))
    return {
        "tokens": sum_known(request_tokens(r) for r in receipts),
        "astra_tokens": (sum_known(request_tokens(r) for r in receipts if r["model"] == ASTRA)
                         if all(r.get("model") for r in receipts) else None),
        "cache_read_tokens": sum_known(r["usage"].get("cached_input_tokens") for r in receipts),
        "cache_write_tokens": sum_known(r["usage"].get("cache_write_input_tokens") for r in receipts),
        "api_usd": sum_known(estimate(r) for r in receipts),
    }


def reviewer_usage(folder: Path) -> dict:
    receipt = json.loads((folder / "review-receipt.json").read_bytes())
    fields = receipt.get("usage") or {}
    counts = [fields.get(key) for key in CLAUDE_FIELDS]
    if not all(type(value) is int and value >= 0 for value in counts):
        counts = [None, None, None, None]
    estimate_usd = receipt.get("total_cost_usd")
    model_usage = receipt.get("modelUsage") or {}
    return {"tokens": sum_known(counts), "uncached_input_tokens": counts[0], "cache_read_tokens": counts[1],
            "cache_write_tokens": counts[2], "output_tokens": counts[3],
            "cli_estimated_usd": (estimate_usd if type(estimate_usd) in (float, int)
                                  and math.isfinite(estimate_usd) and estimate_usd >= 0 else None),
            "model_labels": sorted(model_usage), "cost_basis_labels": sorted(
                {str(r.get("costBasis") or "unknown") for r in model_usage.values()})}


def native_usage(folder: Path, turn_status: str) -> dict:
    journal = folder / "receipts.jsonl"
    receipts = [json.loads(line) for line in journal.read_bytes().splitlines()] if journal.is_file() else []
    executor, advisor = [], []
    missing = turn_status in {"anthropic_http_error", "executor_interrupted"}
    for receipt in receipts:
        usage = receipt.get("usage") or {}
        executor.append((receipt.get("model"), usage))
        messages = [row for row in (usage.get("iterations") or []) if row.get("type") == "advisor_message"]
        advisor.extend((row.get("model"), row) for row in messages)
        if "advisor_tool_result" in receipt["block_types"] and not messages:
            missing = True
    by_role = {}
    for role, rows in (("executor", executor), ("advisor", advisor)):
        counts = [[usage.get(key) for key in CLAUDE_FIELDS] for _, usage in rows]
        known = all(type(value) is int and value >= 0 for record in counts for value in record)
        prices = [CLAUDE_STANDARD_RATES["models"].get(model) for model, _ in rows]
        by_role[role] = {
            "model_labels": sorted({model for model, _ in rows if model}),
            "observed_tokens": sum(map(sum, counts)) if known else None,
            "cache_read_tokens": sum(count[1] for count in counts) if known else None,
            "cache_write_tokens": sum(count[2] for count in counts) if known else None,
            "api_usd": (sum(sum(count * rate for count, rate in zip(usage, price)) for usage, price
                            in zip(counts, prices)) / 1e6 if known and None not in prices else None),
        }
    complete = bool(receipts) and not missing
    return {
        "tokens": sum_known(role["observed_tokens"] for role in by_role.values()) if complete else None,
        "api_usd": sum_known(role["api_usd"] for role in by_role.values()) if complete else None,
        "returned_receipts": len(receipts),
        "advisor_iterations_observed": len(advisor),
        "by_role": by_role,
    }


def matched_peer_metrics(rows: dict, deadline: float = 900) -> dict:
    paired = {task: row for task, row in rows.items() if row["spot_22_pass"] is not None}
    summary = {"task_ids": sorted(paired)}
    timing = {"seconds": deadline}
    for name, prefix in (("peer", ""), ("spot_22", "spot_22_")):
        elapsed = [row[prefix + "wall_seconds"] for row in paired.values()]
        summary[name] = {
            "verified": sum(row[f"{name}_pass"] for row in paired.values()),
            "median_seconds_to_grade": median(elapsed) if elapsed and None not in elapsed else None,
        }
        timing[name] = deadline_wait([(row[f"{name}_pass"], row[prefix + "wall_seconds"])
                                      for row in paired.values()], deadline)
    summary["deadline_wait"] = timing
    jointly_verified = {
        task: row for task, row in paired.items()
        if row["peer_pass"] and row["spot_22_pass"]
        and None not in (row["wall_seconds"], row["spot_22_wall_seconds"])
    }
    deltas = [row["wall_seconds"] - row["spot_22_wall_seconds"] for row in jointly_verified.values()]
    summary["joint_pass_timing"] = {"task_ids": sorted(jointly_verified),
                                    "peer_earlier": sum(value < 0 for value in deltas),
                                    "spot_22_earlier": sum(value > 0 for value in deltas),
                                    "ties": deltas.count(0),
                                    "median_peer_minus_spot_seconds": median(deltas) if deltas else None}
    for unit in ("tokens", "api_usd"):
        common = {task: row for task, row in paired.items()
                  if all(row[key] and row[key][unit] is not None for key in ("usage", "spot_22_usage"))}
        scope = {"task_ids": sorted(common)}
        for name, field in (("peer", "usage"), ("spot_22", "spot_22_usage")):
            passes = sum(row[f"{name}_pass"] for row in common.values())
            amount = sum(row[field][unit] for row in common.values()) if common else None
            scope[name] = {"verified": passes, "total": amount,
                           "per_verified": amount / passes if passes else None}
        summary[unit] = scope
    return summary


def summarize_peer(cases: list[str], rows: dict, pending: dict, deadline: float = 900) -> dict:
    if rows.keys() & pending.keys() or rows.keys() | pending.keys() != set(cases):
        raise ValueError("Peer dispositions do not partition the registered cases.")
    pairs = [row["peer_minus_spot_22"] for row in rows.values() if row["peer_minus_spot_22"] is not None]
    known_usage = [row for row in rows.values() if row["usage"] and row["usage"]["tokens"] is not None]
    known_cost = [row for row in known_usage if row["usage"]["api_usd"] is not None]
    return {"assigned": len(cases), "graded": len(rows),
            "passes": sum(row["peer_pass"] for row in rows.values()),
            "paired": len(pairs), "peer_only_pass": pairs.count(1), "spot_only_pass": pairs.count(-1),
            "advisor_delivery_failures": sum(row["state"] != "graded" for row in rows.values()),
            "rows_with_known_tokens": len(known_usage), "rows_with_comparable_api_price": len(known_cost),
            "graded_rows": rows, "ungraded_assignments": pending,
            "matched_metrics": matched_peer_metrics(rows, deadline)}


def peer_audit(roots: dict[str, Path], by_task: dict, deadline: float = 900) -> dict:
    assignment = Path(__file__).with_name("study22-tasks.json")
    selected = assignment.read_bytes()
    cases = json.loads(selected)["peer_tasks"]
    out = {}
    for peer, name in PEERS.items():
        rows = {}
        pending = {}
        for task in cases:
            folder = roots[peer] / task / peer if peer in roots else None
            if folder is None:
                pending[task] = "no_run_supplied"
                continue
            if not (folder / "result.json").is_file():
                marker = folder / "attempt.json"
                if marker.is_file() and json.loads(marker.read_bytes()).get("status") == "review_failed":
                    pending[task] = "review_failed"
                else:
                    pending[task] = "ungraded_with_local_trace" if folder.is_dir() else "unstarted"
                continue
            row = json.loads((folder / "result.json").read_bytes())
            patch_hash = hashlib.sha256((folder / "model.patch").read_bytes()).hexdigest()
            if (row["task"] != task or row.get("peer", row.get("arm")) != peer
                    or type(row["accepted"]) is not bool
                    or row.get("artifact_sha256", patch_hash) != patch_hash):
                raise ValueError(f"Peer result and artifact disagree: {folder}")
            before = by_task.get(task, {}).get("spot_advisor_22")
            effect = None if before is None else int(row["accepted"]) - int(before["accepted"])
            state = "graded"
            if peer == "pal-mcp" and row.get("advisor_error"):
                state = "graded_with_advisor_delivery_failure"
            usage = None
            if peer == "pal-mcp":
                usage = codex_usage([folder / name for name in ("pal-advisor-attempts.jsonl", "executor-attempts.jsonl")])
            elif peer == "second-opinion":
                executor = codex_usage([folder / "executor-attempts.jsonl"])
                review = reviewer_usage(folder)
                usage = {"tokens": sum_known((executor["tokens"], review["tokens"])), "executor": executor,
                         "review": review, "api_usd": None}
            elif peer == "claude_native":
                usage = native_usage(folder, row["turn"]["status"])
            rows[task] = {"state": state, "peer_pass": row["accepted"], "peer_verdict": row["verdict"],
                          "wall_seconds": row["wall_seconds"], "artifact_sha256": patch_hash,
                          "usage": usage, "spot_22_wall_seconds": before["wall_seconds"] if before else None,
                          "spot_22_usage": (usage_totals(requests(before))
                                            if before and before["executor_attempts"] and not before["reroutes"] else None),
                          "spot_22_pass": before["accepted"] if before else None, "peer_minus_spot_22": effect,
                          "product_revision": row.get("product_revision"), "workflow": row.get("workflow")}
        out[peer] = {"label": name, **summarize_peer(cases, rows, pending, deadline)}
    return {"assignment_sha256": hashlib.sha256(selected).hexdigest(), "peers": out,
            "native_pricing": CLAUDE_STANDARD_RATES}


def peer_evidence_files(folder: Path, peer: str) -> dict[str, bytes]:
    result = json.loads((folder / "result.json").read_bytes())
    fields = ("task", "peer", "arm", "accepted", "verdict", "wall_seconds", "artifact_sha256",
              "product_revision", "workflow")
    evidence = {key: result[key] for key in fields if key in result}
    if peer == "pal-mcp":
        evidence["advisor_error"] = bool(result.get("advisor_error"))
    if peer == "claude_native":
        evidence["turn"] = {"status": result["turn"]["status"]}
    files = {"result.json": json.dumps(evidence).encode(), "model.patch": (folder / "model.patch").read_bytes()}
    for name in PEER_RECEIPT_FILES[peer]:
        source = folder / name
        if not source.is_file():
            continue
        if name == "review-receipt.json":
            row = json.loads(source.read_bytes())
            model_usage = row.get("modelUsage") or {}
            cleaned = {"usage": {k: (row.get("usage") or {}).get(k) for k in CLAUDE_FIELDS},
                       "total_cost_usd": row.get("total_cost_usd"),
                       "modelUsage": {model: {"costBasis": item.get("costBasis")} for model, item in model_usage.items()}}
            files[name] = json.dumps(cleaned).encode()
            continue
        events = [json.loads(line) for line in source.read_bytes().splitlines()]
        if name == "receipts.jsonl":
            cleaned_events = []
            for row in events:
                usage = row.get("usage") or {}
                iterations = [{key: item[key] for key in (*CLAUDE_FIELDS, "type", "model") if key in item}
                              for item in usage.get("iterations") or []]
                cleaned_events.append({"model": row.get("model"), "block_types": row["block_types"],
                                       "usage": {**{k: usage.get(k) for k in CLAUDE_FIELDS}, "iterations": iterations}})
        else:
            attempt_numbers = {}
            cleaned_events = []
            for row in events:
                label = attempt_numbers.setdefault(row["attempt_id"], len(attempt_numbers))
                usage = row.get("usage")
                cleaned_events.append({"attempt_id": label, "event": row["event"], "model": row.get("model"),
                                       "usage": {key: usage.get(key) for key in CODEX_FIELDS} if usage else None})
        files[name] = b"".join(json.dumps(row).encode() + b"\n" for row in cleaned_events)
    return files
