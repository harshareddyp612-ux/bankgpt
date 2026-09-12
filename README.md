# Computer-Use Automation System

**The model discovers. The artifact becomes a reusable capability. Deterministic replay is how the AI agent invokes it in production.**

An LLM drives a legacy bank back-office UI once to accomplish a natural-language goal. The successful run is recorded as a typed, versioned **capability artifact**. From then on the capability is **replayed deterministically** with no model in the loop, with explicit handling of runtime conditions (not-found, validation errors, dialogs, timeouts, permission denials, app errors), a **policy layer** (allowlist, risk gating, redaction), and a **human handoff** that transfers control of the *same live session* to an operator and back.

The target is a mock legacy credit-union core ("CU-Core Teller Desk"): server-rendered, table-based, no ids or test-ids, with injectable runtime faults. Design write-up: [`REPORT.md`](REPORT.md). Run evidence: [`evidence/`](evidence/README.md).

```
goal ──> discovery (LLM, observe→decide→act, policy-gated) ──> artifact (typed contract)
                                                                   │
                       operator ◄── escalation / handoff ◄── replay (no LLM) ──> result contract
```

## Setup

Requirements: Python 3.12+, [uv](https://docs.astral.sh/uv/) (or pip), Chromium via Playwright.

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
playwright install chromium
cp .env.example .env          # put ANTHROPIC_API_KEY=sk-ant-... in .env (only needed for discovery)
```

Config (`.env` or environment):

| variable | purpose | default |
|---|---|---|
| `ANTHROPIC_API_KEY` | key for the discovery model | required for `cua discover --llm anthropic` |
| `CUA_MODEL` | model id | `claude-opus-5` |
| `CUA_EFFORT` | reasoning effort for the agent | `medium` |
| `CUA_TARGET_URL` | base URL of the target app | `http://127.0.0.1:5055` |

Policy lives in [`policy/allowlist.yaml`](policy/allowlist.yaml); the app profile (business-outcome wording, known dialogs) in [`profiles/cu_core.yaml`](profiles/cu_core.yaml).

## Demo path

Terminal 1 - the target app:

```bash
cua serve-app                      # http://127.0.0.1:5055/teller/members/search
```

Terminal 2 - discover, then replay:

```bash
# 1. LLM-driven discovery. Headed by default so you can watch; the operator prompt is your terminal.
cua discover --name lookup_member_savings_balance \
  --goal "Look up member {{member_id}} and read the current balance of their regular savings (S01) account." \
  --param member_id=12345
#   -> artifacts/lookup_member_savings_balance.json  +  evidence/discovery-<ts>/

# 2. Deterministic replay (no LLM; a runtime guard makes any model call raise).
cua replay --artifact artifacts/lookup_member_savings_balance.json --param member_id=12345
#   SUCCESS  outputs: {'s01_current_balance': 4512.78}   steps: s01:ok[near_text], s02:ok[table_cell]
#   (output names are chosen by the model at discovery and become the artifact's typed outputs)

# 3. Business outcome vs failure
cua replay --artifact artifacts/lookup_member_savings_balance.json --param member_id=99999
#   BUSINESS_OUTCOME (MEMBER_NOT_FOUND)  "No member found matching 99999..."        exit code 2

# 4. Runtime conditions (test-only fault injection on the mock app)
cua replay --artifact artifacts/lookup_member_savings_balance.json --param member_id=12345 --inject dialog     # recovered
cua replay --artifact artifacts/lookup_member_savings_balance.json --param member_id=12345 --inject error500   # reload, recovered
cua replay --artifact artifacts/lookup_member_savings_balance.json --param member_id=12345 --inject denied     # ESCALATED_ABORTED (unattended)

# 5. Human handoff on the live session: session expires mid-replay, you re-authenticate in the browser window
cua replay --artifact artifacts/lookup_member_savings_balance.json --param member_id=12345 --inject timeout --headed --operator cli
#   ... INTERVENTION REQUESTED ... (sign in as teller1 / demo-pass in the window, then type `resume`)
#   or unattended with a scripted stand-in operator:  --operator scripted:reauth

# 6. A state-changing capability: the commit step is risky and gated
cua discover --name open_member_subaccount \
  --goal "For member {{member_id}}, open a new sub-account of product type {{product}} with nickname {{nickname}} and an initial deposit of {{deposit}}, and reach the confirmation screen." \
  --param member_id=12345 --param "product=Money Market" --param "nickname=Vacation Fund" --param deposit=100
#   (the operator prompt asks you to approve the 'Open Sub-Account' click)
cua replay --artifact artifacts/open_member_subaccount.json --param member_id=12345 --param "product=Money Market" --param "nickname=Vacation Fund" --param deposit=100
#   ESCALATED_ABORTED (RISKY_NEEDS_CONFIRMATION)  - draft artifact, unattended: the commit step never runs
cua approve --artifact artifacts/open_member_subaccount.json
cua replay --artifact artifacts/open_member_subaccount.json --param member_id=12345 --param "product=Money Market" --param "nickname=Vacation Fund" --param deposit=100 --confirm-risky
#   SUCCESS  checkpoint: confirmation screen reached (this goal declares no outputs; add "and read the confirmation number" to the goal to get one)
cua replay --artifact artifacts/open_member_subaccount.json --param member_id=12345 --param "product=Money Market" --param "nickname=Vacation Fund" --param deposit=10 --confirm-risky
#   BUSINESS_OUTCOME (VALIDATION_ERROR) "Initial deposit must be at least $25.00."

# 7. Agent-facing catalog (stretch): capabilities as tool definitions, invoke by name, JSON result contract
cua catalog --json
cua invoke lookup_member_savings_balance --param member_id=12345
```

Regenerate the whole `evidence/` set in one go: `scripts/make_evidence.sh` (real model runs; `LLM=mock` for an offline smoke run).

### Running without live services

- **Replay never needs the model** - only the target app.
- `cua discover --llm mock` runs the full loop -> recorder -> artifact path with a rule-based stand-in for the model (it only knows the two demo goals). Its output is clearly labelled and is *not* the discovery evidence.
- `pytest` (44 tests, ~20 s) starts the mock app itself if it is not running: schema contract, policy and redaction, detectors, control state machine, recorder templating, and end-to-end replay scenarios (success, not-found, dialog, 500, session-expired handoff, access-denied, slow load, locator fallback, locator-not-found, LLM guard).

Exit codes: `0` SUCCESS, `2` BUSINESS_OUTCOME, `1` anything else.

## Layout

```
cua/
  surface/      Surface ABC + Playwright implementation; DOM enumerator; locator candidate ranking
  agent/        LLM clients (Anthropic, mock), tools, prompts, discovery loop, recorder
  artifact/     the capability schema (pydantic) + store
  replay/       deterministic engine, locator resolution, detectors, error taxonomy, result contract
  policy/       allowlist + risk classification, redaction
  escalation/   control-transfer state machine, intervention requests, operator consoles
  evidence/     structured JSONL logs + screenshots
  cli.py
target_app/     the mock legacy credit-union app (Flask) with fault injection
policy/allowlist.yaml   profiles/cu_core.yaml   artifacts/   evidence/   tests/   scripts/
```

## Notes

- Fault injection (`--inject`, `/__chaos/<mode>`) exists only on the mock app and is denied to the agent by the allowlist.
- All member data is fabricated. Operator credentials for the mock re-auth screen are `teller1` / `demo-pass`.
- Keep `.env` out of git (it is ignored). Evidence logs are redacted; screenshots are not pixel-redacted (see REPORT.md, Safety).
