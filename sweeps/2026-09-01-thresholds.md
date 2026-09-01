# Threshold sweep — 30 sessions (2026-07-20 to 2026-08-28)

Universe: SPY, QQQ, AAPL, MSFT, NVDA, TSLA, AMZN, META
Opening range 30 min, top 2, cost 5 bps/side, max 6 trades/day.

Replay of a pre-registered grid, not a live race. See DESIGN-threshold-sweep.md.

| threshold | days | trades | mean daily gross % | mean daily net % | total net % | win rate |
|---:|---:|---:|---:|---:|---:|---:|
| 0.00% | 30 | 57 | +0.0592 | -0.0357 | -1.122 | 0.31 |
| 0.25% | 30 | 48 | -0.0537 | -0.1336 | -3.991 | 0.33 |
| 0.50% | 30 | 40 | -0.0787 | -0.1452 | -4.315 | 0.28 |
| 1.00% | 30 | 27 | -0.0353 | -0.0802 | -2.407 | 0.42 |
| 1.50% | 30 | 14 | -0.0009 | -0.0242 | -0.736 | 0.50 |
| 2.00% | 30 | 8 | -0.0007 | -0.0141 | -0.424 | 0.50 |
| 3.00% | 30 | 2 | -0.0108 | -0.0141 | -0.422 | 0.00 |

Spearman rho (threshold vs mean daily net) = +0.750, exact permutation p = 0.0663
Same test on GROSS return (costs removed) = +0.107, p = 0.8397

Read the two together. Trading less always loses less to costs, so a positive ordering on net that vanishes on gross is the cost gradient wearing a signal's clothes, not evidence that bigger breakouts pay. Note also that the grid is nested — a 2% breakout is also a 1% breakout — so the arms are heavily correlated and the permutation test's independence assumption is generous. Treat the p-value as an optimistic bound.

No ordering. The best row in this table is the best row in a table of noise; do not promote it, and do not report its return as an estimate of anything.
