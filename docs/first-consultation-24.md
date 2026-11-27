# First consultation on the 2.4 candidate

Start from the candidate checkout. Use a fresh project for these exercises. Python 3.10+ runs the skill. Install Pillow for images. Set up a Codex login or Claude Code for text. The scoped Codex vision route accepts images. Original PCM WAV requires an audio-capable chat-completions gateway, its exact model name and the gateway credentials. Both host applications can launch that same caller.

| Host | Candidate install in the project | Text | Picture | WAV |
| --- | --- | --- | --- | --- |
| Codex | `.agents/skills/advisor` | Codex subscription | Scoped Codex vision | Configured audio gateway |
| Claude Code | `.claude/skills/advisor` | Claude CLI or Codex subscription | Scoped Codex vision or configured image gateway | Configured audio gateway |

Install from the checkout by running `python scripts/install_skill.py PROJECT/.agents/skills/advisor` for Codex or substituting `.claude/skills/advisor` for Claude Code. The command prints the installed path and the backup path if it replaced an earlier copy. To roll back run the same command with `--restore-from BACKUP`. Reopen the host in the project and run the installed caller with `--doctor`. It reports the invoked path, version, local configuration and other visible copies. A live exercise below checks the configured backend.

Prepare a case using `python examples/first_review.py text PROJECT/case`. Choose `image` or `audio` for the other exercises, each with a new case directory. From that directory, read `brief.txt` and `question.txt`. Run the installed caller with the following arguments, replacing QUESTION with the contents of the question file.

```sh
python PATH_TO_INSTALLED_CALLER --state-file state.json --session-file session.json --handoff-file handoff.json --max-rounds 1 --question "QUESTION" --preview
```

For images add `--transport codex --image view.png`. For audio add `--transport gateway --model AUDIO_MODEL --audio sound.wav` and configure `EXPERTS_BASE_URL` and `EXPERTS_API_KEY` for that route. Inspect the preview, then run the same command without `--preview`. Read the four advice fields. Apply the requested change to the local output, then run `python PATH_TO_CHECKOUT/examples/first_review.py verify .` from the case directory. Report the verifier's output, the actual model, the caller digest and any step that needed help. Keep credentials and local paths private when sharing receipts.

When the backend returns an unusable response, inspect `--status --session-file session.json`. A possible dispatch already consumed the round. A new independent local check can still verify the task. Preserve the failed observation when reporting route reliability.

The audio walkthrough uses an original synthetic English dispatch announcement. Score the spoken words by listening and verify the onset on the PCM waveform. For the extra `tone` exercise, measure frequency and onset from the original samples when those properties determine delivery. The calibration included reversed pitch judgments despite a valid backend receipt.
