"""Run one registered, receipt-backed triplet per saved multimodal checkpoint."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from itertools import permutations
import json
import math
from pathlib import Path
import random
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace
import unicodedata

from scipy.stats import beta, norm

from direct_session import DirectSession, transport
from gap_study24 import ROOT, LUNA, ASTRA, digest
from run import dump
from sandbox import Sandbox, checked, docker
from suites import PYTHON_IMAGE
from trigger_pilot23 import tool

ARMS = ("full", "summary", "scoped")
AUDIO_MODEL = "gemini-subscription-argon"
AUDIO_SLOTS = threading.BoundedSemaphore(2)
PROTOCOL = ROOT / "docs/compression-confirmation-24.md"
FREEZE_FILES = [
    PROTOCOL,
    Path(__file__),
    Path(__file__).with_name("compression_inputs24.py"),
    ROOT / "eval/pricing.json",
    ROOT / "skills/advisor/scripts/ask_expert.py",
    ROOT / "skills/advisor/SKILL.md",
    ROOT / "eval/direct_session.py",
    ROOT / "eval/compression_report24.py",
    ROOT / "eval/telemetry_schema.json",
]
DEADLINE = 180


def read(path):
    return json.loads(path.read_bytes())


def words(s):
    return "".join(
        " " if unicodedata.category(c).startswith("P") else c for c in s.casefold()
    ).split()


def edit_distance(a, b):
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(
                min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (x != y))
            )
        previous = current
    return previous[-1]


def grade_output(modality, output, key):
    if modality == "audio":
        reference = words(key["transcript"])
        errors = edit_distance(reference, words(output))
        return errors <= 0.15 * len(reference), {
            "word_errors": errors,
            "reference_words": len(reference),
        }
    if modality == "image":
        value = output.strip().casefold()
        for reference in key["answers"]:
            target = reference.strip().casefold()
            if value == target:
                return True, {}
            try:
                x, y = [float(v.replace(",", "")) for v in (value, target)]
                if (
                    math.isfinite(x)
                    and math.isfinite(y)
                    and abs(x - y) <= abs(y) * 0.05
                ):
                    return True, {}
            except ValueError:
                pass
        return False, {}
    with tempfile.TemporaryDirectory() as tmp, Sandbox(PYTHON_IMAGE) as box:
        files = Path(tmp)
        (files / "core.py").write_text(output, encoding="utf-8")
        (files / "grade.py").write_text(
            "from core import *\n" + "\n".join(key["tests"]), encoding="utf-8"
        )
        checked(docker("exec", box.container, "mkdir", "-p", "/work"))
        for name in ("core.py", "grade.py"):
            box.copy(files / name, "/work/" + name)
        result = box.exec("python -B grade.py", timeout=8)
        return result.returncode == 0, {"verifier_exit": result.returncode}


def plan_size(pilot):
    pairs = {}
    for row in pilot:
        pairs.setdefault(row["case"], {})[row["arm"]] = row["verified_by_deadline"]
    if len(pairs) != 18 or any(set(v) != set(ARMS) for v in pairs.values()):
        raise ValueError(
            "Need all 54 assigned pilot outcomes before choosing the sample size."
        )
    discordant = sum(r["full"] != r["scoped"] for r in pairs.values())
    upper = (
        1.0
        if discordant == 18
        else float(beta.ppf(0.8, discordant + 1, 18 - discordant))
    )
    needed = math.ceil((norm.ppf(0.95) + norm.ppf(0.80)) ** 2 * upper / 0.05**2)
    return {
        "discordant": discordant,
        "upper_design_variance": upper,
        "uncapped_tasks": needed,
        "tasks": min(180, max(18, 18 * math.ceil(needed / 18))),
    }


def executor_call(session, prompt, tool_name, field, deadline):
    response = session.request(
        LUNA,
        "max",
        [{"role": "user", "content": prompt}],
        [tool(tool_name, "Submit the requested text.", (field,))],
        deadline,
    )
    calls = [r for r in response.get("output", []) if r.get("type") == "function_call"]
    if len(calls) != 1 or calls[0]["name"] != tool_name:
        raise ValueError("Executor did not submit the requested tool call.")
    value = json.loads(calls[0]["arguments"])[field]
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Executor submitted an empty value.")
    return value


def ask(case, job, packet, audio_url, deadline, folder):
    modality = job["modality"]
    image_parts, _ = transport.prepare_images(
        [case / "view.png"] if modality == "image" else []
    )
    audio_parts, _ = transport.prepare_audio(
        [case / "sound.wav"] if modality == "audio" else []
    )
    instruction = (
        transport.HANDOFF_INSTRUCTION
        + "\nThis is a decision. Use ACTION or NEEDS_EVIDENCE."
    )
    if audio_parts:
        instruction += "\nListen to the original. Give the complete verbatim utterance in Do. Omit timestamps unless requested."
    messages = [
        {"role": "developer", "content": instruction},
        {"role": "user", "content": packet},
    ]
    requested = AUDIO_MODEL if audio_parts else ASTRA
    receipt = {
        "requested_model": requested,
        "possible_dispatch": False,
        "status": "unavailable",
        "usage": {},
    }

    def dispatched():
        receipt["possible_dispatch"] = True
        dump(folder / "dispatch.json", receipt)

    args = SimpleNamespace(
        model=requested,
        protocol="v2",
        effort="low" if audio_parts else "medium",
        base_url=audio_url,
        image_parts=image_parts,
        audio_parts=audio_parts,
        timeout_seconds=max(1, min(75, deadline - time.monotonic())),
        consultation=SimpleNamespace(dispatch=dispatched),
    )
    try:
        if audio_parts:
            with AUDIO_SLOTS:
                args.timeout_seconds = min(75, deadline - time.monotonic())
                if args.timeout_seconds <= 0:
                    raise TimeoutError("Deadline before audio dispatch.")
                raw = transport.call_gateway(args, requested, messages)
        else:
            raw = transport.consult_codex(args, messages)
        advice = raw["advice"]
        if audio_parts:
            advice = transport.normalize_audio_advice(advice)
        fields = transport.parse_v2_advice(advice)
        verdict = transport.decision_label(fields["verdict"]) if fields else ""
        valid = raw.get("model") == requested and verdict in (
            "ACTION",
            "NEEDS_EVIDENCE",
        )
        receipt.update(
            status="ok" if valid else "invalid_advice",
            model=raw.get("model"),
            usage=transport.normalized_usage(raw),
            advice=advice,
            request_id=raw.get("request_id"),
        )
        return advice if valid else ""
    except (OSError, RuntimeError, ValueError) as exc:
        partial = getattr(exc, "receipt", {})
        receipt.update(
            error=str(exc),
            model=partial.get("model"),
            usage=transport.normalized_usage(partial),
        )
        return ""
    finally:
        dump(folder / "advisor.json", receipt)


def make_packet(case, job, session, history, transcript, deadline):
    question = history[-1]["decision_question"]
    if job["arm"] == "full":
        packet = transcript
    elif job["arm"] == "summary":
        summary = executor_call(
            session,
            "Summarize the following checkpoint for a tool-free advisor. "
            f"Keep within {4000 - len(question) - 1} characters. Include decisive evidence. "
            "The host adds the question and original media. Call summarize.\n"
            + transcript,
            "summarize",
            "text",
            deadline,
        )
        packet = summary.strip() + "\n" + question
        if len(packet) > 4000:
            raise ValueError("Free summary exceeded the registered character limit.")
    else:
        _, pictures = transport.prepare_images(
            [case / "view.png"] if job["modality"] == "image" else []
        )
        _, clips = transport.prepare_audio(
            [case / "sound.wav"] if job["modality"] == "audio" else []
        )
        packet = transport.scoped_handoff(
            case / "handoff.json",
            case / "state.json",
            read(case / "state.json"),
            question,
            pictures,
            clips,
        )
        if len(packet) > 4000:
            raise ValueError("Scoped packet exceeded the registered character limit.")
    return packet


def trial(folder, inputs, job, audio_url):
    case = inputs / job["case"]
    row = dict(**job, verified_by_deadline=False, correct=False, submitted=False)
    start = time.monotonic()
    deadline = start + DEADLINE
    session = None
    try:
        session = DirectSession(folder / "executor.jsonl")
        history = read(case / "checkpoint.json")
        transcript = json.dumps(history, ensure_ascii=False, indent=2)
        advice = ""
        try:
            packet = make_packet(case, job, session, history, transcript, deadline)
            (folder / "packet.txt").write_text(packet, encoding="utf-8")
            row["packet_chars"] = len(packet)
            advice = ask(case, job, packet, audio_url, deadline, folder)
        except (OSError, RuntimeError, ValueError) as exc:
            row["handoff_error"] = type(exc).__name__ + ": " + str(exc)
        prompt = (
            "Complete the original task. Use submit to write the exact contents of the final file, without Markdown. "
            "You own the final decision.\n"
            + transcript
            + "\nAdvisor reply:\n"
            + (advice or "Unavailable.")
        )
        output = executor_call(session, prompt, "submit", "contents", deadline)
        (folder / "submission").write_bytes(output.encode("utf-8"))
        row.update(
            submitted=True, submission_sha256=sha256(output.encode()).hexdigest()
        )
        correct, grade = grade_output(
            job["modality"], output, read(case / "grade.json")
        )
        row.update(
            correct=correct,
            grade=grade,
            verified_by_deadline=correct and time.monotonic() <= deadline,
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        KeyError,
        subprocess.TimeoutExpired,
    ) as exc:
        row["error"] = type(exc).__name__ + ": " + str(exc)
    row.update(
        seconds=round(time.monotonic() - start, 3),
        executor_usage=session.usage() if session else {},
    )
    if (folder / "advisor.json").exists():
        row["advisor"] = {
            k: v
            for k, v in read(folder / "advisor.json").items()
            if k not in ("advice", "request_id")
        }
    dump(folder / "outcome.json", row)
    return row


def run(inputs, out, phase, audio_url, pilot, acceptance, workers):
    selected = read(inputs / "selection.json")
    hashes = {
        p.relative_to(ROOT).as_posix(): transport.text_digest(p) for p in FREEZE_FILES
    }
    size = None
    if phase == "formal":
        pm = read(pilot / "manifest.json")
        if pm["files"] != hashes or pm["selection_sha256"] != digest(
            inputs / "selection.json"
        ):
            raise ValueError("Formal inputs differ from the pilot freeze.")
        proof = read(acceptance)
        if (
            set(proof["hosts"]) != {"codex", "claude"}
            or not all(
                len(proof["hosts"][host]) == 3
                and all(x["verified"] and x["advisor_ok"] for x in proof["hosts"][host])
                for host in proof["hosts"]
            )
            or proof["caller_sha256_lf"] != transport.text_digest(FREEZE_FILES[4])
        ):
            raise ValueError(
                "Both native hosts must pass three candidate consultations."
            )
        size = plan_size(read(pilot / "outcomes.json"))
    n = size["tasks"] // 3 if size else 6
    selected = [
        r
        for r in selected
        if r["phase"] == phase and int(r["id"][-3:]) <= (6 + n if size else n)
    ]
    orders = list(permutations(ARMS))
    rng = random.Random(2410 if size else 2409)
    rng.shuffle(orders)
    rng.shuffle(selected)
    by_modality = {}
    jobs = []
    for r in selected:
        m = r["modality"]
        i = by_modality.get(m, 0)
        by_modality[m] = i + 1
        case = inputs / r["id"]
        if any(digest(case / f) != h for f, h in r["packet_inputs"].items()):
            raise ValueError("Scoped input integrity check failed.")
        if any(
            digest(case / name) != r[k]
            for name, k in [
                (r["asset"], "bytes_sha256"),
                ("checkpoint.json", "checkpoint_sha256"),
                ("grade.json", "grade_sha256"),
            ]
        ):
            raise ValueError("Checkpoint integrity check failed.")
        jobs.extend(
            {"id": r["id"] + "-" + a, "case": r["id"], "modality": m, "arm": a}
            for a in orders[i % 6]
        )
    manifest = {
        "phase": phase,
        "files": hashes,
        "assignments": jobs,
        "sample_size": size,
        "deadline_seconds": DEADLINE,
        "selection_sha256": digest(inputs / "selection.json"),
        "executor": LUNA,
        "text_image_advisor": ASTRA,
        "audio_advisor": AUDIO_MODEL,
        "python_image": PYTHON_IMAGE,
    }
    out.mkdir(parents=True, exist_ok=True)
    with transport.session_lock(out / "cohort"):
        if (out / "manifest.json").exists():
            if read(out / "manifest.json") != manifest:
                raise ValueError("Refusing to overwrite a different frozen manifest.")
        else:
            dump(out / "manifest.json", manifest)

        def execute(job):
            folder = out / job["id"]
            if (folder / "outcome.json").exists():
                return read(folder / "outcome.json")
            if folder.exists():
                row = dict(
                    **job,
                    verified_by_deadline=False,
                    correct=False,
                    submitted=False,
                    error="interrupted",
                    seconds=None,
                )
                dump(folder / "outcome.json", row)
                return row
            folder.mkdir()
            return trial(folder, inputs, job, audio_url)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(execute, jobs))
        dump(out / "outcomes.json", rows)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["pilot", "formal"])
    parser.add_argument("inputs", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--pilot", type=Path)
    parser.add_argument("--acceptance", type=Path)
    parser.add_argument("--audio-url", required=True)
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=4)
    args = parser.parse_args()
    if args.phase == "formal" and not (args.pilot and args.acceptance):
        parser.error(
            "Formal assignments require pilot outcomes and exact-candidate host acceptance."
        )
    run(
        args.inputs,
        args.out,
        args.phase,
        args.audio_url,
        args.pilot,
        args.acceptance,
        args.workers,
    )
