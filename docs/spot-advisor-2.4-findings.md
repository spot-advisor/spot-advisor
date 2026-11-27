# What can go wrong between deciding to ask and accepting delivery?

The highest-priority 2.4 change is to preserve the requirements that determine delivery. A reviewer given a trustworthy observation of the correct file version still approved a defective payroll roster and a defective event invitation when the deciding line of the brief was missing. The failure repeated twice for each file. Returning that line to the packet changed all four verdicts to REWORK. [Decision and release plan](spot-advisor-2.4-plan.md)

We tested the published 2.3 skill at `14eacdc`. The screen contains 96 first-action decisions, 16 delivery reviews, 32 prospectively assigned follow-up reviews and 12 actual file repairs. All 156 assigned trials returned usable records. Three route smokes were excluded. These are repeated trials on six decision scenarios, two document families and three repair tasks. The author designed the cases and grading keys. There is no population-wide rule-following estimate here. [Run manifests](../eval/results/spot24-gap-audit/manifest.json) · [All observations](../eval/results/spot24-gap-audit/observations.jsonl)

## Delivery judgments depend on what the host sends

The exported CSV had either the approved header order or the two columns reversed. The HTML invitation had either the approved French link label or a different label on the correct link. A parser read the saved file. The host had the full brief, the acceptance criterion, the artifact revision and a registered digest for the parser log in every condition. The model saw only the scoped packet. All 48 review receipts echoed the correct artifact revision.

In the first 16 reviews, all eight valid artifacts received NEEDS_EVIDENCE, including the four whose packet quoted the entire requirement. For the defective artifacts the reviewer requested rework three times and more evidence five times. In the fully specified, valid cases the replies questioned whether the observed contents belonged to the submitted revision. The log digest and artifact digest identify different files. [Original protocol](spot-advisor-2.4-protocol.md) · [Archived reviewer packets and responses](../eval/results/spot24-gap-audit/observations.jsonl)

The follow-up added the parsed artifact's SHA-256 to the observation and updated the digest of the resulting log. The artifact bytes stayed fixed within each pair. Both variants ran fresh in randomized order. Each cell below contains two artifact families with two repeats each. [Follow-up protocol](spot-advisor-2.4-provenance-control.md)

| Actual artifact | Deciding requirement in packet | Original observation | Observation names artifact SHA-256 |
| --- | --- | --- | --- |
| Defective | Omitted | 4 need evidence | **4 accept** |
| Defective | Quoted | 2 rework, 2 need evidence | **4 rework** |
| Valid | Omitted | 2 accept, 2 need evidence | 4 accept |
| Valid | Quoted | 4 need evidence | 2 accept, 2 need evidence |

Both valid, fully specified invitations passed with the label. Both equivalent rosters still received NEEDS_EVIDENCE. Their responses compared the log digest prefix to the artifact revision despite the correct full artifact digest in the same observation (records `p005` and `p017`). An explicit role for each identifier and machine-checked coverage of the acceptance conditions address different errors. A preflight that required the omitted line in these fixtures would have caught all four false approvals. It would also have asked for the missing criterion on valid files with incomplete packets. Its usability and effect on fresh tasks still need measurement.

## Choosing the right tool and asking an admissible question

The policy screen crossed two small changes. One added a rule about pending checks. The other replaced a generic local rejection with its format requirements and an instruction to retry only if the decision remained unresolved. Only two scenarios contained an error message. Other feedback cells repeated the same prompt. Scoring required the exact next tool, then a locally admissible question and nonempty evidence for required consultations. All consultations in this cohort were proposed calls. The harness replayed them locally against the published packet builder. [Scenarios and keys](../eval/spot24-checkpoints.json) · [Offline verifier](../eval/verify_gap_study24.py)

| Added pending rule | Feedback on rejection | Correct next action | Action and form |
| --- | --- | ---: | ---: |
| No | Generic | 17/24 | 9/24 |
| No | Actionable | 22/24 | 14/24 |
| Yes | Generic | 17/24 | 10/24 |
| Yes | Actionable | 20/24 | 15/24 |

- **A newer instruction can settle a rejected question.** After the user approved the exact label, 0/8 generic-feedback trials applied it immediately, versus 5/8 with actionable feedback. When the decision genuinely still needed the advisor, correct action plus form rose from 2/8 to 5/8. The treatment changed multiple pieces of feedback together. A held-out comparison should isolate which piece helped.
- **Language changes whether the chosen consultation can run.** Among correctly selected, required calls, 17/22 English forms and 1/24 Chinese forms passed. Replaying the same arguments with only a terminal full-width question mark normalized raised Chinese acceptance to 8/24. That recovers seven forms locally. It leaves sixteen forms rejected and says nothing about whether subsequent advice would solve the task. A separate preview rejected a natural short English question and accepted two English questions concatenated in one field. [Local probe outputs](../eval/results/spot24-local-probes.json)
- **More pending-check wording has no demonstrated benefit here.** All 16 relevant-pending cases chose inspection and all 16 irrelevant-pending cases chose consultation, across both wordings. These prompts explicitly described relevance and tool availability. In the earlier end-to-end replication, both revised repeated-failure runs escalated with a local refresh queued. The discrepancy calls for a timed state transition on a fresh task. The static scenario cannot reproduce it. [Earlier rule experiment](spot-advisor-2.3-rule-results.md)

## Routine work pays for instructions even when calls stay local

Luna Max repaired three small Python fixtures twice per arm. Each final module was independently run in a fresh, network-isolated container against the original public and held-out checks. All 12 stored patches also passed a second replay from the exported bytes. The optional arm exposed both the full skill and the consultation tool, so the measured difference belongs to that combined configuration. [Coding protocol](spot-advisor-2.4-coding-control.md)

| Workflow | Independently correct and executor-verified | Advisor dispatches | Executor input + output tokens | Median elapsed per assignment |
| --- | ---: | ---: | ---: | ---: |
| Local tools | 6/6 | 0 | 22,291 + 4,817 = 27,108 | 23.48 s |
| Local tools with optional 2.3 advisor | 6/6 | 0 | 45,211 + 5,530 = 50,741 | 24.22 s |

Both arms had complete usage. The optional configuration used 23,633 additional reported tokens over these six assignments. Prompt injection on every task in this harness can be more expensive than discovery on a native host. The small cohort gives no dependable latency estimate or billing comparison.

The earlier 2.3 seed-972 comparison illustrates a different trade-off. Revised wording reduced local attempts from 18 to 6 and executor tokens from 127,364 to 86,387, while actual advisor dispatches rose from 3 to 6 and frontier tokens from 8,844 to 17,812. Terminal verification improved from 9/10 to 10/10. The primary rule-change gate remained unmet. [Prior results and uncertainty](spot-advisor-2.3-rule-results.md)

## Installation and handoff compatibility

- On the audited Windows host, the discoverable generic and Claude skill files and caller scripts matched the 2.2 release after newline normalization. Both lacked the published caller's scoped handoff and audio options. A third Codex-specific location contained a disabled skill filename and a different callable script. These observations identify disk copies and CLI capabilities. A given session's selected copy requires its own runtime receipt. [Machine probe without absolute paths](../eval/results/spot24-local-probes.json)
- With the same state, the ordinary v2 follow-up preview retained the acceptance condition and previous advice outcome in 527 characters. The scoped follow-up used 568 characters and omitted both. The host correctly flagged a registered missing update, rejected altered registered evidence and blocked an exhausted round. A 2,069-character first packet exceeded the existing follow-up limit when retried intact. Explicit coverage must fit the existing budgets or return a precise request for the missing evidence. [Preview harness](../eval/probe_handoff24.py)

## Reproduce the measurements

From the repository root, `python -B eval/verify_gap_study24.py` validates the observation digest, assignments and submitted file hashes and prints the tables. Add `--docker` to regrade the 12 stored modules in fresh containers using the cached image pinned in the run manifest. This takes no advisor credentials. Model routes used for the live screen were the Codex subscription executor `gpt-6-luna` at max effort and reviewer `gpt-6.2-astra`. The experiment did not qualify Claude, gateway, audio or image dispatch in this cohort.

To collect a fresh run, use a new directory for each command below. The original manifests preserve the actual job order, model settings and source or protocol hashes. Model responses can vary. The reviewed artifact bytes and submitted patch digests are deterministic within their corresponding saved records.

```sh
python -B eval/gap_study24.py policy eval/runs/fresh24/24-policy
python -B eval/gap_study24.py review eval/runs/fresh24/24-review
python -B eval/coding_control24.py eval/runs/fresh24/24-coding
python -B eval/provenance_control24.py eval/runs/fresh24/24-provenance
python -B eval/export_gap_study24.py eval/runs/fresh24 eval/runs/fresh24/archive
python -B eval/verify_gap_study24.py eval/runs/fresh24/archive --docker
```
