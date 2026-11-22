# Paired evaluation

## Spot Advisor 2.2

The [2.2 roadmap](../ROADMAP.md#spot-advisor-22-research-and-planning) defines the coding, checkpoint and visual comparisons. The [frozen coding cohort](study22-tasks.json) records task order and the peer subset. The coding runner pins the 2.1 helper, current source digest, model settings and balanced arm order in the run manifest.

```powershell
python -B eval/study22.py shakedown --run eval/runs/22-coding-shakedown
python -B eval/study22.py coding --run eval/runs/22-coding-formal
python -B eval/study22.py score --run eval/runs/22-coding-formal --output eval/runs/22-coding-score
python -B eval/checkpoint_pilot.py --study22 --output eval/runs/22-checkpoint-comparison
python -B eval/checkpoint_report.py eval/runs/22-checkpoint-comparison --matched
```

Use an unused scorecard output directory for each snapshot. The exporter produces sanitized records, a JSON scorecard, and a verification and token chart. The quality gate uses a conservative lower bound on the paired pass-rate difference from the two discordance proportions. Token ratios resample whole issue pairs. Missing receipts suppress the complete-use comparisons. Partial model totals remain visible.

When the coding runner has stopped, use `python -B eval/resume_study22.py --run eval/runs/22-coding-formal`. It verifies the frozen source, task definitions and both advisor variants. It runs arms that have yet to start and preserves existing results and interrupted attempts. Any post-interruption patch regrades belong in a separate recovery record because the original delivery time and total usage can remain unknown. The manuscript builder reports the recovered grade, available pre-interruption request receipts and unresolved dispatches separately from the timed comparisons.

The exploratory checkpoint runner records each verification command and its result in `prefix-checks.json`. The initial Git diff check alone is ineligible for consultation. Review the recorded commands before interpreting a trigger as a real test failure. The audit derives prefix stop categories from the recorded execution errors and keeps the independent patch grade. The runner records the same starting evidence and randomized continuation order for the focused and generic question arms.

New coding runs retain a `consultation_gate` reason and the referenced checkpoint in the sanitized tool events. Use the provider attempt journal to determine whether an admitted call reached dispatch. Earlier frozen runs have invocation counts and admission markers without per-invocation reasons.

New direct executor receipts also record the budget available when preparing a request and its socket timeout. For timeout exceptions, `deadline_reached_at_timeout` records whether the executor deadline had elapsed. The frozen study uses the journal offsets available at the time.

To diagnose NodeBB patch rejections after tests generate Redis persistence files, run `python -B eval/replay_redis_collisions22.py --run <recorded-run> --output eval/runs/22-redis-collision-audit`. Repeat `--run` to include peer runs. The audit first reproduces the rejection and checks that the colliding paths are untracked runtime files in the base image. It then independently grades a copy with those exact file sections removed. The audit preserves matching existing regrades and adds new cases. The main scorecard retains the original outcome.

Build a paper from a scored run with your own LaTeX manuscript using `eval/build_study22_paper.py --manuscript PATH`. See the [paper instructions](../paper/README.md).

For the pinned peer cases, `claude_native_pilot.py --public --case ID --output PATH` uses the same upstream grader and records wall time through grading. It requires an Anthropic API credential with Advisor access. Run second-opinion and PAL MCP in their normal review mode against the same starting issue, then include the executor applying the review in the measured workflow time. Use `peer22.py --peer second-opinion|pal-mcp --case ID --patch FILE --trace FILE --output PATH` to grade the resulting patch. The trace JSON records `product_revision`, `workflow_seconds` including the review and application, and `receipts` with observed usage or `null` if unavailable. Keep raw traces under `eval/runs/`. Claims of relative cost require comparable observed receipts.

The matched-model PAL experiment runs the pinned PAL `chat` tool with its [custom provider](https://github.com/BeehiveInnovations/pal-mcp-server/blob/7afc7c1cc96e23992c8f105f960132c657883bb1/docs/custom_models.md). Install that checkout's requirements plus `mcp==1.30.0` and `jsonschema` in an isolated environment. From this repo, use that environment's Python:

```powershell
python -B eval/pal22.py --peer-source <pinned-pal-checkout> --output eval/runs/22-pal-qualification
python -B eval/pal22.py --peer-source <pinned-pal-checkout> --case <frozen-peer-id> --output eval/runs/22-pal-peer
```

The adapter exposes one authorized, tool-free text call on loopback and sends it to Astra medium using the existing Codex login. The experiment includes PAL startup, the advisor reply and common Luna reconciliation in workflow time. It tests PAL's one-turn chat, with no file attachments, on the registered repair cases. The same grader independently grades the resulting patch. Use a different output for the marker check and the case run.

Native Claude receipts report executor usage at the top level and separately billed advisor usage in the advisor iterations. The peer audit counts each once, preserves missing usage after interrupted or failed requests, and estimates costs using the [published standard rates](https://platform.claude.com/docs/en/about-claude/pricing). Cache-write estimates assume five-minute retention. [Native Advisor receipt format](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool#usage-and-billing)

The 2.2 visual pilot renders an original sign-up page in isolated Chromium and checks the click target, clipping and confirmation. The following command uses local Docker and the evaluation Chromium image.

```powershell
python -B eval/visual22.py --output eval/runs/22-visual-fixture
```

To qualify a live image route, use a fresh output directory, pass `--model` with the exact supported model ID, and supply `SPOT_ADVISOR_VISION_BASE_URL` and `SPOT_ADVISOR_VISION_API_KEY` for an authorized HTTPS gateway. Select a gateway with both the token preflight and completion endpoints documented in the [install guide](../docs/install.md). Qualification checks the model receipt and a screenshot-only detail. Human first-click evaluation follows route qualification.

For the research-only Codex subscription route, qualify image ingestion and run the disjoint visual pilot:

```powershell
python -B eval/visual22.py --output eval/runs/22-visual-codex-probe --model gpt-6.2-astra --codex
python -B eval/visual22.py --output eval/runs/22-visual-luna-probe --model gpt-6-luna --codex --effort max
python -B eval/visual_pilot22.py --output eval/runs/22-visual-codex-pilot
python -B eval/visual_pilot22.py --output eval/runs/22-visual-luna-image-control --image-control-for eval/runs/22-visual-codex-pilot
```

The pilot starts all four arms from the same page for each of two cases. The two advisor arms ask the same question at the same effort and time budget. One adds a screenshot. The two-case design includes an already-clear control. The run records browser checks, latency and per-request usage. Give each reviewer one panel from `blinded/` and the screenshots it references. Keep the mapping in `review-key.json` private until reviews are complete. Functional checks and model replies supply evidence for the pilot. The first-click outcome comes from blinded reviewers.

The supplementary image control feeds the same starting screenshot directly to Luna Max. It is an exploratory arm linked to the original manifest and runs separately from the four-arm pilot. Use `--visual-image-control` with the original `--visual-pilot` path when building the paper.

To include the supplemental arm in blinded first-click review, generate five panels. Each workflow appears once per case, and case order is counterbalanced across panels. Give each reviewer only their assigned panel directory, including its blank response template. Keep the private key with the study owner. The score report includes responses per panel and coverage for every case and workflow so partial collection stays visible.

```powershell
python -B eval/visual_ratings22.py prepare --pilot eval/runs/22-visual-codex-pilot --control eval/runs/22-visual-luna-image-control --output eval/runs/22-visual-review-balanced
python -B eval/visual_ratings22.py score --key eval/runs/22-visual-review-balanced/private-key.json --output eval/runs/22-visual-review-tally.json reviewer-a.json reviewer-b.json
```

The runner drives the two Codex subscription arms through the same local tools. Model requests use the existing direct Responses transport. A separate App Server client reads plan and quota metadata. Docker holds task environments without credentials or outbound networking.

## Prepare

Use Python 3.11 or later, Docker with Linux containers, Git, and a Codex CLI session signed into ChatGPT. Install report dependencies with `python -m pip install jsonschema 'matplotlib>=3.8'`. From the repository root:

```powershell
git -c core.autocrlf=false clone --depth 1 --filter=blob:none --sparse https://github.com/scaleapi/SWE-bench_Pro-os.git eval/runs/swe-bench-pro-source
git -C eval/runs/swe-bench-pro-source sparse-checkout set v2
git -C eval/runs/swe-bench-pro-source rev-parse HEAD
```

Use the revision listed in [the task manifest](swe_tasks.json). If upstream has advanced, fetch that revision and check it out in the evaluation clone. Selected images are fetched on demand. The Chromium image is built locally from [browser.Dockerfile](browser.Dockerfile).

## Run and regenerate

```powershell
python -B eval/run.py --stage pilot --output eval/runs/pilot
python -B eval/report.py eval/runs/pilot --output eval/runs/pilot-report
python -B eval/run.py --stage formal --output eval/runs/formal
python -B eval/report.py eval/runs/formal --output eval/runs/formal-report
```

Use `--suite diagnostic` or `--suite browser` for a targeted integration run. `--workers` runs up to four independent pairs concurrently, with the two arms of each pair still executed in order. Concurrency is part of the frozen run manifest. A run skips completed arms when resumed. Inspect any interrupted trial's journal before choosing whether to repeat it. The journal preserves dispatches, provider errors and unsuccessful outcomes alongside completed work. Use `regrade.py` to apply revised pilot grading to existing artifacts without new model calls. For a finished study, export the sanitized `evidence.jsonl`, metrics, and manifest with the frozen revision.

The report emits LaTeX from the captured records. A complete formal study requires paired observations for all 50 public coding tasks, 16 diagnostics and eight browser scenarios, with verified model identity and token attribution. See the [research protocol](../docs/research-protocol.md) and [schema](telemetry_schema.json).

Exported runs can be analyzed offline. Their manifests and JSONL preserve the run order, individual attempts, token fields, verification outcomes and timing. Public exports omit provider request IDs, account windows, raw error messages and tool payloads. Use `--deadline-seconds` (repeatable), `--service-target` and `--luna-weight` to evaluate another operating point. The time cutoff uses `wall_seconds` through the final independent grade. The `bootstrap_defined` count shows how many of 10,000 resamples have a defined crossover when the point estimate exists.
