# tradebot

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
- Every tick is committed as it happens, so a killed job cannot take the day's
  record with it.
- Missed ticks are ledger events (`tick_missed`, `tick_error`, `session
  handoff`), never silence. The count of executed vs. planned ticks is part of
  the evaluation record, and a day that lost its morning is disclosed rather
  than averaged away.

`intraday.yml` no longer runs on a schedule and is kept only for manually
replaying a single tick. `daily.yml` keeps its cron as a cheap backup for the
slow arm — `run_once` is idempotent per day, so a duplicate is a no-op.

The strategy parameters did not change. This is instrument repair, logged
here so the 8-week window is read with the right execution history attached.
