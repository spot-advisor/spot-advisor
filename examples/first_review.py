"""Prepare or independently verify a self-contained first consultation."""

import argparse
from array import array
from hashlib import sha256
import json
import math
from pathlib import Path
import wave


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def prepare(folder, modality):
    folder.mkdir(parents=True, exist_ok=False)
    handoff = {
        "phase": "decision",
        "quotes": [{"source": "brief", "first": 1, "last": 2}],
    }
    if modality == "text":
        asset = folder / "expiry.py"
        asset.write_bytes(b"def expired(now, deadline):\n    return now > deadline\n")
        brief = "Repair the deadline comparison in expiry.py.\nAt the exact deadline an entry is expired."
        observation = "Independent boundary probe: expired(19,20)=False, expired(20,20)=False, expired(21,20)=True."
        question = "Which comparison should change to satisfy the deadline rule?"
    elif modality == "image":
        from PIL import Image, ImageDraw

        asset = folder / "view.png"
        pic = Image.new("RGB", (350, 190), "white")
        draw = ImageDraw.Draw(pic)
        for x, name, length in [(45, "K", 70), (145, "M", 120), (245, "R", 48)]:
            draw.rectangle((x, 145 - length, x + 45, 145), fill="#225292")
            draw.text((x + 12, 151), name, fill="black", font_size=22)
        pic.save(asset)
        brief = "Read the attached chart and save the label of the tallest bar in answer.txt.\nUse the exact visible label."
        observation = "Image decoded; there are three labeled bars. The local check measured file identity only."
        handoff["image_sha256"] = [sha256(asset.read_bytes()).hexdigest()]
        question = "Which visible label should the executor save?"
    else:
        asset = folder / "sound.wav"
        samples = array("h")
        for i in range(32_000):
            t = i / 16_000
            frequency = 440 if 0.2 <= t < 0.6 else 880 if 1.2 <= t < 1.6 else 0
            samples.append(round(10_000 * math.sin(2 * math.pi * frequency * t)))
        import sys

        if sys.byteorder != "little":
            samples.byteswap()
        with wave.open(str(asset), "wb") as wav:
            wav.setparams((1, 2, 16_000, 0, "NONE", ""))
            wav.writeframes(samples.tobytes())
        brief = (
            "Listen to the two tones and save rising or falling in answer.txt.\n"
            "On the second line save the approximate onset of the second tone in seconds as a number."
        )
        observation = "The original mono PCM file decodes; its duration is 2 seconds."
        handoff["audio_sha256"] = [sha256(asset.read_bytes()).hexdigest()]
        question = "Does the pitch rise or fall, and when does the second tone begin?"
    revision = sha256(asset.read_bytes()).hexdigest()
    proof = folder / "check.txt"
    proof.write_text(observation, encoding="utf-8")
    (folder / "brief.txt").write_text(brief, encoding="utf-8")
    state = {
        "task_id": "first-" + modality,
        "phase": "decision",
        "revision": revision,
        "goal": brief.splitlines()[0],
        "acceptance": [brief.splitlines()[1]],
        "constraints": [],
        "evidence": [],
        "intent_sources": {"brief": "brief.txt"},
        "evidence_sources": {
            "check": {
                "path": proof.name,
                "sha256": sha256(proof.read_bytes()).hexdigest(),
                "revision": revision,
                "artifact_path": asset.name,
                "artifact_sha256": revision,
            }
        },
    }
    handoff.update(
        revision=revision,
        checks=["Read the current local check."],
        evidence_refs=[{"source": "check", "first": 1, "last": 1}],
    )
    write_json(folder / "state.json", state)
    write_json(folder / "handoff.json", handoff)
    (folder / "question.txt").write_text(question, encoding="utf-8")
    return question


def verify(folder):
    task = json.loads((folder / "state.json").read_bytes())["task_id"]
    if task == "first-text":
        import runpy

        expired = runpy.run_path(str(folder / "expiry.py"))["expired"]
        return all(
            expired(now, 20) is expected
            for now, expected in [(19, False), (20, True), (21, True)]
        )
    answer = (
        (folder / "answer.txt").read_text(encoding="utf-8-sig").strip().splitlines()
    )
    if task == "first-image":
        return answer == ["M"]
    return (
        len(answer) == 2
        and answer[0].strip().lower() == "rising"
        and abs(float(answer[1]) - 1.2) <= 0.25
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["text", "image", "audio", "verify"])
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if args.action == "verify":
        try:
            passed = verify(args.directory)
        except (OSError, ValueError, KeyError, SyntaxError):
            passed = False
        print(json.dumps({"passed": passed}))
        raise SystemExit(0 if passed else 1)
    print(prepare(args.directory, args.action))
