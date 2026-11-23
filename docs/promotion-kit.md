# Spot Advisor 2.2 promotion kit

Prepared for launch from the official organization repository and Pages site. Nothing in this file has been posted or submitted.

## Position

**One focused frontier-model question at the decision point. Then the coding agent continues and verifies locally.**

The best first audience is developers using Codex, Claude Code, or another coding agent who have checked the obvious local causes and still have two materially different next actions. Start the story with that moment and the previewable packet. Use the study as supporting context, not as a promise that the tool wins on cost or quality.

## Evidence guardrails

- The 48-assignment public comparison produced 39 independently graded passes in each workflow. Astra passed 39 tasks within 15 minutes; Luna Max with Spot Advisor passed 31.
- Recorded frontier tokens were much lower for the Spot Advisor workflow, while recorded total tokens were higher. The API-equivalent estimates cover only requests with priceable receipts: $82.82 vs $4.22. Missing receipts mean full-run cost and net savings are unknown.
- The study did not establish comparable repair quality within its prespecified margin. The 2.2 manuscript and remaining comparisons are still in review.
- Do not claim “saves 99%,” lower total tokens, equal quality, best-in-class performance, or published 2.2 results.
- Do not position image consultation as a finished feature. Keep it experimental until the visual route and blinded evidence are ready.

Primary evidence: [README results](../README.md#research-tokens-price-estimates-and-verification), [metrics](../eval/results/2026-11-20/formal-collection/metrics.json), [manifest](../eval/results/2026-11-20/formal-collection/manifest.json), and [receipts](../eval/results/2026-11-20/formal-collection/evidence.jsonl).

## Channel order and readiness

| Order | Channel | Use | Readiness |
| --- | --- | --- | --- |
| 1 | GitHub README and Pages site | Show the decision moment, run the no-call preview, install the skill, then inspect the evidence. | Live at [spot-advisor.github.io](https://spot-advisor.github.io/); source of truth is [spot-advisor/spot-advisor](https://github.com/spot-advisor/spot-advisor). |
| 2 | Skills CLI / skills.sh | Offer a direct install command. Verify the skill is discoverable before adding a directory badge; install counts are discovery signals, not endorsements. | Install command is prepared; indexing is not claimed. |
| 3 | OpenAI Developer Showcase | Submit the public repo and Pages URL. Its form requests a public project URL or hosted try link and setup steps. | Draft below; links are live. |
| 4 | Codex Show-and-Tell | Share the runnable example and ask for feedback on when the agent should consult. | Draft below; post only after demo acceptance. |
| 5 | LinkedIn | Share the builder's concrete problem and the honest study trade-off. | Draft below; do not cross-post identical copy everywhere. |
| 6 | r/AgentsOfAI weekly project showcase | A targeted community post with the demo and one technical question. | Recheck the current weekly-thread rules on posting day. |
| Hold | Hacker News Show HN | Revisit only if the restriction lifts and the demo is easy to try. | As checked Nov 22, 2026, the official page says Show HNs are temporarily restricted. |

The OpenAI Developer Showcase accepts demos and open-source projects ([submission form](https://openai.com/form/showcase-submission/)); Codex has an active [Show-and-Tell discussion category](https://github.com/openai/codex/discussions/categories/show-and-tell). Skills CLI syntax and discovery behavior are documented in the [Skills CLI reference](https://github.com/vercel-labs/skills/blob/main/docs/commands.md) and [skills.sh docs](https://www.skills.sh/docs). Hacker News' current [Show HN update](https://news.ycombinator.com/showlim) should be checked again before launch.

## Drafts

### OpenAI Developer Showcase

**Title:** Spot Advisor — one focused frontier consultation for coding agents

**Tagline:** When a coding agent has evidence but still faces two plausible next moves, Spot Advisor helps it ask one focused question and return to local execution.

**Description:** Spot Advisor is an open-source skill for coding agents. The executor checks the issue, selects a compact evidence packet, and previews it before sending. A frontier advisor returns one recommendation, a verification step, and a risk; the executor keeps control of edits and local tests. The repository includes an offline preview that makes no model call, setup instructions for Codex, Claude Code, and compatible gateways, and a reproducible research package. The current public study has missing usage receipts and did not establish equivalent repair quality, so its cost figures are limited to priceable requests.

**Setup:** Clone the public repository, run the offline preview command in the README, and inspect the packet. For live use, install the skill with the Skills CLI and configure one supported advisor route using the setup guide.

**Project URL:** https://github.com/spot-advisor/spot-advisor · **Try it:** https://spot-advisor.github.io/

### Codex Show-and-Tell

I built Spot Advisor for the point where a coding agent has run the relevant check but still has two plausible next moves.

It helps the agent package the evidence, ask one focused question, and get a next action plus a check. The agent still makes the edit and verifies it.

The offline preview is in the repo and makes no model call. The public study is mixed: both workflows passed 39 of 48 tasks, Astra passed more within 15 minutes, and the Spot Advisor workflow used more recorded total tokens. The receipt gaps mean I can’t claim net savings or equivalent quality yet.

I’d like feedback on one thing: what evidence should make an agent ask for advice instead of continuing locally?

Links: [repository](https://github.com/spot-advisor/spot-advisor) · [official site](https://spot-advisor.github.io/) · [demo](https://github.com/spot-advisor/spot-advisor/tree/main/demo)

### LinkedIn

I built Spot Advisor for a specific moment: the coding agent ran the check, the evidence is real, and there are still two reasonable next steps.

It gives the agent a way to ask one focused question, see what would be sent, and get back a next action with a check. The agent keeps the code changes and local verification.

The preview is free to try and makes no model call. The research is still mixed: equal final passes in one 48-task study, fewer recorded frontier tokens, more recorded total tokens, and missing receipts. I can’t call that proven net savings or equal quality.

Repo: https://github.com/spot-advisor/spot-advisor · Demo: https://spot-advisor.github.io/

If you use coding agents, what would make you decide it’s time to ask for a second opinion?

## Measurement

- Use GitHub traffic and referral data where available, Skills CLI aggregate install counts if the project is indexed, and voluntary feedback from first-time users.
- Do not add hidden site analytics or product telemetry for this launch.
- Ask three developers who did not build the skill to run the offline preview and follow the live setup. Record time to first preview, whether they understand the packet and trigger, where they get stuck, and whether the final recommendation can be verified.
- Treat stars and impressions as reach indicators. The key signal is a new user completing a preview or verified live consultation without maintainer help.
