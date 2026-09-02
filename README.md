# tradebot

**What this is not.** Not investment advice, not a product, not a strategy
anyone should run with money. It is a pre-registered experiment in whether
three trading cadences can be told apart from noise on a $50 paper account
inside eight weeks, run by a physician-researcher as a methods exercise. The
strategies are textbook and deliberately unoriginal; the interesting part is
the governance — fixed criteria written before the data, an append-only
ledger, a cross-vendor adversarial audit, and an honest record of every tick
the system missed. Expect it to fail its own criteria. That is a result, not
a bug. No warranty of any kind.

**Terms.** All rights reserved. Public for transparency and citation, not for
reuse: read it, link it, quote it, cite it. Copying it into another project or
redistributing it is not permitted. No license is granted by visibility alone.

A rules-based paper-trading bot with an explainable chat interface —
now a **two-arm experiment**: slow (dual momentum, weekly-ish) vs
fast (intraday opening-range breakout, multiple trades/day).

Built on three principles, in order:

1. **Code decides, records why, and can prove it.** The strategy is
   deterministic (dual momentum + 200-day trend filter on liquid ETFs).
   Every decision is logged with the exact signal values at decision time.
   `why SPY` answers from that record — the bot cannot invent a reason
   after the fact.
2. **Paper first, against pre-registered criteria.** The pass/fail bar
   lives in `config.yaml` *before* the first trade. No moving the goalposts.
3. **A tiny live account tests plumbing, not skill.** A $50 month is
   statistically silent on edge (t ≈ Sharpe × √years — run `evaluate`,
   it does this math on your actual data and tells you). What $50 *can*
   measure: real fills vs paper fills, slippage, and whether the brakes work.

## Setup (10 minutes)

1. Create a free account at https://alpaca.markets — you want **Paper
   Trading** (no funding, no SSN-linked brokerage needed for paper).
2. Dashboard → Paper Trading → API Keys → generate.
3. `cp .env.example .env` and paste the two keys in.
4. `pip install -r requirements.txt`
5. `PYTHONPATH=src python -m tradebot run` (or add `src/` to your PYTHONPATH).

The paper account starts with fake $100,000. Optional but recommended:
reset it to $50 in the Alpaca dashboard (Paper account → Reset) so the
test runs at the scale you actually intend.

## Daily use

```
python -m tradebot run        # the daily tick (idempotent; skips if market closed)
python -m tradebot chat       # interactive: status / pnl / why SPY / decisions ...
python -m tradebot report     # today's markdown report (also in reports/)
python -m tradebot evaluate   # score vs pre-registered criteria, with honest stats
python -m tradebot kill       # halt all trading (writes .halt)
python -m tradebot resume     # clear the halt
```

## Running it hands-off

Push this folder to a private GitHub repo, add `ALPACA_API_KEY` and
`ALPACA_SECRET_KEY` as repository secrets, and `.github/workflows/daily.yml`
runs the tick every weekday morning and commits the ledger + report back.
No server needed. (Or: a laptop cron line — `40 9 * * 1-5` local — works too.)

## The strategy (pre-registered)

Universe: SPY QQQ IWM EFA TLT GLD. Monthly-grade signals on daily bars:
hold the top 2 by 12-1 momentum, only while above their 200-day SMA and
momentum is positive; otherwise cash. Rebalance orders only when drift
exceeds $2. This is a deliberately boring, literature-adjacent baseline —
the point of the exercise is the *process* (logging, evaluation,
discipline), not a secret signal. Expect weeks with zero trades.

## Brakes (all fail closed)

- Per-order notional cap and max position count (`config.yaml`).
- Drawdown kill switch: equity 10% below high-water mark → flatten
  everything, write `.halt`, refuse to trade until you `resume`.
- Any `.halt` file stops the run before it starts.
- The broker wrapper refuses to run against a non-paper endpoint.
- Chat parse failures and missing data are skips, never trades.

## Going live with the $50 (later)

Only after `evaluate` prints PASS on the pre-registered criteria
(≥8 weeks, net positive, drawdown <10%, zero manual overrides).
Then: fund a live Alpaca account with $50, swap keys, change the base
URL — and treat the live month as an *execution-quality experiment*:
compare live fills to paper fills on the same signals. That comparison,
not the P&L, is the data the $50 buys.

## The experiment: fast vs slow

The fast arm exists to answer one pre-registered question: **does trading
more increase the money, net of costs?**

- **Slow arm** — dual momentum on the paper account (as before).
- **Fast arm** (`python -m tradebot run-fast`) — intraday opening-range
  breakout on liquid names: after the first 30 minutes, buy up to 2
  symbols breaking above their opening range, stop out below the range
  low, hard daily loss stop at -3%, everything flat by 15:30 ET, max 6
  entries/day. Runs a **virtual $50 book against live data** with an
  explicit modeled cost on every fill (5 bps/side — the half-spread and
  slippage that paper accounts pretend away). Every fill logs its cost
  in dollars. The toll booth is on the books.
- **`python -m tradebot compare`** — equity, Sharpe, drawdown, order
  counts, and total costs paid, side by side, plus the pre-registered
  verdict from `config.yaml` (`fast_evaluation`): the fast arm must
  overlap ≥8 weeks, be net positive, and beat the slow arm net of costs.

Run the fast tick every 30 minutes during market hours — locally
(`*/30` cron) or with the included `.github/workflows/intraday.yml`.
Chat understands `fast` and `compare`.

Why simulated fills instead of the paper account: paper fills are
frictionless, which would flatter exactly the thing under test.
Modeled costs make the frequency tax visible and itemized. The prior
from the literature (Barber & Odean; the Taiwan day-trading studies) is
that costs win. If your data says otherwise after 8 weeks, that's a
result worth taking seriously — and either way it costs $0 to find out.

## Arm 3 — movers mode

`python -m tradebot run-movers` (scheduled in the intraday workflow) trades
the same opening-range playbook, but its universe is rebuilt every morning
from the market's top most-active stocks (min price $5), with a 15 bps/side
cost model — movers carry wider spreads. It exists to test attention-driven
trading against Barber & Odean's evidence, pre-registered in
`movers_evaluation`. Three philosophies race: patience, frequency on
quality, frequency on heat. `compare` renders all three.

## Cross-vendor review

See `REVIEW_BRIEF.md`: OpenAI's model audits this Claude-written code
weekly and after every strategy change, committing findings to `REVIEWS/`
and opening an issue. One secret enables it: `OPENAI_API_KEY`.

## Tests

```
python -m pytest tests/ -q     # 23 tests, offline, fake brokers for both arms
```

Not investment advice; a personal research tool. Expect the strategy to
trail buy-and-hold in strong bull markets — trend filters buy insurance
against deep drawdowns and pay for it in whipsaws.

## Execution layer (revised 2026-08-31)

The first live weekday exposed a defect in the harness, not the strategy.
GitHub's `schedule` event is best-effort: on 2026-08-31 the 13:40 UTC daily
tick and the 14:05 / 14:35 / 15:05 UTC intraday ticks were never delivered at
all, and the previous day's review cron ran 3h50m behind its 12:00 UTC slot.
Nothing errored. The Actions page was clean. The account simply sat flat
through the open.

For a daily rebalance that is an annoyance. For an opening-range arm it is
fatal — a tick that fires at 13:00 ET is not a late version of the 10:05 tick,
it is a different experiment wearing its name.

So the cadence moved out of cron and into the process:

- `session.yml` fires once at 09:30 UTC, four hours ahead of the open, purely
  to get a process started. `python -m tradebot session` then reads the
  exchange calendar (half days included), sleeps to the open, and runs the
  pre-registered cadence off its own clock.
- Three chained legs, because a hosted job is capped at six hours. Each leg
  hands off at 5h40m and the next resumes from the ledger; legs that find the
  session already over exit in seconds.
- Seven staggered starts, all allowed to run. There is no `concurrency` group:
  GitHub cancels a superseded run rather than queueing it, which on 2026-09-01
  killed five of the seven and left the day resting on the one that stalled.
  Duplicate work is prevented inside the process instead — each run re-reads
  the shared ledger immediately before every tick and skips what another run
  already did, and every intraday order carries a deterministic
  `client_order_id` that the broker itself refuses to fill twice.
- Every tick is committed as it happens, so a killed job cannot take the day's
  record with it. The deadline handoff goes further: it writes the event,
  fsyncs it, and does not return until the commit hook acknowledges that the
  record left the runner. If it cannot, the session exits `handoff_unconfirmed`
  and the job goes red — because a handoff nobody can see is the gap that
  ate 5h45m on 2026-09-01.
- Before the first tick, every arm has to fetch its own data through its own
  account — `python -m tradebot preflight` runs the same probes on demand. An
  arm that cannot read what it trades on is disabled for the day
  (`DATA_PREFLIGHT_FAIL`) and the others carry on. There is deliberately no
  feed fallback: SIP and IEX are different information sets, and an arm that
  quietly switched feeds would be running against a pre-registration it no
  longer satisfies.
- Missed ticks are ledger events (`tick_missed`, `tick_error`, `session
  handoff`), never silence. The count of executed vs. planned ticks is part of
  the evaluation record, and a day that lost its morning is disclosed rather
  than averaged away.

`intraday.yml` no longer runs on a schedule and is kept only for manually
replaying a single tick. `daily.yml` keeps its cron as a cheap backup for the
slow arm — `run_once` is idempotent per day, so a duplicate is a no-op.

The strategy parameters did not change. This is instrument repair, logged
here so the 8-week window is read with the right execution history attached.

## Fill model (revised 2026-08-31)

Each arm trades its own paper account and sends real orders. Alpaca caps a
user at three paper accounts, and the original $100k one already existed, so
the slow arm stays on it with `book_cap: 50` holding its tradable book to $50;
the two intraday arms get genuinely $50 accounts, which is where a real
balance matters, since settlement and buying power actually bind a day trader
and not a monthly rebalance.

The earlier design had the intraday arms simulate their own fills. That was
not a preference; all three arms shared one $100k paper account, and real
orders would have pooled their positions into a single pile with no way to
attribute a dollar of profit to the strategy that earned it. Alpaca now
allows additional paper accounts with a chosen starting balance, which
removes the constraint, so the arms are genuinely separate books and the
account is the only record of what each holds. `assert_distinct_accounts`
refuses to start if two arms ever resolve to the same account number — that
misconfiguration would not raise anything on its own; it would just quietly
invalidate the study.

The modeled cost survives the change, for a documented reason. Alpaca's paper
engine fills at the best available price and explicitly models no slippage,
no spread, no market impact, no queue position and no latency. Free perfect
execution flatters frequent trading precisely where these arms operate. So
5 bps a side (15 for movers) is still charged, now as a separate accrued line
rather than folded into the fill price: the account reports gross,
`cost_accrued` converts it to net, and the pre-registered criterion reads net.
`fills_mode: simulated` keeps the older self-contained engine available.

Two consequences worth stating in advance. Order rejections are now possible
and are recorded as `fast_rejected` rather than raised — a $50 cash account
cannot recycle unsettled proceeds all day, and if settlement rules are what
cap the fast arm's trade count, that is a finding rather than a fault. And
`book_cap` now applies to exactly one arm — the slow one, still inside the
original $100k account — which is what it was written for.

## Amendment 2026-09-01 — record execution quotes, change no metric

Every fill now stores the best bid, best ask, midquote and quoted spread in
basis points at the instant the order was sent (`broker.quote_snapshot`).

This adds a recorded quantity. It does not change a criterion. The
pre-registered pass/fail still reads net-of-modeled-cost return at 5 bps a
side (15 for movers), exactly as written before the first trade. Swapping in a
measured cost after seeing results would be outcome switching, which is the
specific failure this whole structure exists to prevent.

What it buys: in October we can report the modeled cost and the measured
effective cost side by side, and the gap between them becomes a finding
instead of an argument. The flat 5 bps was a number chosen with no empirical
basis, and it is load-bearing — it is what turns the fast arm's +1.8% gross
over 30 replayed sessions into −1.1% net. An assumption doing that much work
should be checkable.

Prior expectation, stated now so it can be wrong later: a breakout rule sends
marketable buys into rising prices, which is the textbook adverse-selection
case (Harris, *Trading and Exchanges*, ch. 13-14). The realised cost should
therefore exceed the quoted half-spread, and exceed it by more in the movers
arm than the fast arm.

The quote fetch is fail-open — a missing quote costs a measurement, never a
trade.

## The rule

**The language model proposes and explains. The gates veto. The future
adjudicates.**

**The system itself is the strategy being tested.**

The model's objective is to generate mechanism-diverse, falsifiable
hypotheses under a fixed research budget — not to find profitable strategies,
and not even to generate decorrelated ones, since asking for low historical
correlation makes decorrelation an optimisation target. Every hypothesis must
name a preregistered mechanism and state, before any code exists, the
observation that would kill it.

No model output is evidence until an independent test produces it.

**Human intervention is measured as part of execution, never allowed to alter
the experiment quietly.** An approval that changes when a trade happens changes
what the strategy is; if that is not recorded against an estimated
unsupervised counterfactual, the live record and the pre-registered strategy
have silently become different things.

**Every piece of information consumed by the research process must leave a
trace.** Not only executed backtests: model suggestions, parameter searches,
regime definitions, vault access, strategy descendants, null simulations,
rejected variants and manual interventions. Once that holds, the research
history becomes data in its own right, and the question worth asking stops
being "did it once find a t-stat of 2.4" and becomes: *after hundreds of
pre-registered attempts, do the strategies this process promotes survive
unseen data at a rate better than the null process?* That is the thing being
evaluated here. A single strategy is a sample of size one.

Everything else in this repository is machinery for enforcing those two
sentences when it would be more comfortable not to. Concretely: REJECT is the
default verdict and all seven gates must pass; the holdout is bound to a hash
of the strategy's rules, parameters, universe, cost model, data cutoffs,
acceptance criteria and code commit, and is consumed the moment the data is
handed over rather than when an analysis finishes; the research log is
hash-chained so a deleted or edited experiment is detectable; permutation
counts escalate on a schedule fixed before any result is seen; and every
report card carries the count of how much searching preceded it, with edits of
one failed idea grouped by lineage instead of counted as separate hypotheses.
