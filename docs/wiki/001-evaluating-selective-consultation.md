# Evaluating selective consultation

Draft research note, 2026-11-26. The methods below informed the [2.4 diagnostics](../spot-advisor-2.4-findings.md) and [confirmation plan](../spot-advisor-2.4-plan.md).

| Primary research | Finding or method relevant here | Application to Spot Advisor |
| --- | --- | --- |
| Sowietzki et al., 2026 | ARIA compares selective consultation with random placement at matched advice volume. | Hold the advice budget comparable when testing whether the trigger identifies a better moment to ask. [Ask Your Advisor](https://arxiv.org/abs/2610.13870) |
| Kim et al., 2026 | The authors separately model deciding to ask, formulating the query and using the answer. | Record the first decision, form admission, reviewer judgment, execution and verified final state. This locates where a rule change helped. [Learning to Ask](https://arxiv.org/abs/2610.01236) |
| Yao et al., 2024 | The authors compare the final database state with the intended result and measure consistency across repeated trials. | Grade artifact bytes or state after execution. Separate distinct tasks from repeated attempts on the same task. [τ-bench](https://arxiv.org/abs/2406.12045) |
| Jimenez et al., 2023 | SWE-bench evaluates real repository issue fixes with tests. | Preserve submitted patches and execute original and held-out checks in an isolated grader. [SWE-bench](https://arxiv.org/abs/2310.06770) |
| Zheng et al., 2023 | The authors document position, verbosity and other biases in model-based judging. | Blind the graders to the arm, randomize assignment order and use a parser or execution result whenever correctness has an objective definition. [MT-Bench and Chatbot Arena](https://arxiv.org/abs/2306.05685) |

The published performance effects concern the authors' own tasks and systems. The recommendations in the last column are design choices for Spot Advisor. To establish a product benefit, test whether the executor delivered an independently correct result with an acceptable end-to-end cost on its intended workload.
