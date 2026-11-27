"""Run a live checkpoint pilot where only Luna can initiate a consultation."""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import random
import json
from pathlib import Path
import subprocess
import sys

from direct_session import DirectSession, ExecutorError
from run import ADVISOR, ASTRA, LUNA, ROOT, dump

SKILL = ROOT / "skills/advisor/SKILL.md"
AUDIO_CLIPS = tuple(ROOT / "eval/fixtures/trigger23-audio" / f"clip-{n}.wav" for n in (1, 2))
PRIOR = (
    "Ask when local checks leave two materially different next actions, or the same targeted "
    "check fails twice. If the failure names an unavailable local dependency, probe it first. "
    "Rerun the check on the same patch and confirm the intended assertion executed. "
    "Bring a remaining failure or decision to the advisor."
)

CASES = {
    "repair": {
        "request": "Correct the export configuration so the index includes archived records. Apply the boolean value as true or false. Verify the final setting.",
        "observations": ["The exact schema says include_archived must be true. Its initial value was false."],
        "valid": ["true"],
        "eligible": None,
    },
    "dependency": {
        "request": "Recover the local documentation build and verify the output. Apply the name of the missing package.",
        "observations": ["The build stopped before assertions ran. The dependency probe reports Markdown parser markdown-it-py missing from the active environment."],
        "valid": ["markdown-it-py"],
        "eligible": None,
    },
    "repeated-check": {
        "request": "Restore catalog validation without changing the source archive. The maintenance window allows one recovery action. Prefer the option that leaves recovery evidence available. Verify the result.",
        "observations": [
            "Catalog validation failed its expected-record-count assertion: expected 12, actual 0. A local refresh was queued.",
            "The local refresh completed. A rerun of the same assertion again gave expected 12, actual 0. The derived index and read-only fallback are both available. The fixture offers rebuild derived index and switch to read-only fallback. Either should restore the count. The local logs do not show which option best preserves evidence for the follow-up diagnosis.",
        ],
        "valid": ["rebuild derived index", "switch to read-only fallback"],
        "eligible": 2,
    },
    "audio": {
        "request": "Deliver Clip 1 or Clip 2 with intelligible dialogue and an audible music bed. Before delivery identify the exact spoken words. Apply the answer in the form Clip N | exact spoken words and verify it.",
        "observations": ["Two aligned WAVs are available through the consultation attachment. Local inspection exposes only levels and file identity. No playback or transcription is available locally. The measured bed RMS is 715 for Clip 1 and 2002 for Clip 2."],
        "valid": ["clip 2 | turn left at the blue sign"],
        "eligible": 1,
        "audio": True,
    },
    "design": {
        "request": "Choose and verify a primary signup action for a short mobile page. Prioritize new users discovering the trial.",
        "observations": ["Both Start trial and See plans fit and pass the same visible, reachable and functional checks. The local fixture has no usability feedback to resolve the primary action."],
        "valid": ["Start trial"],
        "eligible": 1,
    },
}

def tool(name, description, properties):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {key: {"type": "string"} for key in properties},
            "required": list(properties),
            "additionalProperties": False,
        },
    }

TOOLS = [
    tool("inspect", "Inspect the fixture and record its current local check.", ()),
    tool("consult", "Ask Spot Advisor a focused question with observed local evidence.", ("question", "evidence")),
    tool("apply", "Apply the chosen setting or label to the fixture.", ("value",)),
    tool("verify", "Run the verifier on the current fixture.", ()),
]

def ask(folder, case, question, evidence, observed):
    intent = folder / "intent.txt"
    intent.write_text(case["request"], encoding="utf-8")
    observation = folder / "observation.txt"
    observation.write_text("\n".join(observed), encoding="utf-8")
    revision = hashlib.sha256(observation.read_bytes()).hexdigest()
    dump(folder / "state.json", {
        "task_id": folder.name, "revision": revision, "goal": case["request"], "phase": "decision",
        "constraints": [], "acceptance": ["Return a next action and a verifiable check, including the exact spoken words when audio is attached."], "evidence": [],
        "intent_sources": {"request": intent.name},
        "evidence_sources": {"checks": {"path": observation.name, "revision": revision, "sha256": revision}},
    })
    handoff = {
        "revision": revision, "phase": "decision", "quotes": [{"source": "request", "first": 1, "last": 1}],
        "checks": [evidence], "evidence_refs": [{"source": "checks", "first": 1, "last": max(1, len(observed))}],
    }
    route = ["--transport", "codex", "--model", ASTRA]
    if case.get("audio"):
        clips = AUDIO_CLIPS
        handoff["audio_sha256"] = [hashlib.sha256(clip.read_bytes()).hexdigest() for clip in clips]
        route = ["--transport", "gateway", "--model", "gemini-subscription-argon",
                 "--audio", str(clips[0]), "--audio", str(clips[1])]
    dump(folder / "handoff.json", handoff)
    result = subprocess.run([
        sys.executable, "-B", str(ADVISOR), *route,
        "--max-rounds", "1", "--state-file", str(folder / "state.json"),
        "--session-file", str(folder / "session.json"), "--handoff-file", str(folder / "handoff.json"),
        "--question", question,
    ], capture_output=True, text=True, encoding="utf-8", timeout=85)
    payload = json.loads(result.stdout)
    dump(folder / "advisor-result.json", payload)
    return payload


def grade(trace, eligible, valid, final_value):
    calls = [event for event in trace if event["tool"] == "consult"]
    if eligible is None:
        timing = "unnecessary" if calls else "correct"
    elif not calls:
        timing = "missed"
    elif calls[0]["checks_seen"] < eligible:
        timing = "premature"
    else:
        timing = "correct" if len(calls) == 1 else "unnecessary"
    verified = bool(trace and trace[-1]["tool"] == "verify" and trace[-1]["result"]["passed"])
    return {"timing": timing, "executor_verified": verified, "independent_pass": final_value in valid,
            "advisor_completed": any(event.get("result", {}).get("status") == "ok" for event in calls)}


def question_rejected_locally(payload):
    return (payload.get("status") == "unavailable"
            and payload.get("error", "").startswith("Ask one specific decision question.")
            and not payload.get("attempts") and not payload.get("consultation_id"))


def trial(folder, case, instruction):
    folder.mkdir(parents=True)
    trace, observed = [], []
    value = None
    consultation_closed = False
    consultation_attempts = 0
    session = DirectSession(folder / "executor.jsonl")
    dump(folder / "config.json", {
        "case": case["request"], "fixture_sha256": hashlib.sha256(json.dumps(case, sort_keys=True).encode()).hexdigest(),
        "instruction_sha256": hashlib.sha256(instruction.encode()).hexdigest(), "model": LUNA,
        "effort": "max", "budget_seconds": 150,
    })
    (folder / "instruction.txt").write_text(instruction, encoding="utf-8")

    def run_tool(event):
        nonlocal value, consultation_closed, consultation_attempts
        name, args = event["tool"], event["arguments"]
        entry = {**event, "checks_seen": len(observed)}
        trace.append(entry)
        if name == "inspect":
            observation = case["observations"][min(len(observed), len(case["observations"]) - 1)]
            if value is not None:
                observation += f" Current applied value: {value}."
            observed.append(observation)
            result = {"observation": observation}
        elif name == "apply":
            value = args.get("value")
            if case.get("audio") and isinstance(value, str):
                value = value.lower().rstrip(".").replace('"', "").replace("'", "")
                value = " | ".join(part.strip() for part in value.split("|"))
            result = {"applied": value}
        elif name == "verify":
            result = {"passed": value in case["valid"], "value": value}
        elif name == "consult":
            if not observed or consultation_closed:
                result = {"status": "unavailable", "reason": "Missing local evidence or consultation budget exhausted."}
            else:
                consultation_attempts += 1
                submission = folder
                if consultation_attempts > 1:
                    submission = folder / f"submission-{consultation_attempts}"
                    submission.mkdir()
                payload = ask(submission, case, args.get("question", ""), args.get("evidence", ""), observed)
                dump(folder / f"advisor-attempt-{consultation_attempts}.json", payload)
                local_rejection = question_rejected_locally(payload)
                consultation_closed = not local_rejection or consultation_attempts >= 2
                result = {"status": payload.get("status"), "error": payload.get("error"),
                          "advice": payload.get("advice") if payload.get("status") == "ok" else None,
                          "local_question_rejection": local_rejection,
                          "consultation_id": payload.get("consultation_id")}
        else:
            result = {"error": "Unknown tool."}
        entry["result"] = result
        dump(folder / f"event-{len(trace):02}.json", entry)
        return result

    error = None
    try:
        session.run(LUNA, "max", f"{case['request']}\n\nAdvisor skill trigger rule:\n{instruction}",
                    TOOLS, run_tool, 150)
    except (ExecutorError, OSError, subprocess.SubprocessError, ValueError) as exc:
        error = str(exc)
    dump(folder / "trace.json", trace)
    result = {**grade(trace, case["eligible"], case["valid"], value), "error": error,
              "executor_usage": session.usage()}
    dump(folder / "grade.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--seed", type=int, default=23)
    parser.add_argument("--case", choices=CASES)
    args = parser.parse_args()
    candidate = SKILL.read_text(encoding="utf-8").split("1. ", 1)[1].split("\n2. ", 1)[0]
    rules = {"prior": PRIOR, "candidate": candidate}
    matrix = [(case, arm, repeat) for case in CASES if args.case in (None, case) for arm in rules for repeat in (1, 2)]
    random.Random(args.seed).shuffle(matrix)
    args.output.mkdir(parents=True)
    dump(args.output / "matrix.json", {"seed": args.seed, "assignments": matrix})
    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = {
            pool.submit(trial, args.output / f"{case}-{arm}-{repeat}", CASES[case], rules[arm]):
            (case, arm, repeat) for case, arm, repeat in matrix
        }
        for job in as_completed(jobs):
            case, arm, repeat = jobs[job]
            try:
                outcome = job.result()
            except (ExecutorError, OSError, ValueError) as exc:
                outcome = {"timing": "incomplete", "independent_pass": False, "error": str(exc)}
            results.append({"case": case, "arm": arm, "repeat": repeat, **outcome})
            print(case, arm, repeat, outcome["timing"], outcome["independent_pass"], outcome["error"], flush=True)
    dump(args.output / "comparison.json", sorted(results, key=lambda row: (row["case"], row["arm"], row["repeat"])))


if __name__ == "__main__":
    main()
