"""Exercise the installed command hooks and advisor CLI boundary on a temporary Codex home."""
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest


ADAPTER = Path(__file__).resolve().parents[1] / "integrations/codex"
FAKE = """
import argparse, json
p = argparse.ArgumentParser()
for name in ('state-file', 'session-file', 'trigger', 'question'):
    p.add_argument('--' + name)
p.add_argument('--preview', action='store_true')
a = p.parse_args()
state = json.load(open(a.state_file, encoding='utf-8'))
if a.preview or a.question == 'fail?':
    print(json.dumps({'status': 'preview' if a.preview else 'invalid_advice'}))
else:
    print(json.dumps(dict(state, status='ok', consultation_id='r', advice='Verify the actual artifact',
        advice_fields={'verify':'check'}, attempts=[{'status':'ok','dispatch_possible':True,'consultation_id':'r'}])))
"""


@pytest.fixture
def home(tmp_path):
    advisor = tmp_path / "fake.py"
    advisor.write_text(FAKE, encoding="utf-8")
    folder = tmp_path / "codex home"
    folder.mkdir()
    before = {"description": "Keep this", "hooks": {"Stop": [{"hooks": [{"type": "command",
              "command": "second-brain", "timeout": 5}]}], "SubagentStop": []}}
    path = folder / "hooks.json"
    path.write_text(json.dumps(before), encoding="utf-8")
    cmd = [sys.executable, str(ADAPTER / "install.py"), "--codex-home", str(folder),
           "--advisor", str(advisor)]
    subprocess.run(cmd, check=True)
    after = path.read_bytes()
    subprocess.run(cmd, check=True)
    assert after == path.read_bytes()
    config = json.loads(after)
    assert config["hooks"]["Stop"][:-1] == before["hooks"]["Stop"]
    assert config["hooks"]["SubagentStop"] == [] and config["description"] == "Keep this"
    assert len(config["hooks"]["PreToolUse"]) == 1
    return folder / "hooks/spot_advisor/review_gate.py"


def event(script, kind, tool="Bash", command="", turn="1", continued=False):
    payload = dict(hook_event_name=kind, session_id="session", turn_id=turn,
                   tool_name=tool, tool_input={"command": command}, stop_hook_active=continued)
    p = subprocess.run([sys.executable, str(script), "hook"], input=json.dumps(payload),
                       text=True, capture_output=True, check=True)
    return json.loads(p.stdout)


def consult(script, key, phase, *extra, question="Check this?"):
    return subprocess.run([sys.executable, str(script), "review", key, phase, "goal",
                           "observed evidence", question, *extra], capture_output=True, text=True)


def test_review_lifecycle(home):
    def pre(**kw):
        return event(home, "PreToolUse", **kw)

    def stop(**kw):
        return event(home, "Stop", **kw)

    blocked = pre(command="Set-Content early.txt too-soon")["hookSpecificOutput"]
    key = re.search(r" review ([0-9a-f]{32}) plan ", blocked["permissionDecisionReason"])[1]
    assert blocked["permissionDecision"] == "deny"
    assert pre(command="Get-Content -LiteralPath 'a b.txt'") == {}
    assert pre(command="rg -n 'pattern' src") == {}
    assert pre(command="pwd; run-unknown")["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert pre(command=f"python '{home.as_posix()}' review {key} plan 'g' 'e' 'q?'") == {}
    assert consult(home, key, "plan", "--preview").returncode == 0
    assert pre(tool="apply_patch")["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert consult(home, key, "plan", question="fail?").returncode != 0
    assert pre(tool="apply_patch")["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert consult(home, key, "plan").returncode == 0
    assert pre(tool="apply_patch") == {}
    assert stop()["decision"] == "block"
    assert consult(home, key, "qa", "--preview").returncode == 0
    assert stop(continued=True)["decision"] == "block"
    assert consult(home, key, "qa", question="fail?").returncode != 0
    assert "incomplete" in stop(continued=True)["stopReason"]
    assert consult(home, key, "qa").returncode == 0
    assert stop(continued=True) == {}
    assert pre(command="Set-Content reviewed.txt change") == {}
    assert stop()["decision"] == "block"  # A later edit invalidates earlier QA.
    assert consult(home, key, "qa").returncode == 0
    assert stop() == {}
    assert pre(tool="apply_patch", turn="another")["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert stop(turn="another")["decision"] == "block"
