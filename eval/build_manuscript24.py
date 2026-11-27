"""Build the publication manuscript from the immutable Spot Advisor 2.4 confirmation."""

import argparse
import json
from pathlib import Path
from statistics import median
import zipfile

from build_compression_paper24 import LABEL, draw_quality
from compression_report24 import summarize
from compression_study24 import ARMS, read
from gap_study24 import ROOT, digest


BASE = ROOT / "eval/results/spot24-release"
SOURCE = ROOT / "paper/manuscript24"
GROUPS = ("text", "image", "audio", "all")
TITLE = (
    "What Survives a Structured Handoff? A Paired Study of One-Turn Multimodal Advice"
)
AUTHOR = "Stan Chen"
FILES = (
    "main.tex",
    "frontmatter.tex",
    "abstract.tex",
    "delivery.tex",
    "packets.tex",
    "usage.tex",
    "prices.tex",
    "details.tex",
    "paired-quality.png",
)


def save(path, contents):
    path.write_bytes((contents.rstrip() + "\n").encode("utf-8"))


def table(output, name, columns, headings, rows):
    def line(values):
        return " & ".join(map(str, values)) + r" \\"

    lines = [
        r"\begin{tabular}{" + columns + "}",
        r"\toprule",
        line(headings),
        r"\midrule",
        *(line(row) for row in rows),
        r"\bottomrule",
        r"\end{tabular}",
    ]
    save(output / name, "\n".join(lines))


def make_paper(output):
    archive = BASE / "formal"
    manifest = read(archive / "manifest.json")
    reproduction = read(BASE / "reproduction.json")
    if (
        digest(archive / "observations.jsonl")
        != reproduction["grading"]["archive_sha256"]
    ):
        raise ValueError(
            "Confirmation archive differs from the release's regraded submissions."
        )
    metrics = summarize(archive)
    if metrics != read(archive / "metrics.json") or metrics["phase"] != "formal":
        raise ValueError("Manuscript requires the verified formal analysis.")
    groups = metrics["summary"]
    pairs = metrics["paired_scoped_minus_full"]
    all_tasks = groups["all"]
    main = pairs["all"]
    n = main["n"]
    passes = [all_tasks[a]["verified"] for a in ARMS]
    observations = [
        json.loads(line)
        for line in (archive / "observations.jsonl").read_bytes().splitlines()
    ]
    triples = {}
    for r in observations:
        triples.setdefault(r["case"], {})[r["arm"]] = r
    shorter = sum(
        t["scoped"]["packet_chars"] < t["full"]["packet_chars"]
        for t in triples.values()
    )
    if not main["noninferior"] or main["cost_ratio_upper_95"] is not None:
        raise ValueError(
            "The interpretation in the manuscript is specific to the frozen study."
        )

    output.mkdir(exist_ok=True, parents=True)
    save(output / "main.tex", (SOURCE / "main.tex").read_text(encoding="utf-8"))
    display_title = TITLE.replace("? ", r"?\protect\\")
    save(
        output / "frontmatter.tex",
        "\n".join(
            (
                rf"\title{{{display_title}}}",
                rf"\author{{{AUTHOR}}}",
                rf"\hypersetup{{pdftitle={{{TITLE}}},pdfauthor={{{AUTHOR}}}}}",
            )
        ),
    )
    abstract = (
        "When an agent asks a second model for advice, the format of the handoff can change both "
        "what the advisor sees and how much work the executor must do. We compared a complete "
        "checkpoint, an executor-written summary, and a structured packet on matched programming, "
        f"chart-reading, and English transcription tasks. Each of {n} tasks received all three "
        "conditions with the same advisor route within a task. The advisor received the original "
        "media in every applicable condition. By the registered deadline, the full, summary, and "
        f"structured arms produced {passes[0]}, {passes[1]}, and {passes[2]} verified files, respectively. "
        f"The paired structured-minus-full difference was {100 * main['difference']:+.1f} percentage points "
        f"(one-sided 95% lower bound {100 * main['lower_95']:+.1f}), meeting the prespecified "
        f"noninferiority margin of -5 points. Structured packets were shorter than the full checkpoint "
        f"on {shorter} of {n} tasks. Cost per verified file was higher for the structured arm "
        "than for the full arm on programming and chart tasks. Missing audio prices preclude an "
        "overall cost estimate. The quality result applies to the handoff policy averaged across "
        "the tested workload. It does not isolate the effect of shortening the input."
    )
    save(output / "abstract.tex", abstract.replace("%", r"\%"))
    save(
        output / "arxiv-metadata.txt",
        f"Title: {TITLE}\nAuthors: {AUTHOR}\nPrimary category: cs.AI\n"
        "Potential cross-list: cs.SE\n\nAbstract:\n" + abstract,
    )
    quality = []
    packet = []
    for kind in GROUPS:
        c = pairs[kind]
        g = groups[kind]
        quality.append(
            [
                LABEL[kind],
                c["n"],
                *(g[a]["verified"] for a in ARMS),
                c["scoped_only"],
                c["full_only"],
                f"{100 * c['difference']:+.1f}",
                f"{100 * c['lower_95']:+.1f}",
            ]
        )
        subset = [
            t
            for t in triples.values()
            if kind == "all" or t["full"]["modality"] == kind
        ]
        medians = [median(t[a]["packet_chars"] for t in subset) for a in ARMS]
        short = sum(
            t["scoped"]["packet_chars"] < t["full"]["packet_chars"] for t in subset
        )
        packet.append(
            [LABEL[kind], *(f"{v:,.1f}" for v in medians), f"{short}/{len(subset)}"]
        )
    table(
        output,
        "delivery.tex",
        "lrrrrrrrr",
        [
            "Tasks",
            "$n$",
            "Full",
            "Summary",
            "Scoped",
            "$W$",
            "$L$",
            r"$\Delta$",
            "Lower",
        ],
        quality,
    )
    table(
        output,
        "packets.tex",
        "lrrrr",
        ["Tasks", "Full", "Summary", "Scoped", "Scoped shorter"],
        packet,
    )
    usage = []
    for a in ARMS:
        g = all_tasks[a]
        usage.append(
            [
                LABEL[a],
                *(
                    f"{g[role][key]['known_total']:,}"
                    for role in ("executor", "advisor")
                    for key in ("input_tokens", "output_tokens")
                ),
                g["advisor"]["input_tokens"]["known_rows"],
                f"{g['median_seconds']:.1f}",
            ]
        )
    table(
        output,
        "usage.tex",
        "lrrrrrr",
        ["Arm", "Exec. in", "Exec. out", "Adv. in", "Adv. out", "Coverage", "Time (s)"],
        usage,
    )
    price = []
    for kind in ("text", "image"):
        g = groups[kind]
        if any(g[a]["cost_known"] != g[a]["n"] for a in ARMS):
            raise ValueError(
                "Price table requires complete accounting within each included stratum."
            )
        unit = [100 * g[a]["known_dollars"] / g[a]["verified"] for a in ARMS]
        price.append(
            [
                LABEL[kind],
                *(f"{x:.3f}" for x in unit),
                f"{unit[2] / unit[0]:.3f}",
                f"{pairs[kind]['cost_ratio_upper_95']:.3f}",
            ]
        )
    table(
        output,
        "prices.tex",
        "lrrrrr",
        ["Tasks", "Full", "Summary", "Scoped", "Ratio", "Upper"],
        price,
    )
    design = manifest["sample_size"]
    pilot_n = len(read(BASE / "pilot/manifest.json")["cases"])
    either = sum(
        t["scoped"]["verified_by_deadline"] != t["summary"]["verified_by_deadline"]
        for t in triples.values()
    )
    statuses = [all_tasks[a]["advisor_status"] for a in ARMS]
    if any(
        r["advisor"]["status"] != "ok" and r["modality"] != "audio"
        for r in observations
    ):
        raise ValueError(
            "Review modality-specific failures before using this interpretation."
        )
    data = {
        "PilotN": pilot_n,
        "PilotDiscordant": design["discordant"],
        "DesignTarget": design["uncapped_tasks"],
        "StudyN": n,
        "Deadline": manifest["deadline_seconds"],
        "Assignments": len(observations),
        "TotalPass": sum(passes),
        "Submitted": sum(r["submitted"] for r in observations),
        "LateCount": sum(
            r["seconds"] > manifest["deadline_seconds"] for r in observations
        ),
        "SummaryDisagreement": either,
        "ScopedOnly": main["scoped_only"],
        "FullOnly": main["full_only"],
        "UnusableFull": n - statuses[0]["ok"],
        "UnusableSummary": n - statuses[1]["ok"],
        "UnusableScoped": n - statuses[2]["ok"],
        "PriceCoverage": all_tasks["scoped"]["cost_known"],
        "ExecUsageCoverage": min(
            all_tasks[a]["executor"]["input_tokens"]["known_rows"] for a in ARMS
        ),
    }
    save(
        output / "details.tex",
        "\n".join(rf"\newcommand{{\{name}}}{{{val}}}" for name, val in data.items()),
    )
    draw_quality(pairs, output / "paired-quality.png")


def bundle(output, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w") as archive:
        for name in FILES:
            info = zipfile.ZipInfo(name, date_time=(2026, 11, 27, 0, 0, 0))
            archive.writestr(
                info, (output / name).read_bytes(), compress_type=zipfile.ZIP_DEFLATED
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-zip", type=Path)
    args = parser.parse_args()
    make_paper(args.output)
    if args.source_zip:
        bundle(args.output, args.source_zip)
