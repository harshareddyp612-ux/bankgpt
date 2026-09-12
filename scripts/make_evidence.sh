#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.venv/bin:$PATH"
LLM="${LLM:-anthropic}"
BASE="${CUA_TARGET_URL:-http://127.0.0.1:5055}"
EV=evidence

if [ "$LLM" = "anthropic" ] && [ -z "${ANTHROPIC_API_KEY:-}" ] && ! grep -qs '^ANTHROPIC_API_KEY=sk-' .env; then
  echo "ANTHROPIC_API_KEY is not set (export it or put it in .env). Use LLM=mock for an offline smoke run." >&2
  exit 1
fi

find "$EV" -mindepth 1 -maxdepth 1 -type d ! -name '_scratch' -exec rm -rf {} +

if ! curl -sf "$BASE/__health" >/dev/null 2>&1; then
  echo "starting mock app on $BASE"
  (cua serve-app >/tmp/cua-mock-app.log 2>&1 &)
  for _ in $(seq 1 30); do curl -sf "$BASE/__health" >/dev/null 2>&1 && break; sleep 0.3; done
fi

LOOKUP=artifacts/lookup_member_savings_balance.json
OPEN=artifacts/open_member_subaccount.json
GOAL_LOOKUP="Look up member {{member_id}} and read the current balance of their regular savings (S01) account."
GOAL_OPEN="For member {{member_id}}, open a new sub-account of product type {{product}} with nickname {{nickname}} and an initial deposit of {{deposit}}, and reach the confirmation screen."

step() { echo; echo "================================================================"; echo ">> $*"; echo "================================================================"; }
soft() { "$@" || echo "(exit $? - expected for non-success outcomes)"; }

step "01 discovery (LLM=$LLM): lookup_member_savings_balance"
cua discover --llm "$LLM" --headless --operator scripted:approve --name lookup_member_savings_balance \
  --goal "$GOAL_LOOKUP" --param member_id=12345 --out "$LOOKUP" --run-name 01-discovery-lookup_member_savings_balance

step "02 replay: success"
cua replay --artifact "$LOOKUP" --param member_id=12345 --run-name 02-replay-lookup-success
step "03 replay: unknown member -> BUSINESS_OUTCOME MEMBER_NOT_FOUND"
soft cua replay --artifact "$LOOKUP" --param member_id=99999 --run-name 03-replay-lookup-member-not-found
step "04 replay: injected System Notice dialog -> recovered"
cua replay --artifact "$LOOKUP" --param member_id=12345 --inject dialog --run-name 04-replay-lookup-dialog-recovered
step "05 replay: injected 500 -> reload recovery"
cua replay --artifact "$LOOKUP" --param member_id=12345 --inject error500 --run-name 05-replay-lookup-server-error-recovered
step "06 replay: session expired -> human handoff (scripted operator re-authenticates) -> resumed"
cua replay --artifact "$LOOKUP" --param member_id=12345 --inject timeout --operator scripted:reauth --run-name 06-replay-lookup-session-expired-handoff
step "07 replay: access denied, unattended -> ESCALATED_ABORTED with intervention request"
soft cua replay --artifact "$LOOKUP" --param member_id=12345 --inject denied --operator none --run-name 07-replay-lookup-access-denied-unattended

step "08 discovery (LLM=$LLM): open_member_subaccount (risky step confirmed by operator)"
cua discover --llm "$LLM" --headless --operator scripted:approve --name open_member_subaccount --goal "$GOAL_OPEN" \
  --param member_id=12345 --param "product=Money Market" --param "nickname=Vacation Fund" --param deposit=100 \
  --out "$OPEN" --run-name 08-discovery-open_member_subaccount

step "09 replay: draft artifact, unattended -> risky step blocked (RISKY_NEEDS_CONFIRMATION)"
soft cua replay --artifact "$OPEN" --param member_id=12345 --param "product=Money Market" --param "nickname=Vacation Fund" --param deposit=100 \
  --operator none --run-name 09-replay-open-blocked-unapproved
step "approve after review"
cua approve --artifact "$OPEN" --by "$(whoami)"
step "10 replay: approved + confirm_risky -> SUCCESS (sub-account created)"
cua replay --artifact "$OPEN" --param member_id=12345 --param "product=Money Market" --param "nickname=Vacation Fund" --param deposit=100 \
  --confirm-risky --run-name 10-replay-open-success
step "11 replay: deposit below minimum -> BUSINESS_OUTCOME VALIDATION_ERROR"
soft cua replay --artifact "$OPEN" --param member_id=12345 --param "product=Money Market" --param "nickname=Vacation Fund" --param deposit=10 \
  --confirm-risky --run-name 11-replay-open-validation-error

step "catalog"
cua catalog
echo
echo "done. evidence in $EV/ ; artifacts in artifacts/"
