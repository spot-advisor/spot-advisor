# Spot Advisor 2.4 release record

Install the `v2.4.0` tag for a stable copy of the skill. The [walkthrough](first-consultation-24.md) covers first installation, rollback, previews and actual consultations. The [public receipts](../eval/results/spot24-release/qualification.json) identify the candidate caller by content digest and show the checked output files.

## What was exercised

| User path | Checked action and outcome | Boundary of the evidence |
| --- | --- | --- |
| Codex and Claude Code installation | Two separate project directories, project-specific skill discovery, offline doctor and an actual call from each native host. Both hosts upgraded a disposable historical 2.2 copy and restored its exact files. | Automated Spot Advisor testers operated the host sessions. Elapsed onboarding time for human users was not measured. |
| Text | Both hosts obtained scoped advice and repaired the deadline equality condition. An independent verifier ran before, at and after the deadline. | Both primary first-use sessions used the Codex subscription backend. The host application and advisor transport are distinct. |
| Image | Both hosts sent the same chart bytes, read the bar label and saved the correct answer. | Qualification covers a scoped, decodable chart with the tested vision route. |
| PCM WAV | Both hosts sent the original English recording, recovered the spoken sentence and delivered a verified onset. | In the Codex walkthrough, the model suggested an early onset. The executor corrected it against the WAV before delivery. Earlier two-tone trials included reversed pitch judgments. Independently measure timing and pitch when those properties decide delivery. |
| Delivery review | On the final candidate all eight defective artifacts required rework and all eight valid artifacts were accepted. The mapped checks covered both CSV header order and approved invitation wording. | These are two intentionally enriched regression families with repeated runs. The archive preserves earlier refusals on valid work. |

The local doctor reports configuration and installed copies without sending credentials. Scoped delivery includes current acceptance conditions and constraints. A registered observation identifies the associated revision, log digest and, when supplied, checked artifact digest. A changed registered file invalidates the reply. Local form errors permit a retry when the latest user request still leaves a decision open. The existing session journal distinguishes an unresolved possible dispatch from a locally rejected request.

The additional direct Claude CLI transport completed an ordinary v2 text consultation. On a scoped text probe it returned the correct deadline fix but omitted the mandatory decision label; the caller returned `invalid_advice`. The stable scoped path in the host matrix uses the tested Codex subscription backend. The configured audio backend accepted the exact Gemini subscription alias shown in the receipts. Two alternative gateway aliases returned HTTP 502 during qualification.

## Reproduce the study

Run `python -B -m pytest -q -p no:cacheprovider` from the checkout for the software regression suite. The terminal demo has its own intentionally broken starting fixture and runs through the [demo instructions](../demo/README.md).

The [registered comparison](compression-confirmation-24.md) and [paper reproduction guide](../paper/compression24/README.md) define the experiment and its claims. The [initial calibration](../eval/results/spot24-release/pilot-initial/manifest.json) documented the first candidate's failures. The [amended calibration](../eval/results/spot24-release/pilot/manifest.json) used independent responses on the same 18 tasks. The [formal assignment manifest](../eval/results/spot24-release/formal/manifest.json) contains the untouched confirmation set and the pilot-based sample calculation.

From the release checkout, provision the pinned Docker image and public research dependencies. To repeat live collection, choose fresh output directories and provide an accessible audio gateway URL with the corresponding configuration. The Codex subscription and Claude Code credentials are local to the tester.

```sh
python -B eval/compression_inputs24.py eval/runs/source-cache eval/runs/new-inputs
python -B eval/host_first_use24.py eval/runs/new-hosts --audio-url GATEWAY_URL
python -B eval/compression_study24.py pilot eval/runs/new-inputs eval/runs/new-pilot --audio-url GATEWAY_URL
python -B eval/compression_study24.py formal eval/runs/new-inputs eval/runs/new-formal --pilot eval/runs/new-pilot --acceptance eval/runs/new-hosts/acceptance.json --audio-url GATEWAY_URL
```

The original experiment used the registered Gemini audio model, Luna executor and Astra text/vision reviewer. The host smoke uses the installed native Codex catalog default and the user's configured Claude Code. Hosted models and their behavior can change. Offline verification uses the immutable stored files and receipts.
