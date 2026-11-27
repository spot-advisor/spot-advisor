"""Offline regressions for the simplified Advisor caller (skills/advisor/scripts/ask_expert.py)."""

from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest


ROOT = Path(__file__).resolve().parents[1]
CALLER = ROOT / "skills" / "advisor" / "scripts" / "ask_expert.py"

spec = importlib.util.spec_from_file_location("ask_expert", CALLER)
assert spec is not None and spec.loader is not None
ask_expert = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ask_expert)


def _fake_response(payload: dict | str):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    buf = io.BytesIO(raw.encode("utf-8"))

    class _Resp:
        def __enter__(self):
            return buf

        def __exit__(self, *args):
            buf.close()

    return _Resp()


def test_instructions_enforce_four_line_turn_format():
    for label in ("Verdict:", "Next:", "Prompt:", "Risk:"):
        assert label in ask_expert.ADVISOR_INSTRUCTION
        assert label in ask_expert.VISUAL_INSTRUCTION
    assert ask_expert.MAX_PACKET_CHARS == 4_000


def test_read_packet_rejects_empty_and_bloated_packets(tmp_path):
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("   \n", encoding="utf-8")
    with pytest.raises(SystemExit, match="must not be empty"):
        ask_expert.read_packet(SimpleNamespace(question=None, input_file=empty_file))

    bloated = "x" * (ask_expert.MAX_PACKET_CHARS + 1)
    with pytest.raises(SystemExit, match="limit is 4000"):
        ask_expert.read_packet(SimpleNamespace(question=bloated, input_file=None))

    ok = ask_expert.read_packet(SimpleNamespace(question="  Decision: X\nQuestion: Y?  ", input_file=None))
    assert ok == "Decision: X\nQuestion: Y?"


def test_gateway_happy_path_and_token_limit(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout=75.0):
        calls.append(req.full_url)
        if req.full_url.endswith("/responses/input_tokens"):
            return _fake_response({"input_tokens": 120})
        return _fake_response({
            "id": "req_123",
            "model": "gpt-6.2-astra",
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": "Verdict: Use a lock.\nNext: Add singleton.\nPrompt: Implement singleton.\nRisk: none"},
            }],
            "usage": {"prompt_tokens": 120, "completion_tokens": 25},
        })

    monkeypatch.setattr(ask_expert, "urlopen", fake_urlopen)
    monkeypatch.setenv("EXPERTS_API_KEY", "sk-secret-test")
    args = SimpleNamespace(base_url="https://gateway.example.test/v1", timeout_seconds=10.0, image_parts=[])
    result = ask_expert.call_gateway(args, "gpt-6.2-astra", [{"role": "user", "content": "hi"}])

    assert calls == [
        "https://gateway.example.test/v1/responses/input_tokens",
        "https://gateway.example.test/v1/chat/completions",
    ]
    assert result["transport"] == "gateway"
    assert result["input_tokens"] == 120
    assert "Verdict:" in result["advice"]

    # Exceeding MAX_INPUT_TOKENS aborts before chat/completions
    calls.clear()
    monkeypatch.setattr(
        ask_expert,
        "urlopen",
        lambda req, timeout=75.0: _fake_response({"input_tokens": ask_expert.MAX_INPUT_TOKENS + 1}),
    )
    with pytest.raises(RuntimeError, match="no model request was sent"):
        ask_expert.call_gateway(args, "gpt-6.2-astra", [{"role": "user", "content": "hi"}])


def test_gateway_401_redacts_api_key(monkeypatch):
    def fake_urlopen(req, timeout=75.0):
        raise HTTPError(req.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad key sk-secret-xyz"))

    monkeypatch.setattr(ask_expert, "urlopen", fake_urlopen)
    monkeypatch.setenv("EXPERTS_API_KEY", "sk-secret-xyz")
    args = SimpleNamespace(base_url="https://gateway.example.test/v1", timeout_seconds=10.0, image_parts=[])
    with pytest.raises(RuntimeError, match="HTTP 401") as exc_info:
        ask_expert.call_gateway(args, "gpt-6.2-astra", [{"role": "user", "content": "hi"}])
    assert "sk-secret-xyz" not in str(exc_info.value)


def test_consult_codex_sse_stream(monkeypatch, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "tok-1", "account_id": "acc-1"}}), encoding="utf-8")
    monkeypatch.setenv("CODEX_AUTH_FILE", str(auth))

    sse = (
        'data: {"type":"response.output_text.delta","delta":"Verdict: Keep it simple.\\nNext: Run pytest.\\nPrompt: Run pytest now.\\nRisk: none"}\n\n'
        'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-6.2-astra","status":"completed","usage":{"input_tokens":50,"output_tokens":20,"total_tokens":70}}}\n\n'
        "data: [DONE]\n\n"
    )
    monkeypatch.setattr(ask_expert, "urlopen", lambda req, timeout=75.0: _fake_response(sse))

    args = SimpleNamespace(model="gpt-6.2-astra", timeout_seconds=10.0)
    res = ask_expert.consult_codex(args, [{"role": "user", "content": "hi"}], fallback_reason="gateway down")
    assert res["transport"] == "codex"
    assert res["fallback_reason"] == "gateway down"
    assert res["input_tokens"] == 50
    assert "Prompt:" in res["advice"]


@pytest.mark.parametrize("protocol", ["v1", "v2"])
def test_consult_claude_cli(monkeypatch, tmp_path, protocol):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("Unrelated fixture", encoding="utf-8")
    monkeypatch.setattr(ask_expert.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)

    recorded = {}

    def fake_run(cmd, input, capture_output, text, encoding, timeout, check, cwd):
        recorded["cmd"] = cmd
        recorded["input"] = input
        assert Path(cwd) != tmp_path
        assert not (Path(cwd) / "CLAUDE.md").exists()
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps({
                "is_error": False,
                "result": "Verdict: Use a lock.\nNext: Add lock.\nPrompt: Add lock.\nRisk: none",
                "session_id": "sess_1",
                "usage": {"input_tokens": 40, "output_tokens": 15},
            }),
            stderr="",
        )

    monkeypatch.setattr(ask_expert.subprocess, "run", fake_run)
    args = SimpleNamespace(model="gpt-6.2-astra", timeout_seconds=10.0, protocol=protocol, effort="medium")
    res = ask_expert.consult_claude(args, "SYS", "PKT")

    assert res["transport"] == "claude"
    assert res["model"] == ("claude-cli-default" if protocol == "v1" else None)
    assert "--no-session-persistence" in recorded["cmd"]
    assert "--tools" in recorded["cmd"]
    if protocol == "v2":
        assert recorded["input"].endswith("\n\nPKT")
        assert "Verdict:, Do:, Verify:, and Risk:" in recorded["input"]
    else:
        assert recorded["input"] == "PKT"
    assert "Verdict:" in res["advice"]


def test_auto_transport_falls_back_to_claude_when_no_gateway_or_codex_auth(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CODEX_AUTH_FILE", str(tmp_path / "missing-auth.json"))
    monkeypatch.setattr(ask_expert.shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None)
    monkeypatch.setattr(
        ask_expert.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=0,
            stdout=json.dumps({"is_error": False, "result": "Verdict: OK.\nNext: Go.\nPrompt: Go.\nRisk: none"}),
            stderr="",
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["ask_expert.py", "--question", "Decision: Test fallback?", "--base-url", ""],
    )
    rc = ask_expert.main()
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["transport"] == "claude"
    assert out["fallback_reason"] is not None


def test_fallback_keeps_one_consultation_identity(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CODEX_AUTH_FILE", str(tmp_path / "missing-auth.json"))
    monkeypatch.setattr(ask_expert.shutil, "which", lambda name: "claude")

    def unavailable(*args):
        raise ask_expert.RouteUnavailable("Gateway unavailable before generation")

    monkeypatch.setattr(ask_expert, "call_gateway", unavailable)
    monkeypatch.setattr(ask_expert, "consult_claude", lambda *args: {
        "transport": "claude", "advice": "Verdict: Proceed.",
    })
    path = tmp_path / "receipts.jsonl"
    monkeypatch.setattr(sys, "argv", [
        "ask_expert.py", "--question", "What should I check?", "--base-url",
        "http://localhost:4444/v1", "--receipt-file", str(path),
    ])
    assert ask_expert.main() == 0
    result = json.loads(capsys.readouterr().out)
    first, second = result["attempts"]
    assert not first["dispatch_possible"] and second["dispatch_possible"]
    assert first["attempt_id"] != second["attempt_id"]
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert {event["consultation_id"] for event in [*events, first, second]} == {result["consultation_id"]}


def test_direct_cli_transports_reject_images(monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["ask_expert.py", "--transport", "claude", "--question", "Check UI", "--image", "a.png"],
    )
    with pytest.raises(SystemExit, match="text packets only"):
        ask_expert.main()


def test_compact_packet_trims_whitespace_and_blank_runs():
    raw = "Line 1   \n\n\n\nLine 2\t\n\nLine 3"
    assert ask_expert.compact_packet(raw) == "Line 1\n\nLine 2\n\nLine 3"


def test_session_file_delta_context_and_round_budget(monkeypatch, capsys, tmp_path):
    session_file = tmp_path / "advisor-session.json"
    seen_packets = []

    def fake_claude(args, instruction, packet, fallback_reason=None):
        seen_packets.append(packet)
        return {
            "model": "claude-cli-default",
            "requested_model": args.model,
            "transport": "claude",
            "advice": f"Verdict: Round {len(seen_packets)} answer.\nNext: Step.\nPrompt: Do step.\nRisk: none",
            "advice_words": 10,
            "usage": {"input_tokens": 50, "output_tokens": 12},
            "request_id": f"s_{len(seen_packets)}",
            "elapsed_seconds": 0.1,
        }

    monkeypatch.setattr(ask_expert, "consult_claude", fake_claude)

    monkeypatch.setattr(
        sys,
        "argv",
        ["ask_expert.py", "--transport", "claude", "--session-file", str(session_file),
         "--max-rounds", "2", "--question", "Decision: R1 question?"],
    )
    assert ask_expert.main() == 0
    out1 = json.loads(capsys.readouterr().out)
    assert out1["round"] == 1
    assert out1["max_rounds"] == 2
    assert out1["cumulative_tokens"] == {"input_tokens": 50, "output_tokens": 12}

    monkeypatch.setattr(
        sys,
        "argv",
        ["ask_expert.py", "--transport", "claude", "--session-file", str(session_file),
         "--max-rounds", "2", "--question", "Delta: Ran step, saw X."],
    )
    assert ask_expert.main() == 0
    out2 = json.loads(capsys.readouterr().out)
    assert out2["round"] == 2
    assert out2["cumulative_tokens"] == {"input_tokens": 100, "output_tokens": 24}
    assert seen_packets[1].startswith("Prior Verdict: Round 1 answer.\n\nDelta: Ran step, saw X.")

    monkeypatch.setattr(
        sys,
        "argv",
        ["ask_expert.py", "--transport", "claude", "--session-file", str(session_file),
         "--max-rounds", "2", "--question", "Should not run"],
    )
    assert ask_expert.main() == 1
    out3 = json.loads(capsys.readouterr().out)
    assert out3["status"] == "budget_exhausted"
    assert len(seen_packets) == 2


def test_auto_transport_prefers_direct_codex_first(monkeypatch, capsys, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "tok-1", "account_id": "acc-1"}}), encoding="utf-8")
    monkeypatch.setenv("CODEX_AUTH_FILE", str(auth))

    sse = (
        'data: {"type":"response.output_text.delta","delta":"Verdict: Direct first.\\nNext: Go.\\nPrompt: Go.\\nRisk: none"}\n\n'
        'data: {"type":"response.completed","response":{"id":"resp_1","model":"gpt-6.2-astra","status":"completed","usage":{"input_tokens":40,"output_tokens":15,"total_tokens":55}}}\n\n'
        "data: [DONE]\n\n"
    )
    monkeypatch.setattr(ask_expert, "urlopen", lambda req, timeout=75.0: _fake_response(sse))
    monkeypatch.setattr(
        ask_expert,
        "call_gateway",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("gateway should not be called first")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["ask_expert.py", "--question", "Decision: Test direct #1?", "--base-url", "https://gateway.example.test/v1"],
    )
    assert ask_expert.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["transport"] == "codex"
    assert out["fallback_reason"] is None


def _codex_sse(advice, usage=None):
    events = [
        {"type": "response.output_text.delta", "delta": advice},
        {"type": "response.completed",
         "response": {"status": "completed", "id": "fixture_1", "model": "gpt-6.2-astra", "usage": usage}},
    ]
    return _fake_response("".join(f"data: {json.dumps(event)}\n\n" for event in events))


def test_completed_advice_without_usage_keeps_one_generation(monkeypatch, capsys, tmp_path):
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"tokens": {"access_token": "tok", "account_id": "acct"}}), encoding="utf-8")
    monkeypatch.setenv("CODEX_AUTH_FILE", str(auth))
    session = tmp_path / "session.json"
    calls = []

    def send(request, timeout):
        calls.append(request.full_url)
        return _codex_sse("Verdict: Valid.\nNext: Go.\nPrompt: Go.\nRisk: none")

    monkeypatch.setattr(ask_expert, "urlopen", send)
    argv = ["ask_expert.py", "--session-file", str(session), "--base-url", "http://localhost:4444/v1",
            "--question", "Should it continue?"]
    monkeypatch.setattr(sys, "argv", [*argv, "--max-rounds", "1"])
    assert ask_expert.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert len(calls) == 1
    assert out["advice"].startswith("Verdict:")
    assert out["usage_normalized"]["input_tokens"] is None
    assert out["cumulative_tokens"]["output_tokens"] is None
    journal = session.with_name(session.name + ".attempts.jsonl")
    assert [json.loads(line)["event"] for line in journal.read_text(encoding="utf-8").splitlines()] == [
        "started", "dispatch", "finished",
    ]

    monkeypatch.setattr(sys, "argv", argv)
    assert ask_expert.main() == 1
    assert json.loads(capsys.readouterr().out)["status"] == "budget_exhausted"
    assert len(calls) == 1


def test_timeout_after_dispatch_consumes_round_without_fallback(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("CODEX_AUTH_FILE", str(tmp_path / "auth.json"))
    (tmp_path / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "tok", "account_id": "acct"}}), encoding="utf-8",
    )
    calls = []

    def timeout(request, timeout):
        calls.append(request.full_url)
        raise TimeoutError()

    monkeypatch.setattr(ask_expert, "urlopen", timeout)
    monkeypatch.setattr(sys, "argv", [
        "ask_expert.py", "--question", "Timeout fixture", "--base-url", "http://localhost:4444/v1",
        "--session-file", str(tmp_path / "session.json"), "--max-rounds", "1",
    ])
    assert ask_expert.main() == 1
    out = json.loads(capsys.readouterr().out)
    assert len(calls) == 1
    assert out["round"] == out["unknown_usage_rounds"] == 1
    assert out["attempts"][0]["dispatch_possible"]


def test_gateway_uses_provider_usage_instead_of_preflight(monkeypatch):
    def send(req, timeout):
        if req.full_url.endswith("input_tokens"):
            return _fake_response({"input_tokens": 80})
        return _fake_response({
            "choices": [{"message": {"content": "Advice"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 110, "completion_tokens": 10},
        })

    monkeypatch.setattr(ask_expert, "urlopen", send)
    args = SimpleNamespace(base_url="http://localhost:4444/v1", timeout_seconds=10, image_parts=[])
    out = ask_expert.call_gateway(args, "gpt-6.2-astra", [{"role": "user", "content": "Test"}])
    assert out["estimated_input_tokens"] == 80
    assert out["input_tokens"] == 110
    assert out["model"] is None


def test_gateway_detects_substitution_for_an_explicit_v2_model(monkeypatch):
    def send(req, timeout):
        if req.full_url.endswith("input_tokens"):
            return _fake_response({"input_tokens": 8})
        return _fake_response({"model": "different-model", "id": "reply",
                               "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    monkeypatch.setattr(ask_expert, "urlopen", send)
    args = SimpleNamespace(base_url="http://localhost:4444/v1", timeout_seconds=10, image_parts=[], protocol="v2")
    with pytest.raises(ask_expert.GenerationError) as err:
        ask_expert.call_gateway(args, "gpt-6.2-astra", [{"role": "user", "content": "Question"}])
    assert err.value.receipt["model"] == "different-model" and err.value.receipt["request_id"] == "reply"


def test_status_on_new_session_has_no_files_or_provider_calls(monkeypatch, tmp_path, capsys):
    path = tmp_path / "new" / "session.json"
    monkeypatch.setattr(sys, "argv", ["advisor", "--session-file", str(path), "--status"])
    monkeypatch.setattr(ask_expert, "urlopen", lambda *a, **kw: pytest.fail("unexpected provider call"))
    assert ask_expert.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert not result["session_exists"] and not result["dispatch_possible"] and not result["provider_receipt"]
    assert not path.parent.exists()


@pytest.mark.parametrize(("advice", "change_revision", "expected"), [
    ("Verdict: Fix the guard.\nDo: Adjust the predicate.\nVerify: Run the disk test.\nRisk: none", False, "ok"),
    ("Verdict: Fix the guard.\nDo: Adjust the predicate.\nVerify: Run the disk test.\nRisk: none", True, "stale"),
    ("Verdict: This intentionally omits Verify.\nDo: Edit.\nRisk: none", False, "invalid_advice"),
])
def test_v2_checks_reply_and_revision_without_retry(monkeypatch, capsys, tmp_path, advice, change_revision, expected):
    auth_file = tmp_path / "auth.json"
    auth_file.write_text(json.dumps({"tokens": {"access_token": "tok", "account_id": "acct"}}), encoding="utf-8")
    monkeypatch.setenv("CODEX_AUTH_FILE", str(auth_file))
    state_file = tmp_path / "state.json"
    state = {"task_id": "sample", "goal": "Fix disk check", "constraints": ["Keep signatures"],
             "phase": "verify", "revision": "r1", "acceptance": ["disk test passes"], "evidence": ["disk test failed"]}
    state_file.write_text(json.dumps(state), encoding="utf-8")
    calls = []

    def send(request, timeout):
        payload = json.loads(request.data)
        calls.append(payload)
        if change_revision:
            state_file.write_text(json.dumps({**state, "revision": "r2"}), encoding="utf-8")
        return _codex_sse(advice, {"input_tokens": 60, "output_tokens": 20})

    monkeypatch.setattr(ask_expert, "urlopen", send)
    monkeypatch.setattr(sys, "argv", [
        "ask_expert.py", "--transport", "codex", "--state-file", str(state_file),
        "--session-file", str(tmp_path / "session.json"), "--question", "Which predicate should change?",
    ])
    assert ask_expert.main() == (0 if expected == "ok" else 1)
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == expected
    if expected == "stale":
        assert result.get("advice_fields") is None and not result.get("advice")
    assert result["usage_normalized"]["input_tokens"] == 60
    assert len(calls) == 1
    assert calls[0]["reasoning"]["effort"] == "medium"
    assert calls[0]["tools"] == []
    packet = calls[0]["input"][0]["content"]
    assert "Keep signatures" in packet and "disk test failed" in packet


def test_delta_pack_keeps_constraints_and_bounds_evidence():
    state = {"goal": "Task", "phase": "Verifier", "constraints": ["Keep API"],
             "acceptance": ["Run tests"], "evidence": ["Observed: " + "x" * 4000],
             "last_advice_outcome": "The last change still failed."}
    args = SimpleNamespace(snippet=[], diff=False, error_tail=None)
    packet = ask_expert.v2_packet(args, state, {"round": 1, "last_verdict": ""}, "Next step?")
    assert len(packet) <= ask_expert.MAX_DELTA_PACKET_CHARS
    assert "Keep API" in packet and "Run tests" in packet and "[excerpt cut]" in packet


def test_preview_and_dispatch_use_identical_followup_without_spending_a_preview_round(monkeypatch, tmp_path, capsys):
    state = {"task_id": "cache", "revision": "r2", "phase": "Reproduced the stale read",
             "goal": "Fix expiry against the caller's clock. " * 15,
             "constraints": ["Keep the existing API.", "Keep the time zone."],
             "acceptance": ["The boundary test passes."],
             "last_advice_outcome": "The boundary case still fails.",
             "evidence": ["expected None " + "cached " * 120 + "at expiry=0"],
             "decision_focus": "Expire at the supplied deadline."}
    state_file, session = tmp_path / "state.json", tmp_path / "session.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    args = ["advisor", "--protocol", "v2", "--state-file", str(state_file), "--session-file", str(session),
            "--transport", "codex", "--question", "Which check handles the boundary?"]
    monkeypatch.setattr(sys, "argv", [*args, "--preview"])
    assert ask_expert.main() == 0
    assert json.loads(capsys.readouterr().out)["packet_limit"] == 4000
    assert set(tmp_path.iterdir()) == {state_file}
    session.write_text(json.dumps({"round": 1, "task_id": "cache"}), encoding="utf-8")
    before = session.read_bytes()
    assert ask_expert.main() == 0
    preview = json.loads(capsys.readouterr().out)
    assert session.read_bytes() == before and set(tmp_path.iterdir()) == {state_file, session}
    packet = preview["packet"]
    assert preview["round"] == 1 and preview["packet_limit"] == 1000 and len(packet) <= 1000
    for text in [*state["constraints"], *state["acceptance"], state["last_advice_outcome"],
                 "expected None", "at expiry=0", args[-1]]:
        assert text in packet
    observed = []
    monkeypatch.setattr(ask_expert, "read_codex_credentials", lambda: ("tok", "acct"))
    def reply(req, timeout):
        observed.append(json.loads(req.data)["input"][0]["content"])
        return _codex_sse("Verdict: Expire on equality.\nDo: Fix the guard.\nVerify: Run the boundary test.\nRisk: None",
                          {"input_tokens": 200, "output_tokens": 30})
    monkeypatch.setattr(ask_expert, "urlopen", reply)
    monkeypatch.setattr(sys, "argv", args)
    assert ask_expert.main() == 0
    assert json.loads(capsys.readouterr().out)["round"] == 2
    assert observed == [packet]


def test_oversize_required_state_fails_locally_with_recovery(monkeypatch, tmp_path, capsys):
    state = {"task_id": "long", "revision": "2", "goal": "a" * 1100, "phase": "test",
             "constraints": ["Preserve stable keys"], "acceptance": ["Original data reads successfully"],
             "evidence": ["A verified mismatch at offset 0"]}
    state_file, session = tmp_path / "state.json", tmp_path / "session.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    session.write_text(json.dumps({"round": 1}), encoding="utf-8")
    before = session.read_bytes()
    monkeypatch.setattr(sys, "argv", ["advisor", "--protocol", "v2", "--state-file", str(state_file),
                                     "--session-file", str(session), "--question", "Which key?"])
    assert ask_expert.main() == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure["status"] == "packet_too_large" and failure["attempts"] == []
    assert session.read_bytes() == before and not list(tmp_path.glob("*.jsonl"))
    state_file.write_text(json.dumps({**state, "decision_focus": "Repair the key lookup"}), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [*sys.argv, "--preview"])
    assert ask_expert.main() == 0
    fixed = json.loads(capsys.readouterr().out)["packet"]
    assert all(text in fixed for text in ("Repair the key lookup", *state["constraints"], *state["acceptance"]))


@pytest.mark.parametrize("finished", [False, True])
def test_interrupted_dispatch_is_never_repeated(monkeypatch, tmp_path, capsys, finished):
    session = tmp_path / "session.json"
    journal = tmp_path / "custom.jsonl"
    session.write_text(json.dumps({"task_id": "cache", "round": 1, "unknown_usage_rounds": 1,
                                   "pending": "reserved", "journal": str(journal)}), encoding="utf-8")
    event = {"event": "finished", "task_id": "cache", "attempt_id": "reserved", "dispatch_possible": True,
             "consultation_id": "related-consultation",
             "model": "gpt-6.2-astra", "usage": {"input_tokens": 12, "output_tokens": 3}}
    journal.write_bytes(((json.dumps(event) + "\n") * 2).encode() if finished else b'{"event":')
    before = {p: p.read_bytes() for p in tmp_path.iterdir()}
    monkeypatch.setattr(ask_expert, "route_consultation", lambda *a: pytest.fail("duplicate dispatch"))
    monkeypatch.setattr(sys, "argv", ["advisor", "--session-file", str(session), "--status"])
    assert ask_expert.main() == 0
    status = json.loads(capsys.readouterr().out)
    assert before == {p: p.read_bytes() for p in tmp_path.iterdir()}
    assert status["provider_receipt"] is finished
    assert status["remaining_rounds"] == 2 and status["dispatch_possible"]
    monkeypatch.setattr(sys, "argv", ["advisor", "--session-file", str(session), "--question", "Retry?"])
    assert ask_expert.main() == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == ("recovered_receipt" if finished else "dispatch_unresolved")
    assert result["round"] == 1
    assert result["unknown_usage_rounds"] == (0 if finished else 1)
    assert result["cumulative_tokens"]["input_tokens"] == (12 if finished else None)
    if finished:
        assert result["consultation_id"] == "related-consultation"
        monkeypatch.setattr(sys, "argv", ["advisor", "--session-file", str(session), "--status"])
        assert ask_expert.main() == 0
        after = json.loads(capsys.readouterr().out)
        assert after["pending_attempt"] is None
        assert after["cumulative_tokens"] == {"input_tokens": 12, "output_tokens": 3}


def test_cli_reopen_after_process_death_does_not_send_again(monkeypatch, tmp_path):
    dispatched, release = threading.Event(), threading.Event()
    generations = []

    class Gateway(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/v1/responses/input_tokens":
                body = b'{"input_tokens":180}'
            elif self.path == "/v1/chat/completions":
                generations.append(self.path)
                dispatched.set()
                release.wait(15)
                body = b"{}"
            else:
                self.send_error(404)
                return
            try:
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The test deliberately terminates the requesting process.

    monkeypatch.setenv("EXPERTS_API_KEY", "loopback-test")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    state, session = tmp_path / "state.json", tmp_path / "session.json"
    state.write_text(json.dumps({"task_id": "override", "revision": "1", "goal": "Preserve False",
                                 "phase": "decision", "constraints": ["Keep the API"],
                                 "acceptance": ["False survives"], "evidence": ["False now returns the default"]}),
                     encoding="utf-8")
    caller = [sys.executable, "-B", str(CALLER)]
    with ThreadingHTTPServer(("127.0.0.1", 0), Gateway) as server:
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        args = [*caller, "--transport", "gateway", "--base-url", f"http://127.0.0.1:{server.server_port}/v1",
                "--state-file", str(state), "--session-file", str(session),
                "--question", "Which lookup rule should change?"]
        process = subprocess.Popen(args, cwd=tmp_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            assert dispatched.wait(10)
            process.terminate()
            process.communicate(timeout=10)
            status = subprocess.run([*caller, "--status", "--session-file", str(session)], cwd=tmp_path,
                                    capture_output=True, text=True, timeout=10, check=True)
            reported = json.loads(status.stdout)
            assert reported["dispatch_possible"] is True
            assert reported["provider_receipt"] is False
            assert reported["unknown_usage_rounds"] == 1
            retry = subprocess.run(args, cwd=tmp_path, capture_output=True, text=True, timeout=10, check=False)
            assert retry.returncode == 1
            assert json.loads(retry.stdout)["status"] == "dispatch_unresolved"
            assert generations == ["/v1/chat/completions"]
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
            release.set()
            server.shutdown()
            worker.join()


def test_scoped_handoff_preserves_omission_signal_without_sending_full_intent(tmp_path):
    source = tmp_path / "request.txt"
    source.write_text("Fix mobile checkout.\nUnrelated private backlog.\n", encoding="utf-8")
    correction = tmp_path / "correction.txt"
    correction.write_text("Checkout follow-up.\nKeep checkout available to guests.\n", encoding="utf-8")
    state = {
        "revision": "r2",
        "intent_sources": {"request": source.name, "update-2": correction.name},
        "intent_updates": ["update-2"],
        "intent_update_lines": {"update-2": 2},
    }
    handoff = {
        "revision": "r2", "phase": "delivery",
        "quotes": [{"source": "request", "first": 1, "last": 1}],
        "checks": ["Checked mobile layout at 390 px."],
        "evidence": ["r2: checkout viewport screenshot recorded by executor."],
    }
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
    question = "Does mobile checkout meet the delivery requirements?"
    packet = ask_expert.scoped_handoff(handoff_path, tmp_path / "state.json", state, question)
    assert json.loads(packet)["unquoted_updates"] == ["update-2"]
    assert json.loads(packet)["unquoted_update_lines"] == {"update-2": 2}
    assert "Unrelated private backlog" not in packet
    assert "available to guests" not in packet

    handoff["quotes"].append({"source": "update-2", "first": 1, "last": 1})
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
    packet = json.loads(ask_expert.scoped_handoff(handoff_path, tmp_path / "state.json", state, question))
    assert packet["unquoted_updates"] == ["update-2"]
    handoff["quotes"][-1] = {"source": "update-2", "first": 2, "last": 2}
    handoff_path.write_text(json.dumps(handoff), encoding="utf-8")
    packet = json.loads(ask_expert.scoped_handoff(handoff_path, tmp_path / "state.json", state, question))
    assert packet["constraints_or_corrections"] == ["update-2:2-2 Keep checkout available to guests."]
    assert packet["unquoted_updates"] == []

    state["revision"] = "r3"
    with pytest.raises(ValueError, match="current task revision"):
        ask_expert.scoped_handoff(handoff_path, tmp_path / "state.json", state, question)


@pytest.mark.parametrize("replace_image", [False, True])
def test_scoped_screenshot_preserves_protocol_and_checks_image_bytes(
        monkeypatch, capsys, tmp_path, replace_image):
    from hashlib import sha256
    from PIL import Image

    image_path = tmp_path / "mobile.png"
    Image.new("RGB", (12, 16), "white").save(image_path)
    image_hash = sha256(image_path.read_bytes()).hexdigest()
    (tmp_path / "request.txt").write_text("Keep the primary action visible.", encoding="utf-8")
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps({
        "task_id": "mobile", "revision": "r1", "goal": "Fix layout", "phase": "delivery",
        "constraints": [], "acceptance": ["Primary action visible"], "evidence": [],
        "intent_sources": {"request": "request.txt"},
    }), encoding="utf-8")
    handoff_path = tmp_path / "handoff.json"
    handoff_path.write_text(json.dumps({
        "revision": "r1", "phase": "delivery", "image_sha256": [image_hash],
        "quotes": [{"source": "request", "first": 1, "last": 1}],
        "checks": ["Captured the mobile viewport"], "evidence": ["Current viewport screenshot"],
    }), encoding="utf-8")

    def gateway(args, model, messages):
        assert "Luna alone executes" in messages[0]["content"]
        assert json.loads(messages[1]["content"])["attached_image_sha256"] == [image_hash]
        assert args.image_parts[1]["type"] == "image_url"
        if replace_image:
            Image.new("RGB", (12, 16), "black").save(image_path)
        return {"status": "ok", "advice": (
            "Verdict: ACCEPT action is visible.\nDo: none\nVerify: Check viewport.\nRisk: none"
        )}

    monkeypatch.setattr(ask_expert, "call_gateway", gateway)
    monkeypatch.setattr(sys, "argv", [
        "ask_expert.py", "--transport", "gateway", "--state-file", str(state_path),
        "--session-file", str(tmp_path / "session.json"), "--handoff-file", str(handoff_path),
        "--question", "Is the primary action visible on mobile?", "--image", str(image_path),
    ])
    assert ask_expert.main() == int(replace_image)
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == ("stale" if replace_image else "ok")
    assert result["images"][0]["sha256"] == image_hash
    if replace_image:
        assert result["advice_fields"] is None
        assert "advice" not in result
    else:
        assert result["advice_fields"]["decision"] == "ACCEPT"


def test_scoped_audio_preserves_original_bytes_and_review_identity(tmp_path):
    import base64
    import wave

    clip = tmp_path / "original.wav"
    with wave.open(str(clip), "wb") as stream:
        stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x01\x00" * 800)
    parts, audio = ask_expert.prepare_audio([clip])
    assert base64.b64decode(parts[1]["input_audio"]["data"]) == clip.read_bytes()
    assert audio[0]["duration_seconds"] == 0.1

    (tmp_path / "request.txt").write_text("Check the original for clipping.", encoding="utf-8")
    state = {"revision": "raw1", "intent_sources": {"request": "request.txt"}}
    path = tmp_path / "handoff.json"
    handoff = {
        "revision": "raw1", "phase": "delivery",
        "quotes": [{"source": "request", "first": 1, "last": 1}],
        "checks": ["Measured peak"], "evidence": ["Original clip attached"],
        "audio_sha256": [audio[0]["sha256"]],
    }
    path.write_text(json.dumps(handoff), encoding="utf-8")
    question = "What do you hear in the original clip?"
    packet = ask_expert.scoped_handoff(path, tmp_path / "state.json", state, question, audio=audio)
    assert json.loads(packet)["attached_audio_sha256"] == handoff["audio_sha256"]

    with wave.open(str(clip), "wb") as stream:
        stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\x02\x00" * 800)
    _, changed = ask_expert.prepare_audio([clip])
    with pytest.raises(ValueError, match="Audio bytes do not match"):
        ask_expert.scoped_handoff(path, tmp_path / "state.json", state, question, audio=changed)


def test_audio_normalization_only_removes_identical_finish_repetition():
    reply = "Verdict: ACTION identify phrase.\nDo: Check clip 1.\nVerify: Listen.\nRisk: none."
    finish = '{"toolAction":"Finishing the task","toolSummary":"Finish task"}'
    assert ask_expert.normalize_audio_advice(reply + finish + "\n" + reply) == reply
    assert ask_expert.normalize_audio_advice(finish + "\n" + reply) == reply
    finish_variant = '{"toolAction": "Finishing audio comparison task", "toolSummary": "Finish audio comparison task"}'
    assert ask_expert.normalize_audio_advice(reply + finish_variant + "\n" + reply) == reply
    consultation = '{"toolAction":"Finishing the consultation task","toolSummary":"Finish consultation task"}'
    assert ask_expert.normalize_audio_advice(reply + consultation + "\n" + reply) == reply
    changed = reply + consultation + "\n" + reply.replace("clip 1", "clip 2")
    assert ask_expert.normalize_audio_advice(changed) == changed
    mismatched = finish_variant.replace("Finish audio comparison", "Finish audio evaluation")
    assert ask_expert.normalize_audio_advice(reply + mismatched) == reply + mismatched
    conflicting = reply + finish + "\n" + reply.replace("clip 1", "clip 2")
    assert ask_expert.normalize_audio_advice(conflicting) == conflicting
    unexpected = reply + '{"toolAction":"Read other file","toolSummary":"Inspect"}'
    assert ask_expert.normalize_audio_advice(unexpected) == unexpected


def test_explicit_audio_timestamps_must_fit_the_measured_span():
    assert ask_expert.audio_timestamps_fit("Speech at 0:01-0:03.99; bed through 4s.", 3.994)
    assert not ask_expert.audio_timestamps_fit("Bed audible from 0:00 to 0:06.", 3.994)
    assert not ask_expert.audio_timestamps_fit("Speech ends at 5.2 seconds.", 3.994)


def test_scoped_text_evidence_rejects_replaced_artifacts_and_old_revisions(tmp_path):
    from hashlib import sha256

    artifact = tmp_path / "browser.log"
    artifact.write_bytes(b"Internal line\nGuest confirmation visible.\n")
    record = {"path": artifact.name, "revision": "r2", "sha256": sha256(artifact.read_bytes()).hexdigest()}
    state = {"revision": "r2", "evidence_sources": {"browser": record}}
    refs = [{"source": "browser", "first": 2, "last": 2}]
    excerpts = ask_expert.scoped_evidence(tmp_path / "state.json", state, refs)
    assert excerpts == [f"browser:2-2 [{record['sha256'][:12]}] Guest confirmation visible."]
    with pytest.raises(ValueError, match="current registered source"):
        ask_expert.scoped_evidence(tmp_path / "state.json", {**state, "revision": "r3"}, refs)
    artifact.write_bytes(b"Internal line\nDifferent confirmation.\n")
    with pytest.raises(ValueError, match="registered hash"):
        ask_expert.scoped_evidence(tmp_path / "state.json", state, refs)


@pytest.mark.parametrize(("known_tokens", "unknown_rounds"), [(101, 0), (20, 1)])
def test_frontier_usage_threshold_blocks_additional_dispatch(tmp_path, known_tokens, unknown_rounds):
    path = tmp_path / "session.json"
    path.write_text(json.dumps({
        "round": 1, "max_rounds": 3, "total_input_tokens": known_tokens,
        "total_output_tokens": 0, "unknown_usage_rounds": unknown_rounds,
    }), encoding="utf-8")
    args = SimpleNamespace(session_file=path, max_rounds=None, stop_after_tokens=100,
                           protocol="v2", preview=False)
    result = ask_expert.run_consultation(args, "What is the next verification step?")
    assert result["status"] == "budget_exhausted"
    assert result["attempts"] == []
    args.stop_after_tokens = None
    result = ask_expert.run_consultation(args, "What is the next verification step?")
    assert result["status"] == "budget_exhausted"
    assert result["stop_after_tokens"] == 100


@pytest.mark.parametrize(("returned_model", "extra", "error"), [
    ("different-audio-model", {}, "did not confirm the requested model alias"),
    ("audio-model", {"tool_calls": [{"id": "not-authorized"}]}, "attempted an execution tool call"),
])
def test_audio_gateway_rejects_unattributed_models_and_tool_calls(monkeypatch, returned_model, extra, error):
    def send(req, timeout):
        payload = json.loads(req.data)
        assert req.full_url.endswith("/chat/completions")
        assert "tools" not in payload
        assert payload["messages"][0]["content"][-1]["type"] == "input_audio"
        return _fake_response({"model": returned_model,
            "choices": [{"message": {"content": "Advice", **extra}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7}})

    monkeypatch.setattr(ask_expert, "urlopen", send)
    args = SimpleNamespace(base_url="http://localhost:4444/v1", timeout_seconds=10, image_parts=[],
                           audio_parts=[{"type": "input_audio", "input_audio": {"format": "wav", "data": "AA=="}}])
    with pytest.raises(ask_expert.GenerationError, match=error) as exc:
        ask_expert.call_gateway(args, "audio-model", [{"role": "user", "content": "Review"}])
    assert exc.value.receipt["usage"]["prompt_tokens"] == 11
