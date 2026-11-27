"""Repeat scoped delivery reviews with and without the observed artifact digest."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import random
import time

from gap_study24 import ADVISOR, ASTRA, ROOT, cli, digest, dump, review_files


def run_one(folder, job):
    folder.mkdir()
    start = time.monotonic()
    try:
        requirement, revision = review_files(folder, job["case"], job["valid"], job["quote"])
        if job["label"]:
            proof = folder / "observation.txt"
            proof.write_text(f"Parsed artifact SHA-256={revision}. " + proof.read_text(encoding="utf-8"),
                             encoding="utf-8")
            statefile = folder / "state.json"
            state = json.loads(statefile.read_bytes())
            state["evidence_sources"]["check"]["sha256"] = digest(proof)
            statefile.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        question = "Is this exact revision ready for delivery to the requester?"
        preview = cli(folder, question, True)
        dump(folder / "preview.json", preview)
        payload = cli(folder, question)
        dump(folder / "response.json", payload)
        result = {"requirement_in_packet": requirement in preview.get("packet", ""),
                  "artifact_hash": revision, "revision_bound": payload.get("revision") == revision,
                  "status": payload.get("status"), "model": payload.get("model"),
                  "verdict": (payload.get("advice_fields") or {}).get("decision"),
                  "attempts": len(payload.get("attempts", [])), "usage": payload.get("usage_normalized")}
    except Exception as exc:
        result = {"error": type(exc).__name__ + ": " + str(exc)}
    row = {**job, **result, "seconds": round(time.monotonic() - start, 3)}
    dump(folder / "outcome.json", row)
    return row


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    folder = parser.parse_args().output.resolve()
    folder.mkdir(parents=True)
    jobs = [dict(case=c, valid=v, quote=q, label=l, repeat=r) for c in ("roster", "agenda")
            for v in (False, True) for q in (False, True) for l in (False, True) for r in (1, 2)]
    random.Random(2404).shuffle(jobs)
    jobs = [dict(id=f"p{i:03}", **j) for i, j in enumerate(jobs)]
    dump(folder / "manifest.json", {"seed": 2404, "created_utc": datetime.now(timezone.utc).isoformat(),
         "assignments": jobs, "model": ASTRA, "sha256": {p.relative_to(ROOT).as_posix(): digest(p) for p in
         (Path(__file__), ADVISOR, Path(__file__).with_name("gap_study24.py"),
          ROOT / "docs/spot-advisor-2.4-provenance-control.md")}})
    with ThreadPoolExecutor(max_workers=2) as pool:
        dump(folder / "outcomes.json", list(pool.map(lambda j: run_one(folder / j["id"], j), jobs)))
