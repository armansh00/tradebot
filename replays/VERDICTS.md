# Replay verdicts

Adjudications are written here by a person, against the replay outputs. The
replay code reports numbers; it does not decide what they mean.

## movers — proxy universe — 2025-09-05 to 2026-09-04 — decided 2026-09-06

```
PROXY_REPLAY_RESULT:  REJECTED
TARGET_HYPOTHESIS:    NOT ADJUDICATED
reason:               historical universe does not reproduce the live
                      share-volume most-actives screener
```

Two conclusions, kept apart because combining them would overstate what the
replay established.

**The proxy strategy is rejected.** On the universe actually replayed, the
result is not marginal: gross P&L negative before costs, net worse, 62% of
sessions down. The agreement between −0.33% per session pre-vault and −0.29%
in the vault is more informative than the −54% compounded headline, because
it shows the result did not disappear when the confirmatory window opened.

**The live movers hypothesis is untested.** The proxy ranks by dollar volume
and selected SPY, QQQ, NVDA, TSLA, AAPL, AVGO, META, PLTR on most days.
Alpaca's live screener ranks by share volume and surfaces cheap,
high-attention names — the population the arm was built to test. That is a
different population, and this replay says nothing about it.

**Risk controls: functioned.** Kill-switch halts fired 9 times, day stops 6;
both worked as specified and neither rescued the economics. This is
execution evidence, not strategy evidence.

**Independence note for the fast arm.** Seven of the fast arm's eight
registered names were in the proxy's selection on most days. Whatever the
fast replay shows, it is not a second population. This is a fact about the
universes, not a prediction about the result, and the fast verdict is to be
read on its own.

## fast — as registered — pending

First computation completed in run 34020674300 on 2026-09-06 and was lost
to a research-log push race before it could be committed. The rerun is
labeled `rerun_reason: persistence failure after completed computation` in
the research log so the chain does not pretend it was the first physical
computation.

## Architecture defect, identified 2026-09-06

Two parallel jobs appended to `research_log.jsonl`, a hash chain, from the
same starting commit. Each record chained to the same previous hash and the
second job's rebase could not resolve it. Serializing the replay jobs fixes
today's problem. The class fix is single-writer logging: workers emit
independent result artifacts; one coordinator appends them to the chain in
sequence. Not built yet.
