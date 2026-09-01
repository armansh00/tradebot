# Sweeping the breakout threshold — design note

Proposal on the table: run several arms in parallel, each with a different
threshold for how big a move has to be before it trades, and see which one
works best.

The instinct is right. The obvious implementation would destroy the study.

## Why picking the winner does not work

Eight weeks is 40 trading days. The standard error on a Sharpe ratio measured
over a period of length *T* years is roughly 1/√T. Here that is 1/√0.154 ≈
**2.5**. So a single arm's measured Sharpe is a draw from something like
N(true, 2.5) — the noise alone is larger than any edge a retail breakout rule
could plausibly have.

Now run *k* arms and keep the best. For *k* independent draws from a standard
normal, the expected maximum is about 1.42 at k = 8 and 1.87 at k = 30
(asymptotically √(2 ln k)). Multiply by our standard error:

| arms | expected best Sharpe, all of them worthless |
|-----:|--------------------------------------------:|
|    1 | 0.0 |
|    4 | 2.6 |
|    8 | 3.6 |
|   16 | 4.2 |
|   30 | 4.8 |

Eight arms of pure noise will hand you a winner with a Sharpe near 3.6, which
looks spectacular. Report that number and you have published a coin flip.
This is the mechanism behind Bailey & López de Prado's deflated Sharpe ratio
and their probability of backtest overfitting: the more configurations you
try, the higher the bar the winner must clear, and almost nobody raises the
bar.

A partial reprieve: threshold variants on the same universe and the same
signal are heavily correlated, so the effective number of independent trials
is well below *k*. But that cuts both ways — highly correlated arms are not
giving you *k* pieces of evidence either. You pay in multiplicity roughly what
you gain in information.

## Three ways to do it properly

**1. Test the pattern, not the winner.** Pre-register the grid — say 0.5%,
1%, 1.5%, 2%, 3% — and ask one question: is net return monotone in the
threshold? One test across the whole grid, one p-value, and it uses every arm
as evidence instead of discarding all but the luckiest. It is also the more
scientifically interesting question. "Bigger breakouts pay better" is a claim
about market structure. "2% won" is a claim about August.

**2. Sweep offline, not live.** A threshold sweep does not need live accounts
at all. Every tick already records the opening-range levels and the prices it
saw; the same recorded data can be replayed against any threshold afterwards,
instantly, as many times as you like, with no calendar cost and perfect
reproducibility. Alpaca caps you at three paper accounts, so *k* live arms is
not even possible past k = 3 — and that constraint is pointing at the right
answer. Run the sweep as a replay over the ledger. Promote at most one
pre-registered threshold to a live arm.

**3. If it must be live, pre-register the correction.** Fix *k* and the exact
thresholds in `config.yaml` before the first trade, and write into the
evaluation criteria which correction applies (Holm–Bonferroni across arms, or
the deflated Sharpe with *k* trials declared). Deciding the correction after
seeing the results is the same error in slower motion.

## Recommendation

Build the replay harness — `tradebot sweep --thresholds 0.5,1,1.5,2,3` reading
the existing ledgers and reporting net return per threshold with the
monotonicity test. It costs no calendar time, cannot contaminate the running
experiment, and answers the question better than eight live accounts would.
Leave the three live arms exactly as pre-registered until 26 October.

The one thing not to do is add arms to the live experiment mid-window. That
converts a pre-registered study into a fishing expedition, and no amount of
analysis afterwards puts it back.


---

## What this became (2026-09-01)

The threshold question turned into a validation engine, because the question
behind it — "which variant works best?" — is the wrong question and answering
it well requires machinery that answers a better one.

The object being evaluated is the **research process**, not the best strategy
it found. Concretely:

- **REJECT is the default verdict.** Seven gates, all must pass: discovery,
  long-short, multiple testing, benchmark, costs, walk-forward, parameter
  stability. First live run failed six of seven.
- **The long-short gate** tests E[R|loser] > E[R|winner] rather than
  E[R|loser] > 0. Both legs carry the market, so the beta cancels. On real
  data this moved the reversal result from t = 1.69 to t = 0.84.
- **Multiple-testing correction is computed, not quoted.** A rule of thumb
  assumes independent trials; variants of one trade are not independent. The
  null shifts yesterday's ranking against today's returns, preserving
  cross-section and autocorrelation. Draw count is adaptive — 400 to screen,
  20,000 near the boundary.
- **Stability is split into sign consistency and economic viability**, because
  a process can be perfectly stable at losing money. The gate originally
  returned PASS for three unanimously negative values.
- **The vault** is a holdout bound to a hash of the strategy's rules,
  parameters, universe, cost model and acceptance criteria — not merely to a
  date. Opening it is recorded once. A tweaked strategy is a different
  strategy and does not inherit unseen data. This closes the loophole where
  repeated peeking silently converts a holdout into training data.
- **The research budget** counts hypotheses, parameter combinations, distinct
  strategies, vault openings and survivors, and is printed with every result.
  A t-stat of 2.4 means one thing as the third idea tried and another as the
  winner of forty-eight thousand.

Still to build, in order: regime gates with definitions declared in advance;
cost decomposition into spread, slippage, fees and impact with execution
priced at the ask for buys and the bid for sells rather than at the midpoint;
purged and embargoed cross-validation; the deflated Sharpe ratio; probability
of backtest overfitting; and full execution simulation.

The division of labour worth preserving: the language model proposes and
explains hypotheses. The statistical and execution gates hold the veto.
