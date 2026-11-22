# Spot Advisor research

*When Is Frontier Advice Worth Asking For?* is a working manuscript by **Stan Chen**. It examines when a coding agent should ask for one frontier-model judgment and how the resulting workflow changes independent verification, elapsed time, token use, and estimated cost.

The 2.2 study compares four workflows on the same repair assignments. The evaluation records executor and advisor usage, including cached inputs, and grades final patches with the same upstream tests. A separate study design compares question formats from a common saved decision point. The [roadmap](../ROADMAP.md) describes the peer comparisons and visual pilot.

The 2.2 manuscript and replication data are in private review. The public repository includes a LaTeX scaffold, run commands, and the task manifest.

[Research protocol](../docs/research-protocol.md) · [Task assignments](../eval/study22-tasks.json) · [Evaluation guide](../eval/README.md)

## Original paired study

The [arXiv working draft (PDF)](spot-advisor-preprint.pdf) covers the earlier two-workflow study shown in the project README. The [run manifest](../eval/results/2026-11-20/formal-collection/manifest.json), [sanitized receipts](../eval/results/2026-11-20/formal-collection/evidence.jsonl), and [reported metrics](../eval/results/2026-11-20/formal-collection/metrics.json) are available here.

From the repository root, with Python, `jsonschema`, and `matplotlib` installed, rebuild the study report offline.

```sh
python -B eval/report.py eval/results/2026-11-20/formal-collection --output eval/runs/readme-reproduction
```

The [chart generator](../eval/render_readme_charts.py) documents how to regenerate the localized README charts. It prices the recorded requests with the [study rate card](../eval/pricing.json).

## Verify a 2.2 draft

With an extracted replication package, run `python -B analysis/replication22.py` from its root. To regrade a selected patch, supply the clean upstream benchmark checkout at the package's pinned revision, the task image, and a new output directory. Use the README included in the package for the exact command.

To generate a draft from your own scored run, provide the LaTeX source and a fresh output directory.

```sh
python -B eval/build_study22_paper.py eval/runs/22-coding-score --manuscript path/to/manuscript.tex --output eval/runs/draft
```

The builder expects a title, author, and abstract, along with inputs for the generated sections and figures. It uses Tectonic to compile the source, records arXiv metadata, and creates a reproducibility archive. The public [LaTeX scaffold](main.tex) can be built separately with `pdflatex` and `bibtex`.
