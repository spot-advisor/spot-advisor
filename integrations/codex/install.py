"""Install the optional Codex review gates, preserving unrelated hook registrations."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


MATCHER = ("^(Bash|PowerShell|exec_command|shell_command|apply_patch|Edit|Write|"
           "mcp__node_repl__js|mcp__filesystem__.*(?:write|edit|move|create|delete).*)$")


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
        temp = Path(f.name)
        try:
            f.write(data)
        except BaseException:
            f.close()
            temp.unlink()
            raise
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def install(home, advisor):
    if not advisor.is_file():
        raise ValueError("Supply the installed ask_expert.py with --advisor.")
    path = home / "hooks.json"
    before = path.read_bytes() if path.exists() else None
    config = json.loads(before) if before is not None else {}
    hooks = config.setdefault("hooks", {})
    root = home / "hooks" / "spot_advisor"
    script = root / "review_gate.py"
    command = subprocess.list2cmdline([sys.executable, str(script), "hook"]) if os.name == "nt" else None
    if command is None:
        import shlex
        command = shlex.join([sys.executable, str(script), "hook"])
    for event, title in [("PreToolUse", "Spot Advisor plan review"), ("Stop", "Spot Advisor final QA")]:
        groups = hooks.setdefault(event, [])
        handler = {"type": "command", "command": command, "timeout": 10, "statusMessage": title}
        group = {"hooks": [handler]}
        if event == "PreToolUse":
            group["matcher"] = MATCHER
        if group not in groups:
            if any(h.get("statusMessage") == title for g in groups for h in g["hooks"]):
                raise ValueError(f"Conflicting existing {title} registration. Inspect {path}.")
            groups.append(group)
    root.mkdir(parents=True, exist_ok=True)
    if before is not None and not (root / "hooks.before-install.json").exists():
        (root / "hooks.before-install.json").write_bytes(before)
    atomic_write(script, Path(__file__).with_name("review_gate.py").read_bytes())
    atomic_write(root / "settings.json", json.dumps({"advisor": str(advisor.resolve())}).encode())
    if before is None or config != json.loads(before):
        atomic_write(path, (json.dumps(config, indent=2) + "\n").encode())
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex-home", type=Path, default=Path(os.getenv("CODEX_HOME") or Path.home() / ".codex"))
    parser.add_argument("--advisor", type=Path, default=Path.home() / ".agents/skills/advisor/scripts/ask_expert.py")
    args = parser.parse_args()
    print(install(args.codex_home.resolve(), args.advisor))
    print("Review the installed script and trust its two registrations in Codex /hooks.")
