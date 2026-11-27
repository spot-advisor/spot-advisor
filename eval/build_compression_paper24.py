"""Rebuild the 2.4 paper tables, uncertainty figure and uploadable source directory."""

import argparse
from pathlib import Path
import shutil
import zipfile

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from compression_report24 import summarize
from compression_study24 import ARMS, read
from gap_study24 import ROOT


LABEL = {
    "full": "Full record",
    "summary": "Free summary",
    "scoped": "Scoped packet",
    "all": "All tasks",
    "text": "Programming",
    "image": "Chart reading",
    "audio": "Speech",
}


def build(archive, output):
    data = summarize(archive)
    output.mkdir(parents=True, exist_ok=True)
    source = ROOT / "paper/compression24/main.tex"
    if source.resolve() != (output / "main.tex").resolve():
        shutil.copy2(source, output / "main.tex")
    groups = data["summary"]
    contrasts = data["paired_scoped_minus_full"]
    formal = data["phase"] == "formal"
    lines = [
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Task set & Tasks & Full record & Free summary & Scoped packet \\",
        r"\midrule",
    ]
    for key in ("text", "image", "audio", "all"):
        group = groups[key]
        lines.append(
            " & ".join(
                [
                    LABEL[key],
                    str(group["full"]["n"]),
                    *(str(group[a]["verified"]) for a in ARMS),
                ]
            )
            + r" \\"
        )
    lines.extend([r"\bottomrule", r"\end{tabular}"])
    (output / "results-table.tex").write_bytes(("\n".join(lines) + "\n").encode())
    rows = [
        r"\begin{tabular}{lrrrr}",
        r"\toprule",
        r"Arm & Executor input & Advisor input & Median seconds & Priceable tasks \\",
        r"\midrule",
    ]
    for a in ARMS:
        g = groups["all"][a]
        nums = [
            LABEL[a],
            f"{g['executor']['input_tokens']['known_total']:,}",
            f"{g['advisor']['input_tokens']['known_total']:,}",
            f"{g['median_seconds']:.1f}",
            f"{g['cost_known']}/{g['n']}",
        ]
        rows.append(" & ".join(nums) + r" \\")
    rows.extend([r"\bottomrule", r"\end{tabular}"])
    (output / "cost-table.tex").write_bytes(("\n".join(rows) + "\n").encode())
    contrast = contrasts["all"]
    statements = (
        "The prespecified quality criterion was met."
        if contrast["noninferior"]
        else "The prespecified quality criterion was not established."
    )
    if not formal:
        statements = "This calibration cohort supplies no confirmatory claim."
    results = (
        f"{'Confirmation' if formal else 'Calibration'} included {contrast['n']} independent tasks. "
        f"The paired scoped-minus-full difference was {100 * contrast['difference']:+.1f} percentage points "
        f"with a one-sided 95\\% lower confidence bound of {100 * contrast['lower_95']:+.1f} points. "
        + statements
        + " "
    )
    if contrast["cost_ratio_upper_95"] is None:
        results += "The all-modality cost ratio is unidentifiable from these receipts and rate cards."
    else:
        results += f"The one-sided 95\\% upper bound on the cost ratio was {contrast['cost_ratio_upper_95']:.3f}."
    if formal:
        scoped = groups["all"]["scoped"]["verified"]
        summary = groups["all"]["summary"]["verified"]
        secondary = 100 * (scoped - summary) / contrast["n"]
        design = read(archive / "manifest.json")["sample_size"]
        results += (
            f" The descriptive scoped-minus-summary difference was {secondary:+.1f} points."
            f" The pilot-based design called for {design['uncapped_tasks']} tasks before"
            f" the registered cap of {design['tasks']} was applied."
        )
    (output / "finding.tex").write_bytes((results + "\n").encode())
    draw_quality(contrasts, output / "paired-quality.png")


def draw_quality(contrasts, path):
    fig, ax = plt.subplots(figsize=(6.3, 2.4), layout="constrained")
    for y, key in enumerate(("audio", "image", "text", "all")):
        c = contrasts[key]
        lo, point = 100 * c["lower_95"], 100 * c["difference"]
        ax.plot([lo, point], [y, y], color="#285b72", lw=2)
        ax.plot(lo, y, marker="|", color="#285b72", ms=10)
        ax.plot(point, y, marker="o", color="#173c50", ms=6)
    ax.axvline(
        -5, color="#ae4f2c", ls="--", lw=1.2, label="Prespecified margin (−5 pp)"
    )
    ax.axvline(0, color="0.55", lw=0.8)
    ax.set_yticks(range(4), [LABEL[k] for k in ("audio", "image", "text", "all")])
    ax.set_xlabel("Scoped minus full verified delivery (percentage points)")
    ax.set_ylim(-0.6, 3.65)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    fig.savefig(path, dpi=220)
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-zip", type=Path, help="Write a self-contained TeX submission bundle.")
    args = parser.parse_args()
    build(args.archive, args.output)
    if args.source_zip:
        args.source_zip.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(args.source_zip, "w") as bundle:
            for name in ("main.tex", "results-table.tex", "cost-table.tex", "finding.tex", "paired-quality.png"):
                path = args.output / name
                data = path.read_bytes() if path.suffix == ".png" else path.read_text(encoding="utf-8").encode()
                info = zipfile.ZipInfo(name, date_time=(2026, 11, 27, 0, 0, 0))
                bundle.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
