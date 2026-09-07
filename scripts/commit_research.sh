#!/usr/bin/env bash
# Get the research chain off this machine, now.
#
# Called twice per replay: once after the window is claimed and before any
# computation, once after the result is recorded. The first call is the one
# that matters — the lost fast run computed for ninety seconds and died with
# its record still local. Exits non-zero if the push does not land, and the
# caller treats that as a refusal to proceed: a claim nobody else can see is
# not a claim.
set -uo pipefail
git config user.name  tradebot
git config user.email tradebot@users.noreply.github.com
for f in research_log.jsonl replays; do [ -e "$f" ] && git add "$f"; done
git diff --cached --quiet && exit 0
git commit -q -m "research chain $(date -u +%Y-%m-%dT%H:%MZ) [skip ci]" || exit 0
for attempt in 1 2 3 4 5; do
  git pull --rebase -q && git push -q && exit 0
  sleep $((attempt * 7))
done
echo "research chain: push failed after 5 attempts" >&2
exit 1
