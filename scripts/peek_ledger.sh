#!/usr/bin/env bash
# What have the OTHER processes done today?
#
# The session's ledger is committed and pushed after every tick, so origin/main
# is the shared record across the redundant runs. Read it without touching the
# working tree: a checkout or a pull here could discard local lines that have
# not been pushed yet, and losing a record is worse than doing a tick twice.
# Silence on any failure — a spare that cannot see the leader still trades.
set -uo pipefail
git fetch -q origin main 2>/dev/null || exit 0
for f in ledger.jsonl ledger_fast.jsonl ledger_movers.jsonl; do
  git show "origin/main:$f" 2>/dev/null || true
done
