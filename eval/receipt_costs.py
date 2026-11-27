"""Shared arithmetic for estimating costs from archived model receipts."""

import json
from pathlib import Path


BILLING = json.loads((Path(__file__).resolve().parent / "pricing.json").read_text(encoding="utf-8"))


def advisor_requests(row: dict) -> list[dict]:
    return [attempt for call in row["consultations"] for attempt in call["attempts"]
            if attempt["dispatch_possible"]]


def requests(row: dict) -> list[dict]:
    return [*row["executor_attempts"], *advisor_requests(row)]


def sum_known(values) -> int | float | None:
    parts = list(values)
    return None if any(value is None for value in parts) else sum(parts)


def request_tokens(receipt: dict) -> int | None:
    usage = receipt.get("usage") or {}
    return sum_known(usage.get(key) for key in ("input_tokens", "output_tokens"))


def estimate(receipt: dict) -> float | None:
    pricing = BILLING["models"].get(receipt.get("model"))
    usage = receipt["usage"]
    tokens = [usage.get(key) for key in ("input_tokens", "output_tokens", "cached_input_tokens",
                                        "cache_write_input_tokens")]
    if pricing is None or any(count is None for count in tokens):
        return None
    inputs, outputs, read, write = tokens
    if read + write > inputs:
        return None
    input_weight, output_weight = (2, 1.5) if inputs > BILLING["long_input_threshold"] else (1, 1)
    return (input_weight * ((inputs - read - write) * pricing["input"] + read * pricing["cache_read"]
                             + write * pricing["cache_write"]) + output_weight * outputs * pricing["output"]) / 1e6


def fully_observed(row: dict) -> bool:
    receipts = requests(row)
    return (
        not row["reroutes"] and row["verdict"] not in {"infrastructure_error", "model_rerouted"} and bool(receipts)
        and bool(row["executor_attempts"]) and all(request_tokens(r) is not None for r in receipts)
        and all(r.get("model") == r["requested_model"] for r in receipts)
    )
