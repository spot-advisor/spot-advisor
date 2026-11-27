# Rule experiment and live replication

## Replication protocol fixed after pilot 971

The [original design](spot-advisor-2.3-rule-experiment.md) and all 52 assignments stand. The pilot uncovered a file collision on two corrected submissions and a connection refusal at the configured local audio endpoint. In a separate replication, keep both entire instruction texts, case definitions, media and original strict grader fixed. Give a corrected submission its own directory. Use the existing gateway on loopback port 4000 with the same audio model alias. Check that the exact audio can be reviewed before starting. Run all ten live pairs with fresh sessions, seed 972 and the same budgets. Count all assigned trials and report them separately. The 16-case primary experiment is closed.

For each arm report strict timing, final acceptance, terminal verification, all consult attempts, dispatch receipts and missing usage. Retain errors. The existing adoption criteria continue to apply. Results from either live cohort are descriptive because both use familiar fixtures.

The preflight reached the original audio model alias and returned the correct phrase and clip. A duplicated answer surrounded a previously unrecognized completion marker. Both replication arms use the same narrow parser change accepting this marker when the existing matching and schema checks pass. A replay of that exact response verifies the correction. Include provider and parser failures in the live scores.

## Results

The [evidence archive](../eval/results/2026-11-26-rule-following.json) contains both manifests, 72 assigned outcomes, aggregate scores, receipt counts and hashes of the raw comparisons. The current arm received the full existing 2.3 skill. The experiment replaced only its first numbered instruction in the revised arm. The freeze was local. The scenario author also wrote the grading keys.

| Cohort and outcome | Current 2.3 | Revised | Revised-only / current-only passes |
| --- | ---: | ---: | ---: |
| 971, new decisions meeting the exact action and format key | 10/16 | 13/16 | 3 / 0 |
| 971, action alone, exploratory breakdown | 13/16 | 13/16 | 1 / 1 |
| 971, first live cohort, strict timing | 6/10 | 8/10 | 3 / 1 |
| 971, first live cohort, final state and terminal verification | 7/10 | 8/10 | 1 / 0 |
| 972, repaired live replication, strict timing | 5/10 | 8/10 | 3 / 0 |
| 972, repaired live replication, final state and terminal verification | 9/10 | 10/10 | 1 / 0 |

The primary difference was 18.75 percentage points with an exact two-sided paired p-value of .25. Current 2.3 chose consultation on all five scenarios keyed for it and submitted two valid question forms. The revision consulted on four and submitted four valid forms. It inspected again on the scenario where the requested new evidence was ready and a follow-up was appropriate. Case 01 also rewarded immediate application over another inspection. This grading key measures a specific next action and gives no partial credit for a cautious alternative.

In cohort 971 the configured audio endpoint refused connections. Two current-arm retries also hit a write-once state file. A deterministic replay after fixing the collision showed that the corrected design question passed preview and the second audio question still failed local validation. The frozen grades retain these failures. In both cohorts, the historical timing grader can award correct timing even when an on-time request fails. Read timing alongside final verification and dispatch receipts.

In cohort 972 both arms had complete traces and known reported token usage. The revised arm made 6 consultation attempts, sent 6 requests, received 6 usable replies and spent 17,812 reported Frontier tokens. Current 2.3 made 18 attempts, sent 3 requests, received 3 usable replies and spent 8,844 Frontier tokens. Local format rejections fell from 7 to 0. Executor tokens fell from 127,364 to 86,387. These are reported input plus output tokens and do not establish a dollar-cost saving across model routes.

Both revised repeated-failure runs consulted after the first assertion failure while a local refresh was queued. Each eventually passed the fixture's final-value check. The verification covers the selected fixture value. Broader operational recovery and evidence preservation need independent checks. The default remains the existing 2.3 rule because the revision missed both the primary evidence threshold and the strict timing gate.

Recompute each table from the retained run directories with `python -B eval/report_rule_experiment23.py eval/runs/spot23-rule-paired-971` and `python -B eval/report_rule_experiment23.py eval/runs/spot23-rule-live-972`. The frozen rules and alternate wording are available in each run snapshot and the [proposal](../eval/trigger23-proposal.txt).

To run a fresh comparison, use the configured Luna executor and audio-capable gateway with the two [archived audio fixtures](../eval/fixtures/trigger23-audio). From the repository root:

```sh
python -B eval/rule_experiment23.py eval/runs/fresh-rule-pilot
python -B eval/rule_experiment23.py eval/runs/fresh-live --replicate-live eval/runs/fresh-rule-pilot
```

## Next comparison

Keep the existing trigger wording as the control. Cross two independently switchable changes in a four-arm comparison. The first gives a targeted local correction identifying the question and evidence fields. The second tells the executor to read the result of a pending, relevant check before escalating and to consult again only when fresh evidence leaves a decision unresolved. Include cases where waiting would add nothing, where new evidence settles the issue locally and where new evidence genuinely warrants a follow-up.

Use independent authors to draw held-out tasks from the intended work mix. Agree on acceptable next actions before running, including safe abstention when evidence is unavailable. Blind the graders to the arm. Make timely dispatch plus independently verified delivery the primary endpoint. Retain format compliance, redundant attempts, actual dispatches and model-specific usage as separate outcomes. Block randomization by task and analyze paired outcomes at the task level. Extra runs on a single task measure variance within that task.

As a planning example for **one prespecified two-arm contrast**, a 10-point effect with 15% revised-only successes and 5% current-only successes needs 168 independent pairs for about 80% power at two-sided .05 under the exact paired test. This calculation averages the exact conditional rejection probability over the binomial number of discordant pairs, using discordance probability .20 and revised wins on .75 of discordances. A 16-pair study has about 1.3% power under the same assumptions. A factorial interaction, multiple primary contrasts, or clustered tasks requires its own power calculation.
