"""Extract public product acceptance evidence without accounts, private paths or host transcripts."""

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys

from compression_report24 import write
from direct_session import transport
from gap_study24 import ADVISOR, ROOT, digest
from verify_install24 import verify as verify_install


def read(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def reviews(folder):
    rows = []
    for outcome in read(folder / "outcomes.json"):
        item = folder / outcome["id"]
        receipt = read(item / "response.json")
        packet = json.loads(read(item / "preview.json")["packet"])
        rows.append(
            {
                k: outcome.get(k)
                for k in (
                    "id",
                    "case",
                    "valid",
                    "quote",
                    "status",
                    "verdict",
                    "revision_bound",
                    "artifact_hash",
                )
            }
        )
        rows[-1].update(
            packet=packet,
            decision_fields=receipt.get("advice_fields"),
            caller_sha256_lf=receipt["attempts"][0].get("script_sha256_lf"),
        )
    return rows


def host_rows(runs):
    exported = {}
    for host in ("claude", "codex"):
        folder = runs / host
        actual = Path(read(folder / "local-project.json")["directory"])
        rows = read(folder / "outcomes.json")
        for row in rows:
            case = actual / row["modality"]
            receipt = read(case / "response.json")
            preview = read(case / "preview.json")
            attempts = receipt.get("attempts", [])
            assert any(
                r.get("dispatch_possible")
                and r["script_sha256_lf"] == transport.text_digest(ADVISOR)
                for r in attempts
            )
            output = case / ("expiry.py" if row["modality"] == "text" else "answer.txt")
            assert digest(output) == row["final_file_sha256"]
            public_verifier = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    str(ROOT / "examples/first_review.py"),
                    "verify",
                    str(case),
                ],
                capture_output=True,
            )
            assert (public_verifier.returncode == 0) == row["verified"]
            row.update(
                artifact=output.read_text(encoding="utf-8-sig"),
                packet=json.loads(preview["packet"]),
                status=receipt["status"],
                advice_fields=receipt["advice_fields"],
                transport=receipt["transport"],
                skill_version=attempts[-1]["skill_version"],
                caller_sha256_lf=attempts[-1]["script_sha256_lf"],
            )
        exported[host] = {"session": read(folder / "host.json"), "trials": rows}
    return exported


def export(runs, output):
    output.mkdir(parents=True, exist_ok=True)
    current = host_rows(runs / "native24-final")
    historical = {}
    for name in ("native24-first", "native24-catalog", "native24-freeze"):
        folder = runs / name
        dispositions = []
        for host in ("codex", "claude"):
            place = Path(read(folder / host / "local-project.json")["directory"])
            for row in read(folder / host / "outcomes.json"):
                receipt = place / row["modality"] / "response.json"
                data = read(receipt) if receipt.is_file() else {}
                dispositions.append(
                    {
                        "host": host,
                        "modality": row["modality"],
                        "verified": row["verified"],
                        "provider_status": data.get("status"),
                        "decision": (data.get("advice_fields") or {}).get("decision"),
                        "advice": (data.get("advice_fields") or {}).get("do"),
                        "model": data.get("model"),
                        "caller_sha256_lf": next(
                            (
                                a.get("script_sha256_lf")
                                for a in data.get("attempts", [])
                                if a.get("dispatch_possible")
                            ),
                            None,
                        ),
                    }
                )
        historical[name] = dispositions
    regression = {
        s: reviews(runs / s)
        for s in ("rc1-delivery", "rc2-delivery", "rc-final-delivery")
    }
    transports = {}
    for name in (
        "rc-route-claude",
        "rc-route-claude-v2",
        "rc2-audio-0",
        "rc2-audio-1",
        "rc2-audio-2",
    ):
        trial = read(runs / name / "response.json")
        transports[name] = {
            k: trial.get(k)
            for k in (
                "status",
                "model",
                "transport",
                "advice_fields",
                "error",
                "usage_normalized",
            )
        }
    marks = Counter()
    for p in sorted((runs / "paper24-pilot").glob("audio-*/advisor.json")):
        old = read(p)
        fields = transport.parse_v2_advice(
            transport.normalize_audio_advice(old.get("advice", ""))
        )
        admitted = fields and transport.decision_label(fields["verdict"]) in (
            "ACTION",
            "NEEDS_EVIDENCE",
        )
        marks["assigned"] += 1
        marks["original_admitted"] += old.get("status") == "ok"
        marks["offline_parser_admitted"] += bool(admitted)
    write(
        output / "qualification.json",
        {
            "automated_testers": "Spot Advisor on two independent native host sessions",
            "caller_sha256_lf": transport.text_digest(ADVISOR),
            "install": verify_install(),
            "hosts": current,
            "earlier_hosts": historical,
            "delivery_regressions": regression,
            "audio_parser_replay": dict(marks),
            "additional_routes": transports,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    export(args.runs, args.output)
