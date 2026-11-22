"""Regressions for the 2.2 entry point and paired measurement."""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_plain_v2_preview_does_not_route_or_write(monkeypatch, tmp_path, capsys):
    import importlib.util

    spec = importlib.util.spec_from_file_location("preview22", ROOT / "skills/advisor/scripts/ask_expert.py")
    advisor = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(advisor)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["ask_expert.py", "--preview", "--protocol", "v2",
                                      "--question", "Which assertion would distinguish A from B?"])
    monkeypatch.setattr(advisor, "route_consultation", lambda *_: pytest.fail("Preview reached transport"))
    assert advisor.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "preview" and result["round"] == 0
    assert "Decision question: Which assertion" in result["packet"]
    assert not list(tmp_path.iterdir())


def test_candidate_gates_on_live_revision_and_local_checks(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from report import SCHEMA, clean
    from run import TaskExecution

    state = TaskExecution(SimpleNamespace(), "spot_advisor_22", None, tmp_path, float("inf"))
    current_revision = "a" * 64
    state.checkpoints = [{"passed": False, "revision": current_revision}]
    state.last_check_output = "A focused assertion failed."
    monkeypatch.setattr(state, "revision", lambda: current_revision)
    calls = []
    monkeypatch.setattr(state, "consult", lambda *args, **kwargs: calls.append((args, kwargs)) or "Check A.")
    packet = dict(check_ran=True, dependencies_ready=True, local_probe_available=False,
                  option_a="Fix mapper", option_b="Fix validation",
                  unresolved_evidence="The focused assertion failed for both paths.",
                  question="Which hypothesis does the evidence support?")
    def ask(reason, **changes):
        result = state.handle({"tool": "ask_at_checkpoint", "arguments": {**packet, **changes}})
        expected = state.checkpoints[-1]["revision"] if state.checkpoints else None
        event = state.events[-1]
        assert event == {"tool": "ask_at_checkpoint", "consultation_gate": reason,
                         "checkpoint_revision": expected, "checkpoint_index": 0 if expected else None,
                         "elapsed_seconds": event["elapsed_seconds"]}
        assert result["success"] == (reason == "admitted")

    ask("local_probe_available", local_probe_available=True)
    ask("incomplete_evidence", dependencies_ready=False)
    ask("incomplete_evidence", option_a=packet["option_b"])
    state.deadline = float("-inf")
    ask("verification_time")
    state.deadline = float("inf")
    state.checkpoints[-1]["revision"] = "b" * 64
    ask("stale_revision")
    state.checkpoints[-1]["revision"] = current_revision
    ask("admitted")
    assert len(calls) == 1 and calls[0][1]["question"] == packet["question"]
    state.consultations.append({"status": "ok"})
    ask("consultation_used")
    state.consultations.clear()
    state.checkpoints[-1]["passed"] = True
    ask("no_failed_check")
    state.checkpoints[-1]["passed"] = False
    state.tool_limit = state.tools + 1
    ask("tool_budget")
    assert len(calls) == 1
    state.tool_limit = 100
    monkeypatch.setattr(state, "consult", lambda *args, **kwargs: "")
    result = state.handle({"tool": "ask_at_checkpoint", "arguments": packet})
    assert result["success"] is False
    assert state.events[-1]["consultation_gate"] == "admitted"
    state.checkpoints.clear()
    ask("no_failed_check")
    state.checkpoints.append({"passed": False, "revision": current_revision})

    def timeout_revision_check():
        raise subprocess.TimeoutExpired("capture revision", 1)

    monkeypatch.setattr(state, "revision", timeout_revision_check)
    ask("evaluation_interrupted")
    archived = clean({"turn": {}, "executor_attempts": [], "consultations": [], "tool_events": state.events})
    assert archived["tool_events"] == state.events
    SCHEMA.evolve(schema=SCHEMA.schema["properties"]["tool_events"]).validate(archived["tool_events"])


def test_balanced_orders_and_missing_usage_gate(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from study22 import ARMS, arm_order, binomial_upper, usage_row

    orders = [arm_order(i) for i in range(4)]
    assert all(set(column) == set(ARMS) for column in zip(*orders))
    assert len({(a, b) for order in orders for a, b in zip(order, order[1:])}) == 12
    assert binomial_upper(0, 40) < .10
    assert usage_row([], 3)["total_tokens_per_verified"] is None


def test_checkpoint_draft_distinguishes_screening_from_comparisons(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from build_study22_paper import checkpoint_evidence
    from replication22 import verify_checkpoints

    registered = json.loads((ROOT / "eval/study22-tasks.json").read_bytes())
    plan = {"cases": registered["tasks"], "source_revision": registered["source_revision"],
            "natural_trigger": True, "require_behavioral_check": True, "compare_questions": True}
    (tmp_path / "pilot-plan.json").write_text(json.dumps(plan), encoding="utf-8")
    stopped, eligible = [tmp_path / task for task in plan["cases"][:2]]
    stopped.mkdir()
    eligible.mkdir()
    (stopped / "result.json").write_text(json.dumps({
        "status": "prefix_failed", "reason": "no_eligible_trigger",
        "error": "Task reached its tool-call limit.", "accepted": True,
    }), encoding="utf-8")
    order = ["luna_only", "luna_advice", "luna_generic_question", "astra_takeover"]
    (eligible / "checkpoint-summary.json").write_text(json.dumps({"order": order}), encoding="utf-8")
    for arm, accepted, wall in (("luna_advice", True, 30), ("astra_takeover", True, None),
                                ("luna_generic_question", True, 61)):
        folder = eligible / arm
        folder.mkdir()
        (folder / "result.json").write_text(json.dumps({"accepted": accepted, "wall_seconds": wall,
                                                        "consultations": []}), encoding="utf-8")
    (stopped / "prefix-checks.json").write_text(json.dumps([
        {"command": "git diff --check HEAD", "passed": True, "exit_code": 0, "trigger": None},
        {"command": "pytest -q", "passed": False, "exit_code": 124, "trigger": None},
        {"command": "pytest -x", "passed": False, "exit_code": 1, "trigger": None},
    ]), encoding="utf-8")
    (eligible / "prefix-checks.json").write_text(json.dumps([
        {"command": "pytest -q tests/test_repair.py", "passed": False, "exit_code": 1, "trigger": None},
        {"command": "pytest -q tests/test_repair.py", "passed": False, "exit_code": 1, "trigger": "two_failed_checks"}
    ]), encoding="utf-8")
    model = "gpt-6-luna"
    prefix = [
        {"attempt_id": "first", "event": "dispatched", "requested_model": model},
        {"attempt_id": "first", "event": "finished", "requested_model": model,
         "model": model, "status": "completed", "usage": {"input_tokens": 120, "output_tokens": 30,
                                                             "cached_input_tokens": 60,
                                                             "cache_write_input_tokens": 0}},
        {"attempt_id": "second", "event": "dispatched", "requested_model": model},
    ]
    (stopped / "prefix.jsonl").write_text("".join(json.dumps(row) + "\n" for row in prefix), encoding="utf-8")

    audit, note = checkpoint_evidence(tmp_path, deadline=60)
    assert audit["summary"]["eligible_checkpoint_tasks"] == [eligible.name]
    assert f"covers 2 of {len(plan['cases'])} planned issues" in note
    assert "1 matched question-format pairs" in note
    assert "were 0/1, 1/1, 0/1, 0/1" in note
    verify_checkpoints(audit, registered, deadline=60)
    assert audit["summary"]["screening_check_counts"]["with_nondefault_checks"] == 2
    assert audit["summary"]["screening_check_counts"]["with_nondefault_exit_124"] == 1
    assert audit["summary"]["screening_check_counts"]["with_nondefault_triggers"] == 1
    assert audit["summary"]["screening_check_counts"]["with_distinct_failed_nondefault_commands"] == 1
    previous_archive = json.loads(json.dumps(audit))
    del previous_archive["summary"]["screening_check_counts"]["with_distinct_failed_nondefault_commands"]
    for events in previous_archive["summary"]["screening_checks"].values():
        for event in events:
            del event["command_id"]
    verify_checkpoints(previous_archive, registered, deadline=60)
    assert audit["summary"]["screening_diagnostics"]["stop_categories"] == {"tool_allowance": 1}
    assert audit["summary"]["screening_diagnostics"]["verified_prefix_failures"] == 1
    usage = audit["summary"]["screening_prefix_usage"]
    assert (usage["requests_observed"], usage["token_receipts"], usage["observed_tokens"]) == (2, 1, 150)
    assert usage["unresolved_dispatches"] == 1 and usage["observed_cache_read_tokens"] == 60
    usage["observed_tokens"] += 1
    with pytest.raises(ValueError, match="screening usage disagrees"):
        verify_checkpoints(audit, registered, deadline=60)
    usage["observed_tokens"] -= 1
    outcomes = audit["summary"]["assignment_outcomes"]
    assert outcomes["arms"]["astra_takeover"]["missing_deadline_outcomes"] == 1
    assert outcomes["arms"]["luna_generic_question"]["missing_deadline_outcomes"] == 0
    assert outcomes["focused_deadline_difference_bounds"]["luna_only"] == {"lower": 0, "upper": 1}
    assert outcomes["focused_deadline_difference_bounds"]["luna_generic_question"] == {"lower": 1, "upper": 1}
    outcomes["arms"]["luna_only"]["missing_deadline_outcomes"] = 0
    with pytest.raises(ValueError, match="assignment outcomes disagree"):
        verify_checkpoints(audit, registered, deadline=60)
    outcomes["arms"]["luna_only"]["missing_deadline_outcomes"] = 1
    audit["summary"]["complete_tasks"] = [eligible.name]
    with pytest.raises(ValueError, match="summary disagrees with the case-level records"):
        verify_checkpoints(audit, registered, deadline=60)


def test_prefix_timeout_timing_keeps_the_original_stop_category(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from checkpoint_report import prefix_elapsed_seconds, screening_timeout_timing

    journal = tmp_path / "prefix.jsonl"
    events = [
        {"event": "dispatched", "timestamp_utc": "2026-11-21T23:59:00+00:00"},
        {"event": "finished", "timestamp_utc": "2026-11-22T00:03:00.25+00:00"},
    ]
    journal.write_text("\n".join(json.dumps(event) for event in events), encoding="utf-8")
    elapsed = prefix_elapsed_seconds(journal)
    assert elapsed == 240.25
    dispositions = {"late": {"stop_category": "transport_timeout"},
                    "early": {"stop_category": "transport_timeout"},
                    "missing": {"stop_category": "transport_timeout"},
                    "other": {"stop_category": "tls_error"}}
    offsets = {"late": elapsed, "early": 127.0}
    assert screening_timeout_timing(dispositions, offsets, 240) == {
        "elapsed_by_task": offsets, "recorded_timeouts": 3, "at_or_after_budget": 1,
        "range_seconds": [127.0, 240.25],
    }
    with pytest.raises(ValueError, match="Invalid timing"):
        screening_timeout_timing(dispositions, {"other": elapsed}, 240)
    journal.write_text(json.dumps(events[0]), encoding="utf-8")
    assert prefix_elapsed_seconds(journal) is None


def test_peer_cost_and_tokens_use_common_coverage_and_charge_failed_attempts(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from peer_report22 import matched_peer_metrics

    rows = {
        "both-priced": {"peer_pass": True, "spot_22_pass": True,
                        "wall_seconds": 60, "spot_22_wall_seconds": 45,
                        "usage": {"tokens": 150, "api_usd": 0.03},
                        "spot_22_usage": {"tokens": 70, "api_usd": 0.02}},
        "peer-failed-and-unpriced": {"peer_pass": False, "spot_22_pass": True,
                                     "wall_seconds": 120, "spot_22_wall_seconds": 80,
                                     "usage": {"tokens": 100, "api_usd": None},
                                     "spot_22_usage": {"tokens": 40, "api_usd": 0.01}},
        "unmatched-peer": {"peer_pass": True, "spot_22_pass": None,
                           "usage": {"tokens": 900, "api_usd": 0.5}, "spot_22_usage": None},
    }
    summary = matched_peer_metrics(rows)
    assert summary["peer"] == {"verified": 1, "median_seconds_to_grade": 90}
    assert summary["spot_22"]["verified"] == 2
    assert summary["joint_pass_timing"]["task_ids"] == ["both-priced"]
    assert summary["joint_pass_timing"]["spot_22_earlier"] == 1
    assert summary["deadline_wait"]["peer"] == {"verified_by_deadline": 1, "mean_wait_seconds": 480}
    assert summary["deadline_wait"]["spot_22"] == {"verified_by_deadline": 2, "mean_wait_seconds": 62.5}
    assert summary["tokens"]["peer"]["per_verified"] == 250
    assert summary["tokens"]["spot_22"]["per_verified"] == 55
    assert summary["api_usd"]["task_ids"] == ["both-priced"]
    assert summary["api_usd"]["spot_22"]["per_verified"] == 0.02


def test_optimistic_quality_keeps_known_outcomes_fixed(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from replication22 import optimistic_quality_counts

    outcomes = [(True, False), (False, True), (None, True), (False, None), (None, None)]
    manifest = {"tasks": [{"id": str(i)} for i in range(len(outcomes))]}
    records = {str(i): {key: {"accepted": result} for key, result in
                        zip(("spot_advisor_22", "spot_advisor"), pair) if result is not None}
               for i, pair in enumerate(outcomes)}
    assert optimistic_quality_counts(manifest, records) == {
        "possible_wins": 2, "recorded_losses": 1, "missing_pairs": 3,
    }


def test_timed_repair_cost_counts_work_on_late_and_failed_repairs(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from cost_report22 import LABELS, matched_resource_audit
    from report import ASTRA, BILLING, estimate

    receipt = {"model": ASTRA, "usage": {"input_tokens": 40, "output_tokens": 30,
                                       "cached_input_tokens": 15, "cache_write_input_tokens": 0}}
    rows = {
        case: {arm: {"accepted": passed, "wall_seconds": seconds, "reroutes": [], "consultations": [],
                     "executor_attempts": [receipt]}
               for arm in LABELS}
        for case, passed, seconds in (("timely", True, 300), ("late", True, 301), ("failed", False, 20))
    }
    result = matched_resource_audit(rows, BILLING)["arms"]["spot_advisor_22"]["delivery_by_review_seconds"]
    assert result["300"] == {"verified": 1, "tokens_per_timely_verification": 210,
                             "estimated_usd_per_timely_verification": pytest.approx(3 * estimate(receipt))}
    assert result["600"]["verified"] == 2 and result["600"]["tokens_per_timely_verification"] == 105


def test_visual_review_binds_anonymous_choices_to_frozen_images(monkeypatch, tmp_path):
    import hashlib

    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from visual_ratings22 import collect_review

    arms = ["luna_only", "luna_text_advice", "luna_image_advice", "astra_only", "luna_with_image"]
    key, row = {}, {}
    for i, arm in enumerate(arms, 1):
        name = f"view-{i:016x}.png"
        content = arm.encode()
        panel = tmp_path / f"panel-{i}"
        panel.mkdir()
        (panel / name).write_bytes(content)
        key[name] = {"panel": i, "case": "test-case", "arm": arm}
        row[arm] = {"snapshot_sha256": hashlib.sha256(content).hexdigest()}
    visual = {"cases": {"test-case": {arm: row[arm] for arm in arms[:-1]}},
              "supplemental_image_control": {"cases": {"test-case": row["luna_with_image"]}}}
    (tmp_path / "private-key.json").write_text(json.dumps({"files": key}), encoding="utf-8")
    response = tmp_path / "response.json"
    name = next(iter(key))
    response.write_text(json.dumps({"reviewer": "private alias", "panel": 1, "choices": {name: "trial"}}),
                        encoding="utf-8")
    review = collect_review(tmp_path, [response], visual)
    assert review["responses"] == [{"reviewer": "r1", "panel": 1, "choices": {name: "trial"}}]
    assert review["tally"]["first_click_counts"]["test-case"]["luna_only"] == {"trial": 1}
    assert not review["tally"]["balanced_panel_coverage"]
    (tmp_path / "panel-1" / name).write_bytes(b"different screenshot")
    with pytest.raises(ValueError, match="panels differ from the frozen"):
        collect_review(tmp_path, [response], visual)


def test_replication_rejects_a_peer_summary_inconsistent_with_its_rows(monkeypatch):
    import hashlib

    monkeypatch.syspath_prepend(str(ROOT / "eval"))
    from peer_report22 import PEERS, summarize_peer
    from replication22 import verify_peers

    cases = ["issue"]
    plan = json.dumps({"peer_tasks": cases}).encode()
    input_row = {"state": "graded", "peer_pass": True, "wall_seconds": 40, "usage": None,
                 "spot_22_pass": False, "spot_22_wall_seconds": 30,
                 "peer_minus_spot_22": 1, "spot_22_usage": None}
    by_task = {"issue": {"spot_advisor_22": {"accepted": False, "wall_seconds": 30, "executor_attempts": []}}}
    audit = {"assignment_sha256": hashlib.sha256(plan).hexdigest(), "peers": {
        peer: {"label": label, **summarize_peer(cases, {"issue": input_row}, {})}
        for peer, label in PEERS.items()
    }}
    verify_peers(audit, plan, by_task)
    audit["peers"]["pal-mcp"]["matched_metrics"]["peer"]["verified"] = 0
    with pytest.raises(ValueError, match="case-level records: pal-mcp"):
        verify_peers(audit, plan, by_task)
