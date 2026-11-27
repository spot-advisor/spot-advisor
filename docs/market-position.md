# Spot Advisor: market position and maturity

**Reviewed on November 27, 2026**<br>
**Product readiness.** Version 2.4 is a public software release. Automated first-use testers installed the skill in Codex and Claude Code, consulted on text, a chart and a WAV recording, and verified each resulting file. See the [host and backend qualifications](release-2.4.md) for the exact routes and their limits.<br>
**Market validation:** hypothesis. The repository has no measured external repeat-use cohort, customer demand, paid usage, or willingness-to-pay result.

## Audience and job

The intended user is a developer whose coding agent has run the relevant checks but still faces a decision with materially different plausible next steps. Spot Advisor helps the executor prepare a compact, reviewable evidence packet for one frontier judgment, then return to local implementation and verification.

## Alternatives and position

| Substitute | Trade-off | Spot Advisor's testable position |
| --- | --- | --- |
| Ask a frontier model directly or use a stronger model throughout | Fewer workflow components and often more complete task context, with potentially higher frontier usage. | Spot Advisor limits frontier involvement to a decision point and keeps the executor in control. The study does not establish equivalent repair quality or whole-workflow savings. |
| Continue with the current coding agent and local checks | No extra model boundary or handoff. | Useful when a local probe can settle the choice; the skill should only be used when advice can change the next action. |
| Multi-agent delegation or automated handoff | Can distribute work, but adds agents and coordination. | Spot Advisor is deliberately one compact, tool-free judgment step; it does not take over repository edits or verification. |

These are workflow substitutes, not a complete market map. No independent competitive or willingness-to-pay study has been run.

## Evidence and limits

- The public paired repair study covered 48 assignments. Both workflows had 39 independently graded final passes; by 15 minutes, Astra alone had 39 passes and Spot Advisor had 31.
- Priceable receipts estimated API-equivalent request cost of $82.82 for Astra alone and $4.22 for Luna Max with Spot Advisor. Four and eight requests respectively lacked complete usage receipts, so these values do not establish full-run cost or net savings.
- The Spot Advisor arm recorded fewer frontier tokens but more total tokens. Comparable repair quality was not established within the prespecified margin.
- GitHub snapshot on 2026-11-23: 0 stars, 0 forks, and no published release. This is not evidence of user adoption.

## Adoption friction and next experiment

Users must learn when to ask, choose what evidence can leave the local workflow, configure a frontier transport, and assess an answer without treating it as authority. The next decision-changing study should repeat decision-point consultations in real developer tasks and record the local alternative, packet size, advice adopted, independently verified outcome, total time, and complete priceable receipts. Include a no-consultation baseline and blind grading; use the existing [research protocol](research-protocol.md) and [roadmap](../ROADMAP.md) before fixing a final design.

## Product and research values

- **User control:** the executor chooses evidence, applies advice, and owns the result.
- **Focused help:** ask only when local checks leave a meaningful decision fork.
- **Inspectability:** preview the packet and return a checkable next action.
- **Reproducibility:** tie every quantitative statement to immutable evaluation artifacts.
- **Measured limits:** separate frontier tokens, total tokens, task quality, time, and priceable cost.

## Sources

- [Project README](../README.md)
- [Study protocol](research-protocol.md)
- [Formal study receipts](../eval/results/2026-11-20/formal-collection/evidence.jsonl)
- [Paper draft and reproduction notes](../paper/README.md)
- [GitHub repository](https://github.com/spot-advisor/spot-advisor)
