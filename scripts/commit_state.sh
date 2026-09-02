#!/usr/bin/env bash
# Persist the ledgers after every tick, not once at the end of the day.
# A six-hour job that is killed at hour five must not take the day's record
# with it. Never fails the run: a failed push is a retry next tick, not a
# reason to stop trading.
set -uo pipefail

git config user.name  tradebot
git config user.email tradebot@users.noreply.github.com

for f in ledger.jsonl state.json ledger_fast.jsonl state_fast.json \
         ledger_movers.jsonl state_movers.json reports; do
  [ -e "$f" ] && git add "$f"
done

git diff --cached --quiet && exit 0

git commit -q -m "session tick $(date -u +%Y-%m-%dT%H:%MZ) [skip ci]" || exit 0

for attempt in 1 2 3; do
  git pull --rebase -q && git push -q && exit 0
  sleep $((attempt * 5))
done

# Non-zero, deliberately. Mid-day the caller ignores this and retries on the
# next tick. At a deadline handoff there IS no next tick, so the caller has to
# know the record did not leave this runner.
echo "push failed after 3 attempts" >&2
exit 1
