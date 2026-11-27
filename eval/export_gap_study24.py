"""Build an auditable, offline package from the 2.4 study receipts."""

import argparse
from collections import Counter
import json
from pathlib import Path
import tempfile

from direct_session import transport
from gap_study24 import ROOT, digest, review_files


def read(path):
    return json.loads(path.read_bytes())


def admits(folder, args, normalize):
    question = args.get("question", "").strip()
    if normalize and question.endswith("？"):
        question = question[:-1] + "?"
    state = folder / "state.json"
    try:
        transport.scoped_handoff(folder / "handoff.json", state, read(state), question)
    except ValueError:
        return False
    return bool(args.get("evidence", "").strip())


def export(runs, output):
    output.mkdir(parents=True)
    cohorts = ["policy", "review", "coding", "provenance"]
    summaries = {}
    manifests = {}
    with (output / "observations.jsonl").open("x", encoding="utf-8", newline="\n") as archive, tempfile.TemporaryDirectory() as tmp:
        sample = Path(tmp)
        review_files(sample, "roster", True, True)
        for cohort in cohorts:
            folder = runs / ("24-" + cohort)
            manifest = read(folder / "manifest.json")
            manifests[cohort] = manifest
            rows = read(folder / "outcomes.json")
            assert len(rows) == len({r["id"] for r in rows})
            counts = Counter()
            for assignment, row in zip(manifest["assignments"], rows, strict=True):
                assert all(row[k] == v for k, v in assignment.items())
                trial = folder / row["id"]
                assert row == read(trial / "outcome.json")
                data = dict(cohort=cohort, **row)
                if cohort == "policy" and (trial / "calls.json").exists():
                    calls = read(trial / "calls.json")
                    data["calls"] = calls
                    names = [c["tool"] for c in calls]
                    assert names == row["actions"] and (names == [row["gold"]]) == row["action_ok"]
                    if names == ["consult"]:
                        args = calls[0]["arguments"]
                        assert admits(sample, args, False) == row["form_ok"]
                        data["fullwidth_replay_ok"] = admits(sample, args, True)
                elif cohort in ("review", "provenance") and (trial / "response.json").exists():
                    response = read(trial / "response.json")
                    data["review"] = response.get("advice_fields")
                    data["packet"] = json.loads(read(trial / "preview.json")["packet"])
                    assert data["packet"]["revision"] == row["artifact_hash"]
                    assert data["packet"]["revision"] == response.get("revision")
                    assert row["requirement_in_packet"] == row["quote"]
                    artifact = trial / ("roster.csv" if row["case"] == "roster" else "invitation.html")
                    assert digest(artifact) == row["artifact_hash"]
                elif cohort == "coding" and (trial / "work/core.py").exists():
                    core = trial / "work/core.py"
                    assert digest(core) == row["patch_sha256"]
                    data["submitted_source"] = core.read_bytes().decode("utf-8")
                archive.write(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n")
                counts["assigned"] += 1
                counts["errors"] += bool(row.get("error"))
            summaries[cohort] = {**counts, "raw_outcomes_sha256": digest(folder / "outcomes.json")}
    index = {"cohorts": summaries, "manifests": manifests, "observations_sha256": digest(output / "observations.jsonl")}
    with (output / "manifest.json").open("x", encoding="utf-8") as stream:
        json.dump(index, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export(args.runs, args.output)
