"""Assemble the bilingual GitHub Pages bundle with its archived scorecard."""

from hashlib import sha256
import json
from math import fsum
from pathlib import Path
import shutil
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
DIST = SITE / "dist"
FORMAL = ROOT / "eval/results/spot24-release/formal"
REPAIRS = ROOT / "eval/results/2026-11-20/formal-collection"
sys.path.insert(0, str(ROOT / "eval"))
from receipt_costs import estimate, fully_observed, requests

PAGES = ("index.html", "zh-CN/index.html")

SOURCES = {
    SITE / "assets/commands.js": DIST / "assets/commands.js",
    SITE / "assets/site.css": DIST / "assets/site.css",
    ROOT / "demo/spot-advisor-demo.gif": DIST / "assets/spot-advisor-demo.gif",
    ROOT / "demo/spot-advisor-demo.zh-CN.gif": DIST / "assets/spot-advisor-demo.zh-CN.gif",
}


def repair_comparison() -> dict:
    suite = "swe-bench-pro-v2"
    metrics = json.loads((REPAIRS / "metrics.json").read_text(encoding="utf-8"))
    manifest = json.loads((REPAIRS / "manifest.json").read_text(encoding="utf-8"))
    evidence = (REPAIRS / "evidence.jsonl").read_text(encoding="utf-8")
    if sha256(evidence.encode()).hexdigest() != metrics["evidence_sha256"]:
        raise ValueError("Repair receipts differ from the archived metrics.")
    excluded = set(metrics["excluded_before_dispatch"])
    tasks = {task["task_id"] for task in manifest["tasks"] if task["suite"] == suite} - excluded
    arms = ("astra_only", "spot_advisor")
    totals = {arm: {} for arm in arms}
    complete = {arm: set() for arm in arms}
    missing = dict.fromkeys(arms, 0)
    for line in evidence.splitlines():
        row = json.loads(line)
        if row["suite"] != suite:
            continue
        arm, task = row["arm"], row["task_id"]
        if task in totals[arm]:
            raise ValueError(f"Duplicate repair record: {task}, {arm}")
        prices = [estimate(receipt) for receipt in requests(row)]
        unknown = prices.count(None)
        missing[arm] += unknown
        totals[arm][task] = fsum(price for price in prices if price is not None)
        if not unknown and fully_observed(row):
            complete[arm].add(task)
    if any(set(totals[arm]) != tasks for arm in arms):
        raise ValueError("Repair receipts do not match the paired tasks in the manifest.")
    matched = complete["astra_only"] & complete["spot_advisor"]
    cost = {arm: fsum(totals[arm].values()) for arm in arms}
    matched_cost = {arm: fsum(totals[arm][task] for task in matched) for arm in arms}
    timely = next(window["timely_passes"] for window in metrics["deployment"]["windows"]
                  if window["deadline_seconds"] == 900)
    return {
        "n": len(tasks), "arms": metrics["suites"][suite], "cost": cost, "timely": timely,
        "missing": missing, "matched_n": len(matched), "matched_cost": matched_cost,
        "lower": 100 * (1 - cost["spot_advisor"] / cost["astra_only"]),
        "matched_lower": 100 * (1 - matched_cost["spot_advisor"] / matched_cost["astra_only"]),
    }


def write_skill_zip(destination: Path) -> None:
    # Ship only the skill. A fixed timestamp and LF text keep downloads reproducible.
    skill = ROOT / "skills/advisor"
    with ZipFile(destination, "w") as archive:
        for name in ("SKILL.md", "agents/openai.yaml", "scripts/ask_expert.py"):
            member = ZipInfo(f"advisor/{name}")
            member.create_system = 3
            member.external_attr = 0o100644 << 16
            archive.writestr(member, (skill / name).read_text(encoding="utf-8").encode(),
                             compress_type=ZIP_DEFLATED)


def scorecard() -> dict:
    metrics = json.loads((FORMAL / "metrics.json").read_text(encoding="utf-8"))
    manifest = json.loads((FORMAL / "manifest.json").read_text(encoding="utf-8"))
    packets = {}
    with (FORMAL / "observations.jsonl").open(encoding="utf-8") as archive:
        for line in archive:
            row = json.loads(line)
            packets.setdefault(row["case"], {})[row["arm"]] = row["packet_chars"]

    paired = metrics["paired_scoped_minus_full"]["all"]
    if len(packets) != paired["n"] or paired["n"] != manifest["sample_size"]["tasks"]:
        raise ValueError("Scorecard sources disagree on the number of matched tasks.")
    return {
        "repair": repair_comparison(),
        "scores": metrics["summary"],
        "tasks": paired["n"],
        "deadline": manifest["deadline_seconds"],
        "paired_difference": 100 * paired["difference"],
        "paired_lower": 100 * paired["lower_95"],
        "shorter": sum(arms["scoped"] < arms["full"] for arms in packets.values()),
    }


def main() -> None:
    expected_parent = SITE.resolve()
    resolved_dist = DIST.resolve()
    if resolved_dist.parent != expected_parent:
        raise ValueError("Pages output must stay directly inside the site source directory.")
    is_junction = getattr(DIST, "is_junction", lambda: False)
    if DIST.is_symlink() or is_junction():
        raise ValueError("Refusing to build through a linked Pages output directory.")
    missing = [source for source in SOURCES if not source.is_file()]
    if missing:
        raise FileNotFoundError("Required Pages source assets are missing: " + ", ".join(str(path) for path in missing))
    values = scorecard()
    pages = {
        name: (SITE / name).read_text(encoding="utf-8").format_map(values)
        for name in PAGES
    }
    if DIST.exists():
        if not DIST.is_dir():
            raise ValueError("Pages output exists and is not a directory.")
        shutil.rmtree(DIST)
    for source, destination in SOURCES.items():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        print(f"Copied {source.relative_to(ROOT)} -> {destination.relative_to(ROOT)}")
    write_skill_zip(DIST / "advisor.zip")
    for name, html in pages.items():
        destination = DIST / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(html, encoding="utf-8", newline="\n")
        print(f"Rendered {destination.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
