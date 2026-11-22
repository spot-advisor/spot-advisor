# Spot Advisor research

*When Is Frontier Advice Worth Asking For?* is a working manuscript by **Stan Chen**. It examines when a coding agent should ask for one frontier-model judgment and how the resulting workflow changes independent verification, elapsed time, token use, and estimated cost.

The 2.2 study compares four workflows on the same repair assignments. The evaluation records executor and advisor usage, including cached inputs, and grades final patches with the same upstream tests. A separate study design compares question formats from a common saved decision point. The [roadmap](../ROADMAP.md) describes the peer comparisons and visual pilot.

The manuscript and replication data are in private review. The public repository includes a LaTeX scaffold, run commands, and the task manifest.

[Research protocol](../docs/research-protocol.md) · [Task assignments](../eval/study22-tasks.json) · [Evaluation guide](../eval/README.md)

## Verify a 2.2 draft

With an extracted replication package, run `python -B analysis/replication22.py` from its root. To regrade a selected patch, supply the clean upstream benchmark checkout at the package's pinned revision, the task image, and a new output directory. Use the README included in the package for the exact command.

To generate a draft from your own scored run, provide the LaTeX source and a fresh output directory.

```sh
python -B eval/build_study22_paper.py eval/runs/22-coding-score --manuscript path/to/manuscript.tex --output eval/runs/draft
```

The builder expects a title, author, and abstract, along with inputs for the generated sections and figures. It uses Tectonic to compile the source, records arXiv metadata, and creates a reproducibility archive. The public [LaTeX scaffold](main.tex) can be built separately with `pdflatex` and `bibtex`.
