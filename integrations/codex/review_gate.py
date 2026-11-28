"""Optional local Codex hooks. The hook handlers only consult local review records."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile


SCRIPT = Path(__file__).resolve()
ROOT = SCRIPT.parent
KEY = r"[0-9a-f]{32}"
# Only plain, single commands may pass before planning without further classification.
# Scripts, compound commands and unknown executables are conservatively edit-capable.
LITERAL = r"(?!-)(?:[\w./:\\-]+|'(?!-)[^'\u2018\u2019\u201a\u201b\r\n]+')"
READ = re.compile(
    r"(?:pwd|Get-Location|git (?:status(?: --short(?: --branch)?| -sb)?|diff(?: --stat| --check| --name-only)?|branch --show-current)"
    rf"|(?:ls|dir|Get-ChildItem|Get-Content|cat|Test-Path|Get-FileHash)(?: (?:-LiteralPath |-Path )?{LITERAL})?"
    rf"|rg --files(?: {LITERAL})?"
    rf"|rg (?:-(?:n|l|i|F|w|ni|in) )?(?:-- )?(?!-){LITERAL}(?: (?!-){LITERAL})*)", re.I
)
STAGES = ("plan", "qa")


def db():
    con = sqlite3.connect(ROOT / "tasks.sqlite3", timeout=4, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY, edits INTEGER NOT NULL DEFAULT 0, plan TEXT,
        qa TEXT, qa_revision INTEGER, qa_failure_revision INTEGER)""")
    return con


def shell_text(payload):
    args = payload.get("tool_input") or {}
    if not isinstance(args, dict):
        return ""
    return str(args.get("command", args.get("cmd", ""))).strip()


def example(task, stage):
    return (f"python '{SCRIPT.as_posix()}' review {task} {stage} "
            "'task goal' 'specific plan or actual changes and checks' 'focused question?' ")


def checkpoint_command(command):
    """Recognize a standalone Second Brain checkpoint without invalidating project QA."""
    brain = ROOT.parents[1] / "skills/second-brain/scripts/brain_cli.py"
    paths = {str(brain), brain.as_posix()}
    names = [re.escape(path) for path in paths]
    script = "(?:" + "|".join(names) + ")"
    token = r"(?:[\w.,:/\\-]+|'[^'\u2018\u2019\u201a\u201b\r\n]*')"
    return re.fullmatch(rf"python (?:{script}|'{script}') (?:--json )?"
                        rf"(?:search|show|list|add|update)(?: {token})+", command)


def block(event, reason):
    if event == "PreToolUse":
        return {"hookSpecificOutput": {"hookEventName": event, "permissionDecision": "deny",
                                       "permissionDecisionReason": reason}}
    return {"decision": "block", "reason": reason}


def handle(data):
    event = data["hook_event_name"]
    if event not in ("PreToolUse", "Stop"):
        return {}
    identity = [data.get("session_id"), data.get("turn_id")]
    if not all(isinstance(x, str) and x for x in identity):
        return block(event, "Spot Advisor needs Codex session_id and turn_id.")
    task = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:32]
    shell = data.get("tool_name", "").lower() in ("bash", "shell", "shell_command", "exec_command", "powershell")
    command = shell_text(data) if shell else ""
    if shell and (READ.fullmatch(command) or checkpoint_command(command)):
        return {}
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT OR IGNORE INTO tasks(id) VALUES (?)", (task,))
        row = con.execute("SELECT * FROM tasks WHERE id=?", (task,)).fetchone()
        if event == "PreToolUse":
            if shell:
                prefix = f"python '{SCRIPT.as_posix()}'"
                if command == prefix + " context":
                    return block(event, f"Current gate ID: {task}. Use " + example(task, "plan"))
                # An exact standalone invocation allows review without opening a generic shell path.
                invocation = (re.escape(prefix) + " review " + task
                              + r" (?:plan|qa)(?: '[^'\u2018\u2019\u201a\u201b\r\n]+'){3}(?: --preview)?")
                if re.fullmatch(invocation, command):
                    return {}
            if not row["plan"]:
                return block(event, "Spot Advisor plan review required before this edit-capable tool. "
                             "Run the following as a standalone shell command, replacing the three quoted "
                             "arguments with current evidence. Preview with --preview, then repeat without it. "
                             + example(task, "plan"))
            con.execute("UPDATE tasks SET edits=edits+1 WHERE id=?", (task,))
            return {}
        if row["qa"] and row["qa_revision"] == row["edits"]:
            return {}
        reason = ("Spot Advisor final QA review required. Supply the actual changes, verification results "
                  "and remaining question. Preview with --preview, then repeat without it. "
                  + example(task, "qa"))
        if row["qa_failure_revision"] == row["edits"] and data.get("stop_hook_active"):
            message = "Spot Advisor final QA incomplete: consultation failed on the current revision."
            return {"continue": False, "stopReason": message, "systemMessage": message}
        return block(event, reason)


def review(args):
    if not re.fullmatch(KEY, args.task):
        raise ValueError("Use the gate ID from the hook.")
    with db() as con:
        row = con.execute("SELECT * FROM tasks WHERE id=?", (args.task,)).fetchone()
    if not row:
        raise ValueError("Unknown gate ID. Request the current context from Codex.")
    edits = row["edits"]
    stage = args.stage
    if row[stage] and (stage == "plan" or row["qa_revision"] == edits):
        return {"status": "already_reviewed", "consultation_id": row[stage]}
    revision = f"{stage}-{edits}"
    task_dir = ROOT / "sessions" / args.task
    task_dir.mkdir(parents=True, exist_ok=True)
    state = {"task_id": args.task, "phase": stage, "revision": revision, "goal": args.goal,
             "constraints": ["Use the supplied evidence."], "acceptance": ["Return a checkable next action."],
             "evidence": [args.evidence]}
    advisor = json.loads((ROOT / "settings.json").read_text(encoding="utf-8"))["advisor"]
    # Unique input per call so a concurrent preview cannot overwrite an in-flight review.
    with tempfile.NamedTemporaryFile(mode="w", dir=task_dir, encoding="utf-8",
                                     suffix=".json", delete=False) as f:
        json.dump(state, f)
    try:
        cmd = [sys.executable, advisor, "--state-file", f.name,
               "--session-file", str(task_dir / "session.json"), "--trigger", "manual",
               "--question", args.question]
        result = subprocess.run(cmd + (["--preview"] if args.preview else []),
                                capture_output=True, text=True, encoding="utf-8")
        try:
            if result.returncode:
                raise ValueError((result.stdout or result.stderr).strip())
            reply = json.loads(result.stdout)
            if not args.preview:
                validate_receipt(reply, args.task, revision)
        except ValueError:
            if stage == "qa" and not args.preview:
                with db() as con:
                    con.execute("UPDATE tasks SET qa_failure_revision=? WHERE id=? AND edits=?",
                                (edits, args.task, edits))
            raise
    finally:
        Path(f.name).unlink(missing_ok=True)
    if args.preview:
        return reply
    receipt = reply["consultation_id"]
    with db() as con:
        if stage == "plan":
            update = "UPDATE tasks SET plan=? WHERE id=? AND edits=?"
        else:
            update = "UPDATE tasks SET qa=?, qa_revision=edits WHERE id=? AND edits=?"
        if not con.execute(update, (receipt, args.task, edits)).rowcount:
            raise ValueError("Edits occurred during review. Review the current revision.")
    return {"status": "ok", "stage": stage, "revision": revision,
            "consultation_id": receipt, "advice": reply["advice"]}


def validate_receipt(reply, task, revision):
    receipt = reply.get("consultation_id")
    if not (reply.get("status") == "ok" and receipt and reply.get("advice_fields")
            and reply.get("task_id") == task and reply.get("revision") == revision
            and any(a.get("status") == "ok" and a.get("dispatch_possible")
                    and a.get("consultation_id") == receipt for a in reply.get("attempts", []))):
        raise ValueError("No matching successful Spot Advisor receipt.")


def main():
    if sys.argv[1:] == ["hook"]:
        event = ""
        try:
            payload = json.load(sys.stdin)
            event = payload["hook_event_name"]
            output = handle(payload)
        except (OSError, sqlite3.Error, KeyError, ValueError, TypeError) as exc:
            if event in ("PreToolUse", "Stop"):
                output = block(event, f"Spot Advisor gate could not check the review: {exc}")
            else:
                print(f"Invalid Spot Advisor hook input: {exc}", file=sys.stderr)
                return 2
        print(json.dumps(output))
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("context", help="Ask the Codex PreToolUse hook for the active gate ID.")
    p = subs.add_parser("review")
    p.add_argument("task")
    p.add_argument("stage", choices=STAGES)
    for name in ("goal", "evidence", "question"):
        p.add_argument(name)
    p.add_argument("--preview", action="store_true")
    args = parser.parse_args()
    if args.command == "context":
        parser.exit(1, "No active Codex pre-tool hook intercepted this command. Check hook trust.\n")
    try:
        print(json.dumps(review(args), ensure_ascii=True))
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
