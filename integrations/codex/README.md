# Optional Codex review gates

Install these two user-level command hooks when you want a planning consultation before implementation and a final QA consultation before finishing each Codex turn. The portable advisor skill also works without hooks.

Install the [advisor skill](../../docs/install.md), then run this from the checkout:

~~~sh
python integrations/codex/install.py
~~~

The installer appends a PreToolUse registration and a Stop registration to the user's Codex hooks.json. Existing handlers, including Second Brain, retain their positions. Reinstalling the same configuration is safe. Use --advisor PATH or --codex-home DIR for another installation. Open /hooks in Codex, inspect the two Spot Advisor entries, and trust their definitions. Start a fresh session to verify the effective configuration.

## Using the gates

1. Gather context. To start a review proactively, run the standalone shell command below, replacing the script path with the absolute installed path under your Codex home. The pre-tool hook returns the current gate ID. The first blocked edit also prints the exact invocation.
2. Preview the planning packet. Supply the intended outcome, proposed approach and evidence, and one question whose answer could change the approach. Read the preview, then run the same command without --preview. Read the returned advice before editing.
3. Finish the change and run the acceptance checks. Use the same command with qa in place of plan, giving the actual change and observed test results. Ask what evidence is missing or what would warrant rework. Follow up locally. A later covered edit invalidates that QA review.
4. Attempt to finish. Stop asks for a missing or stale QA review. Merely trying to stop again still requires QA. If the advisor call fails on that revision during a continuation, the hook reports QA incomplete and ends the retry cycle. This failure path never creates a successful review record. The user can always interrupt a turn.

~~~sh
python '/absolute/path/to/review_gate.py' context
python '/absolute/path/to/review_gate.py' review GATE_ID plan 'Goal' 'Proposed plan and observed evidence' 'Which assumption should I test first?' --preview
~~~

Use the printed path and single quotes around each text argument, including on PowerShell. Rephrase embedded apostrophes. Only include evidence suitable for the configured advisor. Both reviews share the advisor's per-turn session and its normal round budget. Duplicate requests for an already reviewed stage and revision return the prior receipt without dispatching. Previews, failed calls and responses for another revision cannot unlock a gate. A review records that a consultation occurred. The executor still owns fixes and verification.

## Coverage and storage

The edit gate covers apply_patch, shell execution, the local Node REPL, and matching filesystem MCP changes. Short standalone reads such as git status, Get-Content, Test-Path and rg --files can run before planning. Other shell commands, including compound commands, are treated as potentially mutating and invalidate QA. Run tests before final QA and wait for background writes to finish. Supported standalone Second Brain checkpoint commands can follow QA.

Records are scoped to Codex session and turn IDs. A new user turn requires fresh reviews. The adapter stores a local SQLite record and advisor session journals under the installed hooks/spot_advisor directory. It tracks covered tool calls. External programs, previously started jobs, remote editors and tools outside the matcher can change artifacts independently. This is a workflow check for local Codex, not an OS write barrier.

To remove the gates, remove just the two Spot Advisor handlers from hooks.json. The installer retains the pre-install hooks.json in hooks/spot_advisor/hooks.before-install.json when one existed. Preserve any unrelated configuration added since installation.

## Verification

Run the offline adapter and protocol tests:

~~~sh
python -m pytest -q tests/test_codex_review_gates.py tests/test_advisor.py
~~~

For a live smoke test, start a fresh trusted Codex session in a disposable directory. Attempt a shell file write and an apply_patch before planning. Verify both are denied and the target is absent. Preview and send a plan, make the change, and read it back. Attempt to finish before QA and verify Stop requests a continuation. Preview and send QA for the actual result and finish. Check that a later edit requires fresh QA and that a new turn cannot reuse the old receipt.
