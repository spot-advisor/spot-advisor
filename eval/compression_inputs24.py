"""Build disjoint, pinned public checkpoints; keep grading keys outside the agent workspace."""

import argparse
import ast
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import random
import shutil
from urllib.request import urlopen

from PIL import Image
import pyarrow.parquet as pq
import soundfile as sf

from gap_study24 import digest
from run import dump


SOURCES = {
    "text": (
        "google-research-datasets/mbpp",
        "4bb6404fdc6cacfda99d4ac4205087b89d32030c",
        "sanitized/test-00000-of-00001.parquet",
        "e9e9efa2c0d59ef5e55537a9d126b8f875d5ac010a8d75628d76824884e15850",
        "CC BY 4.0",
    ),
    "image": (
        "HuggingFaceM4/ChartQA",
        "b605b6e08b57faf4359aeb2fe6a3ca595f99b6c5",
        "data/test-00000-of-00001-e2cd0b7a0f9eb20d.parquet",
        "efcf31333368c485eb569ec7bd4cc3694bd0e8e1a06c503124b51d27c924491d",
        "GPL-3.0 dataset card",
    ),
    "audio": (
        "google/fleurs",
        "70bb2e84b976b7e960aa89f1c648e09c59f894dd",
        "parquet-data/en_us/test-00000-of-00001.parquet",
        "6428a4d04d3aac29e16b45e039bb1470a8bd7aa334cf92f7984c9c520d1f234d",
        "CC BY 4.0",
    ),
}


def material(row, modality):
    if modality == "text":
        tests = row["test_list"]
        if len(tests) < 3 or row["test_imports"]:
            return
        functions = [
            n for n in ast.parse(row["code"]).body if isinstance(n, ast.FunctionDef)
        ]
        if not functions:
            return
        fn = next((n for n in functions if n.name + "(" in tests[0]), None)
        if fn is None:
            return
        signature = f"def {fn.name}({ast.unparse(fn.args)}):"
        data = (signature + "\n    raise NotImplementedError\n").encode()
        brief = row["prompt"].strip()
        check = f"Pristine module contains an unimplemented function. Signature: {signature} Public check: {tests[0]}"
        grade = {"tests": tests, "function": fn.name}
        return (
            str(row["task_id"]),
            brief,
            "Deliver the executable Python module core.py.",
            data,
            check,
            grade,
            [],
        )
    if modality == "image":
        data = row["image"]["bytes"]
        with Image.open(BytesIO(data)) as pic:
            if pic.format != "PNG" or len(data) > 300000 or not row["label"]:
                return
            audit = {
                "format": pic.format,
                "mode": pic.mode,
                "dimensions": pic.size,
                "luminance_histogram_16_bins": [
                    sum(pic.convert("L").histogram()[i : i + 16])
                    for i in range(0, 256, 16)
                ],
            }
        return (
            sha256(data).hexdigest(),
            row["query"],
            "Save the requested chart value or label alone in answer.txt.",
            data,
            f"PNG decoded at {audit['dimensions']}. Read the labels and marks in the attached chart.",
            {"answers": row["label"]},
            [audit],
        )
    buf = BytesIO()
    samples, rate = sf.read(BytesIO(row["audio"]["bytes"]), dtype="float32")
    seconds = len(samples) / rate
    if not 3 <= seconds <= 13 or len(row["transcription"].split()) < 5:
        return
    sf.write(buf, samples, rate, subtype="PCM_16", format="WAV")
    peaks = [
        round(float(abs(samples[i : i + rate // 4]).max()), 3)
        for i in range(0, len(samples), rate // 4)
    ]
    return (
        str(row["id"]),
        "Transcribe the attached English utterance into answer.txt.",
        "Preserve the spoken words and their order. Punctuation and capitalization are optional.",
        buf.getvalue(),
        f"The source decodes as {rate} Hz PCM, duration {seconds:.3f}s. Listen to the attached clip.",
        {"transcript": row["transcription"]},
        [{"quarter_second_peaks": peaks, "sample_count": len(samples)}],
    )


def prepare(cache, output):
    cache.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=False)
    selection = []
    for modality, (repo, revision, file, expected, license_name) in SOURCES.items():
        source = cache / (repo.split("/")[-1] + ".parquet")
        if not source.exists():
            with urlopen(
                f"https://huggingface.co/datasets/{repo}/resolve/{revision}/{file}",
                timeout=120,
            ) as response:
                with source.with_suffix(".part").open("wb") as sink:
                    shutil.copyfileobj(response, sink)
            source.with_suffix(".part").replace(source)
        if digest(source) != expected:
            raise ValueError(f"Dataset digest mismatch for {repo}")
        rows = pq.read_table(source).to_pylist()
        indices = list(range(len(rows)))
        random.Random("spot-advisor-2408-" + modality).shuffle(indices)
        seen, unique_text = set(), set()
        for index in indices:
            item = material(rows[index], modality)
            if item is None:
                continue
            key, goal, criterion, data, observed, grade, detail = item
            goal = " ".join(goal.split())
            # Keep one question per chart, one utterance per FLEURS sentence ID and one task per MBPP prompt.
            fingerprint = grade.get("transcript", goal).strip().casefold()
            if key in seen or fingerprint in unique_text:
                continue
            seen.add(key)
            unique_text.add(fingerprint)
            number = len(seen)
            name = f"{modality}-{number:03}"
            case = output / name
            case.mkdir()
            filename = {"text": "core.py", "image": "view.png", "audio": "sound.wav"}[
                modality
            ]
            (case / filename).write_bytes(data)
            (case / "brief.txt").write_text(goal + "\n" + criterion, encoding="utf-8")
            artifact_hash = sha256(data).hexdigest()
            (case / "observation.txt").write_text(observed, encoding="utf-8")
            state = {
                "task_id": name,
                "revision": artifact_hash,
                "phase": "decision",
                "goal": goal,
                "constraints": [],
                "acceptance": [criterion],
                "evidence": [],
                "intent_sources": {"brief": "brief.txt"},
                "evidence_sources": {
                    "inspection": {
                        "path": "observation.txt",
                        "revision": artifact_hash,
                        "sha256": digest(case / "observation.txt"),
                        "artifact_path": filename,
                        "artifact_sha256": artifact_hash,
                    }
                },
            }
            handoff = {
                "revision": artifact_hash,
                "phase": "decision",
                "quotes": [{"source": "brief", "first": 1, "last": 2}],
                "checks": ["Inspected the saved input."],
                "evidence_refs": [{"source": "inspection", "first": 1, "last": 1}],
            }
            if modality != "text":
                handoff[modality + "_sha256"] = [artifact_hash]
            question = {
                "text": "Which algorithm and edge cases should the executor implement?",
                "image": "Which exact chart answer should the executor save?",
                "audio": "Which exact spoken words should the executor save?",
            }[modality]
            transcript = [
                {"user_request": goal},
                {"delivery_requirement": criterion},
                {
                    "file_inspection": {
                        "filename": filename,
                        "bytes": len(data),
                        "sha256": artifact_hash,
                    }
                },
                *detail,
                {"observation": observed},
                {"decision_question": question},
            ]
            dump(case / "state.json", state)
            dump(case / "handoff.json", handoff)
            dump(case / "checkpoint.json", transcript)
            dump(case / "grade.json", grade)
            selection.append(
                {
                    "id": name,
                    "modality": modality,
                    "phase": "pilot" if number <= 6 else "formal",
                    "row": index,
                    "source": repo,
                    "source_key": key,
                    "license": license_name,
                    "revision": revision,
                    "asset": filename,
                    "bytes_sha256": artifact_hash,
                    "packet_inputs": {
                        file: digest(case / file)
                        for file in (
                            "brief.txt",
                            "state.json",
                            "handoff.json",
                            "observation.txt",
                        )
                    },
                    "checkpoint_sha256": digest(case / "checkpoint.json"),
                    "grade_sha256": digest(case / "grade.json"),
                }
            )
            if number == 66:
                break
        if len(seen) != 66:
            raise ValueError(f"Insufficient unique {modality} tasks")
        del rows
    dump(output / "selection.json", selection)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cache", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    prepare(args.cache, args.output)
