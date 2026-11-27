# Reproduce the 2.4 multimodal evidence draft

The [LaTeX entry point](main.tex) and its generated inputs are a writing handoff. The software tag identifies the tested release; the [preregistration](../../docs/compression-confirmation-24.md) describes the matched task design, two calibration cohorts and confirmation gate. The earlier paired-repair PDF in the parent directory covers a different experiment.

## Rebuild without model access

Use a clean checkout of the tag, Python 3.12 or newer and the [research dependencies](requirements.txt). The tables and plot read the archived final submissions and receipt counts. The full reproduction also needs the pinned public dataset downloads and the Docker image recorded in the run manifest.

```sh
python -m pip install -r paper/compression24/requirements.txt
python -B eval/compression_report24.py verify eval/results/spot24-release/formal
python -B eval/build_compression_paper24.py eval/results/spot24-release/formal eval/runs/rebuilt-paper
tectonic eval/runs/rebuilt-paper/main.tex
```

The last command compiles a fresh PDF. The source folder contains the main TeX file, two generated tables, one generated results paragraph and one PNG figure. References are in the TeX file. These files can also be compiled together with PDFLaTeX. For subsequent editing, copy the six files together. [arXiv's TeX instructions](https://info.arxiv.org/help/submit_tex.html)

```sh
python -B eval/compression_inputs24.py eval/runs/source-cache eval/runs/replayed-inputs
python -B eval/replay_compression24.py eval/results/spot24-release/formal eval/runs/replayed-inputs
```

The replay verifies pinned input and grading hashes, re-runs the stored Python submissions in fresh, network-isolated containers and scores the image answers and speech transcripts from their saved bytes. Pull the exact `python_image` from the formal manifest once before running offline. Provider access is needed only to collect new responses. Live collection commands and the public qualification results are in the [release record](../../docs/release-2.4.md).

## Data provenance

The code uses the repository MIT license. Benchmark source data and derived material retain their upstream terms. Only the public text needed to inspect the experiments, model submissions, source identifiers and checksums travel in the release. The sampler retrieves original media from the pinned upstream files and converts FLEURS audio to PCM.

- Programming uses Google's sanitized MBPP test set, distributed under CC BY 4.0. The supplied public and hidden assertions grade the submitted Python files. [Dataset card](https://huggingface.co/datasets/google-research-datasets/mbpp)
- Chart reading uses ChartQA, distributed by Hugging Face M4 with a GPL-3.0 dataset card. One question is sampled per distinct image digest. The upstream authors describe their chart sources and benchmark design. [Dataset card](https://huggingface.co/datasets/HuggingFaceM4/ChartQA) · [Original paper](https://aclanthology.org/2022.findings-acl.177/)
- Speech uses Google's English FLEURS test recordings under CC BY 4.0, sampled by distinct sentence ID and transcript. Accuracy is measured on words after punctuation and case normalization. [Dataset card](https://huggingface.co/datasets/google/fleurs)

The authors' next step is manuscript writing and editorial review. The release supplies reproducible evidence regardless of whether the quality or cost hypotheses cleared their registered thresholds.
