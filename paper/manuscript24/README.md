# What Survives a Structured Handoff?

The Spot Advisor 2.4 manuscript studies how three handoff formats affect the files an executor delivers. It reports the registered paired quality endpoint, the uncertainty in each modality, packet lengths, measured token use and API-equivalent cost per verified delivery on completely priceable strata. Download the [paper (PDF)](https://github.com/spot-advisor/spot-advisor/releases/download/paper-2.4-v1/spot-advisor-2.4-manuscript.pdf), the [self-contained TeX archive](https://github.com/spot-advisor/spot-advisor/releases/download/paper-2.4-v1/spot-advisor-2.4-manuscript-tex.zip) or the [plain-text arXiv metadata](https://github.com/spot-advisor/spot-advisor/releases/download/paper-2.4-v1/arxiv-metadata.txt).

## Rebuild the paper

Use a clean checkout of the paper tag `paper-2.4-v1`. From its root, run the following commands. The builder checks the formal observations against the [release reproduction record](../../eval/results/spot24-release/reproduction.json), recomputes the registered metrics and supplies the figure, four tables, abstract and sample counts. It also exports the title, author, category and plain-text abstract to `arxiv-metadata.txt` outside the source archive.

~~~sh
python -m pip install -r paper/compression24/requirements.txt
python -B eval/build_manuscript24.py eval/runs/paper --source-zip eval/runs/spot-advisor-2.4-manuscript-tex.zip
cd eval/runs/paper
pdflatex -no-shell-escape -halt-on-error main.tex
pdflatex -no-shell-escape -halt-on-error main.tex
~~~

Alternatively, extract the downloaded source ZIP into an empty directory and run the last two commands there. The bibliography is included in the main TeX file. The paper uses standard LaTeX packages and a PNG figure. The upload entry point is `main.tex`. [arXiv TeX preparation](https://info.arxiv.org/help/submit_tex.html) · [Metadata instructions](https://info.arxiv.org/help/prep.html)

## Evidence behind the claims

| Result | Evidence or calculation |
| --- | --- |
| Verified delivery, paired lower limits and cost-ratio upper limits | [Archived metrics](../../eval/results/spot24-release/formal/metrics.json) and [registered analysis](../../eval/compression_report24.py) |
| Median packet lengths, matched differences and example files | [Per-assignment records](../../eval/results/spot24-release/formal/observations.jsonl) and [paper builder](../../eval/build_manuscript24.py) |
| Cost per verified file | Sum every priceable request in the stratum and divide by that arm's successful assignments in the same stratum. The [rate card](../../eval/pricing.json) supplies historical API-equivalent prices. |
| Independence of source items and budget | [Assignment manifest and source identifiers](../../eval/results/spot24-release/formal/manifest.json) and [sampling protocol](../../docs/compression-confirmation-24.md) |
| Regrading original submissions | [Offline replay guide and upstream data attribution](../compression24/README.md) |

The [approved plan](../../docs/spot-advisor-2.4-release-and-paper-plan.md) records the product and study decisions. Software, original evidence draft, and frozen data remain in [v2.4.0](https://github.com/spot-advisor/spot-advisor/releases/tag/v2.4.0). The paper has its own version so readers can cite the manuscript and the tested software separately.
