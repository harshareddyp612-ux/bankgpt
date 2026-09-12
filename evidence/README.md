# Evidence

Each folder is one run. Regenerate everything with `scripts/make_evidence.sh` (see the root README).

| folder | what it shows |
|---|---|
| `01-discovery-lookup_member_savings_balance/` | **Real LLM-driven discovery run.** `run.jsonl` has every model decision with its stated reason, the policy verdict per action, and the result; `step-NN.png` is what the model saw at each step; `trace.json` is the structured trace; `artifact.json` is the capability it produced. |
| `02-replay-lookup-success/` | Deterministic replay with `member_id=12345`: which locator strategy resolved each step, checkpoint verified, typed currency output (name chosen by the model, e.g. `s01_current_balance`). `result.json` is the full result contract. |
| `03-replay-lookup-member-not-found/` | `member_id=99999` -> `BUSINESS_OUTCOME / MEMBER_NOT_FOUND` with the app's own message. A legitimate answer, not a failure. |
| `04-replay-lookup-dialog-recovered/` | Injected "System Notice" modal -> detected as a known recoverable condition, dismissed, run continued. |
| `05-replay-lookup-server-error-recovered/` | Injected HTTP 500 -> detected, one reload, run continued. |
| `06-replay-lookup-session-expired-handoff/` | Injected session expiry -> hard condition escalated; control transferred to the operator (scripted stand-in re-authenticates on the **same** live browser), human actions recorded (password masked), control handed back, replay resumed and succeeded. Look at the `control.transition` and `intervention.*` events and the `handoff-before/after` screenshots. |
| `07-replay-lookup-access-denied-unattended/` | Injected 403 with no operator available -> intervention request raised and recorded, run ends `ESCALATED_ABORTED / ACCESS_DENIED` with step, expected and observed. |
| `08-discovery-open_member_subaccount/` | **Real LLM-driven discovery** of a state-changing flow. Two escalations are visible: at step 2 the model itself called `request_help` because the member already had identical sub-accounts (left over from earlier runs against the same mock instance) and it did not want to create a duplicate without a human's say-so; later the commit click is classified risky by policy and routed to the operator for confirmation before execution. Both are answered by the scripted approver. |
| `09-replay-open-blocked-unapproved/` | Draft artifact replayed unattended: the risky step is **not** executed; run ends `ESCALATED_ABORTED / RISKY_NEEDS_CONFIRMATION`. |
| `10-replay-open-success/` | After `cua approve` and with `--confirm-risky`: the risky step runs, the sub-account is created and the confirmation-screen checkpoint is verified. |
| `11-replay-open-validation-error/` | Deposit below the minimum -> `BUSINESS_OUTCOME / VALIDATION_ERROR` carrying the app's error text. |

Files in every run folder:

- `run.jsonl` - one JSON object per event, redacted (sensitive parameter values, SSNs, card numbers, keys, `password=`).
- `*.png` - screenshots (per step in discovery; entry, per step, handoff before/after, checkpoint, final in replay).
- `summary.json` - one-line status; `result.json` (replay) - the full result contract; `trace.json` + `artifact.json` (discovery).

Anything under `_scratch/` is local scratch output and is not committed.
