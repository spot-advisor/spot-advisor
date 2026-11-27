"""Regrade submissions without consulting either model, tolerating checkout newline conventions."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path

from compression_report24 import summarize
from compression_study24 import grade_output, read
from gap_study24 import digest


def equivalent_text_digest(path, expected):
    data = path.read_bytes()
    lf = data.replace(b"\r\n", b"\n")
    return expected in {
        sha256(v).hexdigest() for v in (data, lf, lf.replace(b"\n", b"\r\n"))
    }


def replay(archive, inputs, workers):
    assert summarize(archive) == read(archive / "metrics.json")
    manifest = read(archive / "manifest.json")
    keys = {r["id"]: r for r in manifest["cases"]}
    for name, reg in keys.items():
        case = inputs / name
        assert digest(case / reg["asset"]) == reg["bytes_sha256"], name
        for path, h in {
            **reg["packet_inputs"],
            "grade.json": reg["grade_sha256"],
            "checkpoint.json": reg["checkpoint_sha256"],
        }.items():
            assert equivalent_text_digest(case / path, h), (name, path)
    rows = [
        json.loads(line)
        for line in (archive / "observations.jsonl").read_bytes().splitlines()
    ]
    submissions = [r for r in rows if r["submitted"]]

    def one(row):
        passed, _ = grade_output(
            row["modality"],
            row["submission"],
            read(inputs / row["case"] / "grade.json"),
        )
        assert passed == row["correct"], row["id"]
        return int(passed)

    with ThreadPoolExecutor(workers) as pool:
        passed = sum(pool.map(one, submissions))
    return {
        "phase": manifest["phase"],
        "assignments": len(rows),
        "submitted_files_regraded": len(submissions),
        "correct": passed,
        "archive_sha256": digest(archive / "observations.jsonl"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("inputs", type=Path)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=3)
    args = parser.parse_args()
    print(json.dumps(replay(args.archive, args.inputs, args.workers)))
