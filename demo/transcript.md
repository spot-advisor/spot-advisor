# Recorded demo transcript

This transcript is a sanitized summary of one recorded local run. The animation is a designed replay of these steps, not a screen recording or a live product UI.

1. Run the focused unittest twice against the unchanged fixture. Both runs reproduce the same three failures: `False`, `0`, and an empty-string override are replaced by defaults. The missing-key test passes.
2. Run Spot Advisor's v2 preview. It prints a 922-character packet and makes zero model calls.
3. Send the same packet through one authenticated Codex advisor call. The returned recommendation is to distinguish a missing key from a present falsey value with a key-membership check. It also warns against eager evaluation of a default expression that could raise `KeyError`.
4. Apply the one-line recommendation in the temporary demo copy, keeping the function signature unchanged.
5. Rerun the same focused unittest. All four checks pass.

The replay omits local paths, account details, session files, and request identifiers. The demo fixture is intentionally small and is not evidence of general repair performance. See [`README.md`](README.md) for the reproducible commands.
