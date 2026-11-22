"""Prepare anonymous screenshot panels and score observed first-click responses."""

import argparse
import hashlib
import json
import random
import re
import shutil
from collections import Counter
from pathlib import Path

CHOICES = {"trial", "tour", "other", "unclear"}


def prepare(pilot: Path, control: Path, output: Path, seed: int) -> None:
    from run import dump

    raw_manifest = (pilot / "manifest.json").read_bytes()
    manifest = json.loads(raw_manifest)
    supplement = json.loads((control / "manifest.json").read_bytes())
    if supplement["original_manifest_sha256"] != hashlib.sha256(raw_manifest).hexdigest():
        raise ValueError("The original pilot and image control are not paired.")
    rng = random.Random(seed)
    output.mkdir(parents=True, exist_ok=False)
    panels = [[] for _ in range(5)]
    key = {}
    for case, arms in manifest["arms_by_case"].items():
        shuffled = rng.sample([*arms, "luna_with_image"], len(panels))
        for panel, arm in zip(panels, shuffled):
            root = control if arm == "luna_with_image" else pilot
            source = root / case / arm / "mobile.png"
            record = json.loads((source.parent / "result.json").read_bytes())
            if record["arm"] != arm or record["snapshot_sha256"] != hashlib.sha256(source.read_bytes()).hexdigest():
                raise ValueError(f"The screenshot differs from the original receipt: {source}")
            filename = f"view-{rng.getrandbits(64):016x}.png"
            panel.append(filename)
            key[filename] = {"case": case, "arm": arm, "functional_pass": record["functional_pass"]}
    cases = list(manifest["arms_by_case"])
    if not cases:
        raise ValueError("The visual pilot has no review cases.")
    rng.shuffle(cases)
    offsets = [i % len(cases) for i in range(len(panels))]
    rng.shuffle(offsets)
    for i, (panel, offset) in enumerate(zip(panels, offsets), start=1):
        by_case = {key[filename]["case"]: filename for filename in panel}
        panel = [by_case[case] for case in cases[offset:] + cases[:offset]]
        target = output / f"panel-{i}"
        target.mkdir()
        for filename in panel:
            item = key[filename]
            item["panel"] = i
            root = control if item["arm"] == "luna_with_image" else pilot
            shutil.copyfile(root / item["case"] / item["arm"] / "mobile.png", target / filename)
        instructions = [
            "# Where would you tap first?", "",
            "You are visiting the page to start a free trial. Open each image once, in order. "
            "For each, record the first element you would tap. Record your first impression before opening the next image.",
            "", "Use `trial` for Start free trial, `tour` for the product tour, "
            "`other` for something else, or `unclear` if you cannot decide.", "",
            *[f"{number}. [Open image]({name})" for number, name in enumerate(panel, 1)],
        ]
        (target / "README.md").write_text("\n".join(instructions) + "\n", encoding="utf-8")
        dump(target / "response-template.json", {"reviewer": "", "panel": i, "choices": dict.fromkeys(panel, "")})
    dump(output / "private-key.json", {"seed": seed, "files": key})


def tally(key: dict, responses: list[dict]) -> dict:
    reviewers, totals = set(), {}
    panel_counts = dict.fromkeys(sorted({item["panel"] for item in key.values()}), 0)
    for response in responses:
        name, panel, choices = (response.get(k) for k in ("reviewer", "panel", "choices"))
        name = name.strip() if isinstance(name, str) else ""
        expected = {filename for filename, item in key.items() if item["panel"] == panel}
        if (not name or name in reviewers or type(panel) is not int or not expected
                or not isinstance(choices, dict) or expected != choices.keys()
                or any(not isinstance(choice, str) or choice not in CHOICES for choice in choices.values())):
            raise ValueError("Use one complete assigned panel per anonymous reviewer.")
        reviewers.add(name)
        panel_counts[panel] += 1
        for filename, choice in choices.items():
            item = key[filename]
            counts = totals.setdefault(item["case"], {}).setdefault(item["arm"], Counter())
            counts[choice] += 1
    coverage = {}
    for item in key.values():
        counts = coverage.setdefault(item["case"], Counter())
        counts[item["arm"]] += panel_counts[item["panel"]]
    balanced = bool(panel_counts) and min(panel_counts.values()) > 0 and len(set(panel_counts.values())) == 1
    return {"reviewers": len(reviewers), "first_click_counts": totals,
            "panel_response_counts": {str(panel): count for panel, count in panel_counts.items()},
            "reviews_by_case_and_workflow": coverage, "balanced_panel_coverage": balanced,
            "interpretation": "Reported first choices by case and workflow. Use the coverage fields to scope the comparison."}


def verify_review(visual: dict, review: dict) -> None:
    control = visual["supplemental_image_control"]["cases"]
    expected = {(case, arm): row["snapshot_sha256"] for case, arms in visual["cases"].items()
                for arm, row in {**arms, "luna_with_image": control[case]}.items()}
    files = review["files"]
    cells = {(item["case"], item["arm"]): item["snapshot_sha256"] for item in files.values()}
    cases = set(visual["cases"])
    if (cells != expected or len(files) != len(expected)
            or any(not re.fullmatch(r"view-[0-9a-f]{16}\.png", name)
                   or item.keys() != {"case", "arm", "panel", "snapshot_sha256"} or type(item["panel"]) is not int
                   or item["panel"] not in range(1, 6) for name, item in files.items())):
        raise ValueError("Blind review panels differ from the frozen visual pilot screenshots.")
    for panel in range(1, 6):
        assigned = [item["case"] for item in files.values() if item["panel"] == panel]
        if len(assigned) != len(cases) or set(assigned) != cases:
            raise ValueError("Each review panel must show one screenshot per case.")
    responses = review["responses"]
    if (not responses or any(row.keys() != {"reviewer", "panel", "choices"} or row["reviewer"] != f"r{index}"
                             for index, row in enumerate(responses, 1))
            or tally(files, responses) != review["tally"]):
        raise ValueError("First-click tally disagrees with its anonymous responses.")


def collect_review(root: Path, response_paths: list[Path], visual: dict) -> dict:
    key = json.loads((root / "private-key.json").read_bytes())["files"]
    files = {}
    for filename, item in key.items():
        if not re.fullmatch(r"view-[0-9a-f]{16}\.png", filename) or type(item["panel"]) is not int:
            raise ValueError("Invalid panel screenshot filename or assignment.")
        png = root / f"panel-{item['panel']}" / filename
        files[filename] = {name: item[name] for name in ("case", "arm", "panel")}
        files[filename]["snapshot_sha256"] = hashlib.sha256(png.read_bytes()).hexdigest()
    original = [json.loads(path.read_bytes()) for path in response_paths]
    totals = tally(files, original)
    responses = [{"reviewer": f"r{index}", "panel": row["panel"], "choices": row["choices"]}
                 for index, row in enumerate(original, 1)]
    review = {"files": files, "responses": responses, "tally": totals}
    verify_review(visual, review)
    return review


def score(key_path: Path, response_paths: list[Path], output: Path) -> None:
    from run import dump

    key = json.loads(key_path.read_bytes())["files"]
    responses = [json.loads(path.read_bytes()) for path in response_paths]
    dump(output, tally(key, responses))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--pilot", type=Path, required=True)
    prep.add_argument("--control", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    prep.add_argument("--seed", type=int, default=20261121)
    grade = sub.add_parser("score")
    grade.add_argument("--key", type=Path, required=True)
    grade.add_argument("--output", type=Path, required=True)
    grade.add_argument("responses", type=Path, nargs="+")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.pilot, args.control, args.output, args.seed)
    else:
        score(args.key, args.responses, args.output)
