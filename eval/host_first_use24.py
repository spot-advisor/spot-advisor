"""Exercise the release walkthrough through actual Codex and Claude Code host entry points."""

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from queue import Empty, Queue
import shutil
import subprocess
import sys
import tempfile
from threading import Thread
import time

from gap_study24 import ROOT, ADVISOR, digest
from direct_session import transport
from run import dump


def native_codex(project, prompt):
    proc = subprocess.Popen(
        [shutil.which("codex"), "app-server"],
        cwd=project,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )
    events = Queue()

    def consume():
        for line in proc.stdout:
            try:
                events.put(json.loads(line))
            except ValueError:
                continue
        events.put(None)

    Thread(target=consume, daemon=True).start()

    def send(method, params, n=None):
        message = {"method": method, "params": params}
        if n is not None:
            message["id"] = n
        proc.stdin.write(json.dumps(message) + "\n")
        proc.stdin.flush()

    deadline = time.monotonic() + 540
    counts = Counter()

    def receive(n=None):
        while time.monotonic() < deadline:
            try:
                event = events.get(
                    timeout=min(5, max(0.1, deadline - time.monotonic()))
                )
            except Empty:
                continue
            if event is None:
                raise RuntimeError(
                    "Codex app-server closed before completing acceptance."
                )
            if n and event.get("id") == n:
                if "error" in event:
                    raise RuntimeError(str(event["error"]))
                return event["result"]
            method = event.get("method", "")
            if method == "item/completed":
                counts[event["params"]["item"]["type"]] += 1
            if not n and method == "turn/completed":
                turn = event["params"]["turn"]
                if turn["status"] == "failed":
                    raise RuntimeError(str(turn.get("error", {}))[:700])
                return turn["status"]
        raise TimeoutError("Native Codex walkthrough exceeded nine minutes.")

    try:
        send(
            "initialize",
            {"clientInfo": {"name": "spot-advisor-release-qa", "version": "2.4"}},
            1,
        )
        receive(1)
        send("initialized", {})
        send("model/list", {}, 4)
        catalog = receive(4)["data"]
        default = next(item["model"] for item in catalog if item.get("isDefault"))
        send(
            "thread/start",
            {
                "cwd": str(project),
                "ephemeral": True,
                "approvalPolicy": "never",
                "sandbox": "danger-full-access",
                "model": default,
            },
            2,
        )
        thread = receive(2)["thread"]["id"]
        send(
            "turn/start",
            {"threadId": thread, "input": [{"type": "text", "text": prompt}]},
            3,
        )
        receive(3)
        return {
            "entrypoint": "codex app-server",
            "turn": receive(),
            "items": dict(counts),
            "model": default,
        }
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=4)
        except subprocess.TimeoutExpired:
            proc.terminate()


def native_claude(project, prompt):
    command = [
        shutil.which("claude"),
        "-p",
        "--output-format",
        "json",
        "--no-session-persistence",
        "--permission-mode",
        "bypassPermissions",
        "--tools",
        "Bash,Read,Write,Edit,Skill",
    ]
    reply = subprocess.run(
        command,
        input=prompt,
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=540,
    )
    if reply.returncode:
        raise RuntimeError("Claude Code exited " + str(reply.returncode))
    payload = json.loads(reply.stdout)
    return {
        "entrypoint": "claude -p",
        "turn": payload.get("subtype"),
        "turns": payload.get("num_turns"),
        "is_error": payload.get("is_error"),
    }


def run(out, host, audio_url):
    folder = out / host
    folder.mkdir()
    project = Path(tempfile.mkdtemp(prefix="spot24-first-" + host + "-"))
    dump(folder / "local-project.json", {"directory": str(project)})
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    package = project / "candidate"
    for name in (
        "examples/first_review.py",
        "scripts/install_skill.py",
        "docs/first-consultation-24.md",
    ):
        target = package / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    shutil.copytree(
        ROOT / "skills/advisor",
        package / "skills/advisor",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    prompt = (
        "You are the approved Spot Advisor first-use tester in a clean "
        + host
        + " project. "
        "Follow candidate/docs/first-consultation-24.md. Install the bundled skill to this project's host-specific "
        "directory, and save the installed caller's --doctor JSON in project-root doctor.json. Perform all three walkthroughs in this project, "
        "using separate project-root directories named text, image and audio. Use the installed script for "
        "every call. Save each actual --preview JSON as preview.json and the live response as response.json in "
        "its case directory. Make one consultation per case. Use the scoped Codex route for text and image, "
        "and gateway "
        + audio_url
        + " with model gemini-subscription-argon for audio. Never print credentials. "
        "Use Python UTF-8 or PowerShell utf8 when reading and saving files. Use the returned advice, write the "
        "requested artifact, then run the public verifier. You may independently measure the artifact to "
        "resolve disagreement. Record any necessary assistance and discrepancies in notes.txt. Treat the "
        "generator and verifier as black boxes. Operate only inside this disposable project. Do not delegate "
        "or modify the candidate, verifier, task briefs or source media. Finish all independent cases even if one fails."
    )
    try:
        summary = (
            native_codex(project, prompt)
            if host == "codex"
            else native_claude(project, prompt)
        )
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        summary = {"error": type(exc).__name__ + ": " + str(exc)}
    dump(folder / "host.json", summary)
    rows = []
    caller_sha = transport.text_digest(ADVISOR)
    expected = (
        project
        / (".agents" if host == "codex" else ".claude")
        / "skills/advisor/scripts/ask_expert.py"
    )
    try:
        doctor = json.loads((project / "doctor.json").read_text(encoding="utf-8-sig"))
        installed = (
            Path(doctor["script"]).resolve() == expected.resolve()
            and doctor.get("script_sha256_lf") == caller_sha
            and doctor.get("live_requests") == 0
        )
    except (OSError, ValueError, KeyError):
        installed = False
    for kind in ("text", "image", "audio"):
        case = project / kind
        row = {"modality": kind, "verified": False, "advisor_ok": False}
        try:
            response = json.loads(
                (case / "response.json").read_text(encoding="utf-8-sig")
            )
            preview = json.loads(
                (case / "preview.json").read_text(encoding="utf-8-sig")
            )
            returned = response.get("advice_fields") or {}
            attempts = response.get("attempts", [])
            row.update(
                installation_verified=installed,
                preview=preview.get("status"),
                status=response.get("status"),
                model=response.get("model"),
                decision=returned.get("decision"),
                advice=returned.get("do"),
                usage=response.get("usage_normalized"),
                advisor_ok=bool(
                    installed
                    and preview.get("status") == "preview"
                    and response.get("status") == "ok"
                    and returned.get("decision") == "ACTION"
                    and any(
                        a.get("dispatch_possible")
                        and a.get("script_sha256_lf") == caller_sha
                        for a in attempts
                    )
                ),
            )
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "examples/first_review.py"),
                    "verify",
                    str(case),
                ],
                capture_output=True,
            )
            row["verified"] = result.returncode == 0
            output = case / ("expiry.py" if kind == "text" else "answer.txt")
            if output.is_file():
                row["final_file_sha256"] = digest(output)
        except (OSError, ValueError) as exc:
            row["error"] = type(exc).__name__
        rows.append(row)
    dump(folder / "outcomes.json", rows)
    return host, rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--audio-url", required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True)
    with ThreadPoolExecutor(2) as pool:
        hosts = dict(
            pool.map(lambda h: run(args.out, h, args.audio_url), ("codex", "claude"))
        )
    dump(
        args.out / "acceptance.json",
        {
            "tester": "Spot Advisor automated first use",
            "hosts": hosts,
            "caller_sha256_lf": transport.text_digest(ADVISOR),
        },
    )
