"""Inspect safe local previews and installed skill capabilities, without a model call."""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from gap_study24 import ADVISOR, ROOT, cli, digest, review_files


def write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def copies():
    home = Path.home()
    dirs = {"published": ADVISOR.parents[1], "agents": home / ".agents/skills/advisor",
            "codex": Path(os.environ.get("CODEX_HOME", home / ".codex")) / "skills/advisor",
            "claude": Path(os.environ.get("CLAUDE_CONFIG_DIR", home / ".claude")) / "skills/advisor"}
    rows = {}
    for name, folder in dirs.items():
        caller = folder / "scripts/ask_expert.py"
        text = folder / "SKILL.md"
        row = {"discoverable_filename": text.is_file(), "disabled_marker": (folder / "SKILL.md.disabled").exists()}
        if caller.exists():
            proc = subprocess.run([sys.executable, "-B", str(caller), "--help"], capture_output=True,
                                  encoding="utf-8", timeout=10)
            row.update(cli_help_ok=proc.returncode == 0,
                       supports={f: f in proc.stdout for f in ["--handoff-file", "--audio", "--preview", "--status"]})
        for component, path in [("rule", text), ("caller", caller)]:
            if path.is_file():
                row[component + "_text_sha256"] = sha256(path.read_text(encoding="utf-8").encode()).hexdigest()
        rows[name] = row
    return rows


def probes(folder):
    requirement, _ = review_files(folder, "roster", False, False)
    question = "Does this saved payroll export meet the delivery criteria?"
    variants = {"en_ascii": question, "en_short": "Ship this CSV?",
                "zh_fullwidth": "这份工资名册导出文件现在符合交付验收要求吗？",
                "zh_ascii": "这份工资名册导出文件现在符合交付验收要求吗?",
                "en_trailing_evidence": question + " I verified the file.",
                "two_questions": "Does the roster meet the criteria? Which format should payroll use?"}
    syntax = {k: {"input": q, "status": cli(folder, q, True)["status"]} for k, q in variants.items()}
    state_path = folder / "state.json"
    state = json.loads(state_path.read_bytes())
    state["last_advice_outcome"] = "Unique follow-up observation: the exporter retained the old header."
    write(state_path, state)
    handoff_path = folder / "handoff.json"
    handoff = json.loads(handoff_path.read_bytes())
    prior = "Verdict: NEEDS_EVIDENCE about payroll reader compatibility."
    write(folder / "session.json", {"task_id": state["task_id"], "round": 1, "max_rounds": 1,
                                    "last_verdict": prior})
    exhausted = cli(folder, question, True)["status"]
    # Invoke both follow-up paths with identical two-round session settings.
    session = {"task_id": state["task_id"], "round": 1, "max_rounds": 2, "last_verdict": prior}
    write(folder / "session.json", session)
    base = [sys.executable, "-B", str(ADVISOR), "--preview", "--state-file", str(state_path),
            "--session-file", str(folder / "session.json"), "--question", question]

    def preview(*extra):
        p = subprocess.run(base + list(extra), capture_output=True, encoding="utf-8", timeout=10)
        return json.loads(p.stdout)

    fields = {}
    for name, extra in [("ordinary", []), ("scoped", ["--handoff-file", str(handoff_path)])]:
        data = preview(*extra)
        packet = data.get("packet", "")
        fields[name] = {"status": data["status"], "acceptance_included": requirement in packet,
                        "outcome_included": state["last_advice_outcome"] in packet,
                        "chars": data.get("packet_chars")}
    session_path = folder / "session.json"
    session_path.unlink()
    state["intent_updates"] = ["brief"]
    state["intent_update_lines"] = {"brief": 2}
    write(state_path, state)
    flagged = json.loads(cli(folder, question, True)["packet"])["unquoted_updates"]
    (folder / "observation.txt").write_text("New observation on the saved revision.", encoding="utf-8")
    stale = cli(folder, question, True)
    state["evidence_sources"]["check"]["sha256"] = digest(folder / "observation.txt")
    write(state_path, state)
    handoff["checks"] = ["Validated sample. " * 90]
    write(handoff_path, handoff)
    first = preview("--handoff-file", str(handoff_path))
    write(session_path, session)
    follow = preview("--handoff-file", str(handoff_path))
    return {"format": syntax, "follow_up_fields": fields, "exhausted_round_status": exhausted,
            "registered_omission_flagged": flagged == ["brief"], "stale_evidence_status": stale["status"],
            "stale_evidence_error": stale.get("error"), "identical_evidence_budget": {
                "first_status": first["status"], "first_chars": first.get("packet_chars"),
                "followup_status": follow["status"]},
            "dispatch_journals_created": len(list(folder.glob("*.attempts.jsonl")))}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    out = parser.parse_args().output
    with tempfile.TemporaryDirectory() as temp:
        result = {"baseline": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "copies": copies(), "probes": probes(Path(temp))}
    with out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
