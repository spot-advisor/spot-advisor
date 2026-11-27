"""Assemble the bilingual GitHub Pages bundle with its archived scorecard."""

import json
from pathlib import Path
import shutil


SITE = Path(__file__).resolve().parent
ROOT = SITE.parent
DIST = SITE / "dist"
FORMAL = ROOT / "eval/results/spot24-release/formal"
PAGES = ("index.html", "zh-CN/index.html")

SOURCES = {
    SITE / "assets/site.css": DIST / "assets/site.css",
    ROOT / "docs/assets/spot-advisor-hero.webp": DIST / "assets/spot-advisor-hero.webp",
    ROOT / "docs/assets/spot-advisor-hero.zh-CN.webp": DIST / "assets/spot-advisor-hero.zh-CN.webp",
    ROOT / "demo/spot-advisor-demo.gif": DIST / "assets/spot-advisor-demo.gif",
    ROOT / "demo/spot-advisor-demo.zh-CN.gif": DIST / "assets/spot-advisor-demo.zh-CN.gif",
}


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
    for name, html in pages.items():
        destination = DIST / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(html, encoding="utf-8", newline="\n")
        print(f"Rendered {destination.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
