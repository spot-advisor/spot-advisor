# Spot Advisor 2.2

**Give your coding agent a second opinion. Keep the fix in the same session.**

Your agent has reproduced a bug. Two fixes look plausible, and guessing wrong means another round of edits and tests. Spot Advisor lets it send a stronger model one focused question with the relevant code and test output. The advisor suggests a next step and a way to check it. Your agent makes the edit and runs the test.

[Install for Codex or Claude Code](docs/install.md) · [See a real reply](#what-one-consultation-looks-like) · [Read the research plan](#research)

## Try it offline

With Git and Python installed, run this from a terminal. If you already cloned the repo, run the last line from its root.

```sh
git clone https://github.com/stancsz/spot-advisor.git
cd spot-advisor
python skills/advisor/scripts/ask_expert.py --preview --protocol v2 --question "A config test expects False but gets the default. The code uses overrides.get(name) or defaults[name]. What should I change and how should I check it?"
```

Open the `packet` field to see the question, constraints, and verification target an advisor would receive. The preview runs locally after cloning, with zero model calls and no account or API key.

## What one consultation looks like

A config override explicitly sets `False`, but the test gets the default. The agent already has the failing test and the offending line. It asks whether to change the lookup or the fallback.

| What the advisor returned | How the agent can use it |
| --- | --- |
| **Verdict** | Check for key presence. A false value can be an explicit override. |
| **Do** | Use `overrides[name] if name in overrides else defaults[name]`. |
| **Verify** | Test that `False`, `0`, and `""` survive, and that an absent key uses the default. |
| **Risk** | An explicit `None` will now survive too. Check whether callers use it to request the default. |

This is condensed from a live reply. The agent can now run a targeted regression test and check the `None` contract before accepting the patch.

## Make your first live call

[Install the skill](docs/install.md), open a task in your coding agent, and give it a concrete fork:

> Use Spot Advisor on this failing test. Confirm the intended assertion ran. If two plausible fixes remain, preview a short packet with the failure and the two options. Ask one question that would change your next edit, then verify the advice locally.

For example, if the test fails because Redis is unavailable, the agent checks Redis and reruns the targeted assertion before spending a consultation. When a decision remains, the agent can preview the exact packet, including any code excerpt or log it selected. The advisor works from that packet without repository tools. The local session also records whether an interrupted call may have been sent, so it can avoid a duplicate.

- **Codex** uses an existing Codex login for text consultations, with no separate API key.
- **Claude Code** can use a signed-in local CLI for a text consultation.
- **Compatible gateways** can provide text and, with a vision-capable route, image consultations. See the [endpoint and credential requirements](docs/install.md#3-transport-setup---transport).

## Research

Stan Chen's working paper, *When Is Frontier Advice Worth Asking For?*, studies which coding decisions merit one frontier consultation. The evaluation compares direct Astra execution, Luna Max alone, and Luna Max using two consultation policies on the same public repair assignments. It measures independent test outcomes, elapsed time, total and frontier-model tokens, and cost estimates that account for cache reads and writes.

The manuscript and its results are in private review. The evaluation code, task assignments, and study design are available here.

[Research overview](paper/README.md) · [2.2 roadmap](ROADMAP.md) · [Evaluation guide](eval/README.md) · [Skill source](skills/advisor/SKILL.md)
