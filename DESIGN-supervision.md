# Supervised live trading — specified, NOT built

Written 2026-09-01, after the commissioning freeze. **No code implemented.**
Nothing in this document ships before vintage 000 has run.

## The authority model

    new live position      ->  HUMAN APPROVES
    stop / kill / risk exit ->  AUTOMATIC
    normal exit per rules  ->  AUTOMATIC

Never require a click to stop a loss. Human response time must not be able to
defeat the risk system — you may be asleep, scanning, or holding a phone with
no signal.

## The problem supervision creates

If every live entry needs approval, the trades that happen are the ones the
operator was awake, free and inclined for. That is a selection filter applied
by human availability, and it silently makes the live record a different
strategy from the paper record — which breaks the pre-registered comparison
the project exists to make.

So: **human intervention is measured as part of execution, never allowed to
alter the experiment quietly.** Every signal writes three parallel objects.

| object | what it is | observability |
|---|---|---|
| `strategy_intent` | what the preregistered rule wanted, at the original decision timestamp | exact |
| `shadow_execution` | what would likely have happened had the order gone immediately | **ESTIMATED** |
| `actual_execution` | what happened after approval — latency, changed price, fills, costs | exact |

    supervision_cost = R_executed - R_shadow

`shadow_execution` must never be labelled "what it would have made". The fill
in a world where the order went earlier is not observable. It is an estimate,
and the ledger says so in the field name.

### Estimating the shadow — and what NOT to claim from it

The paper account runs the same strategy simultaneously and unsupervised, so
it supplies a real parallel fill rather than a simulated one. That is useful.
It is **not** the counterfactual, and the field is named
`paper_execution_comparator` to stop it being read as one.

The tempting decomposition is wrong:

    live fill - paper fill  =  supervision cost        # NO

Alpaca's paper engine matches against the available NBBO and models no market
impact, no latency slippage, no queue position, no price improvement and no
regulatory fees. So that difference actually contains

    approval latency + live execution effects + paper-model error

with no way to separate them. Use the recorded quotes instead, which are
observable at both timestamps:

    approval movement       = quote(live submit) - quote(decision)
    live execution deviation = fill(live)        - quote(live submit)

Both are measured against something real. Record all five:

    decision quote
    paper fill
    live approval timestamp
    live submission quote
    live fill

**A second contamination, less obvious.** Paper and live diverge in position
state. A fill that happens in one and not the other leaves the two accounts
holding different things, and from that point they are no longer running the
same strategy — they are running the same rules from different states, and the
gap compounds. So the comparison is **per-trade at the decision point**, never
portfolio-level P&L over a period. After a week those two equity curves are
not comparable and it will not be visible that they stopped being so.

## Approval states

    PENDING_APPROVAL
    APPROVED
    DECLINED
    EXPIRED_NO_RESPONSE
    INVALIDATED_BY_MARKET
    EXECUTED

Every one is recorded. An expired request is a state, not a silent skip —
otherwise the ledger develops holes exactly where the operator was busy.

`INVALIDATED_BY_MARKET` is the important one. Approval means *permission to
execute if the strategy is still valid*, never *force this order through*. A
signal at 09:32:04 approved at 09:32:47, whose entry condition no longer
holds, is refused by the engine regardless of the approval.

Which means `approval_validity_seconds` must be declared per strategy in
Commit A. Otherwise "still valid" is a judgement made at approval time, which
is exactly the discretion this whole structure exists to remove.

## Which arms may be supervised

Supervision is only compatible with a strategy whose decision point tolerates
human latency, and only if that latency was declared in advance.

- **slow arm** — trades once daily at open+2. Approval costs nothing. Eligible.
- **fast / movers arms** — opening-range breakouts on a 30-minute clock. A
  signal at 09:35:02 approved at 09:38:41 is not a delayed trade, it is a
  different trade at a different price. **Not eligible.** These are paper-only
  or automatic under fixed limits. Human approval inserted into a
  timing-sensitive strategy silently replaces it with another strategy.

## Authentication

A notification is not authority. The push says only *"a trade requires
approval"*; opening it lands on an authenticated surface. Authorisation
requires a live session plus an expiring token bound to that exact
`intent_id`, and it is **incapable of changing symbol, quantity, side, price
ceiling, account or strategy** — those were fixed by the engines before the
human saw them. The human input is one bit: yes or no.

    Intent 7ac91...
    AAPL  BUY 2  limit <= 230.10
    expires 09:33:00
    [AUTHORIZE]

## Hosting: this settles it

GitHub Actions keeps research, testing, pre-registration, nightly jobs,
deployment and reports. It stops being the live execution runtime, because a
job that starts and dies cannot own state that must persist: open positions,
pending orders, partial fills, outstanding approvals, current risk, broker
connection health, kill-switch state. Alpaca's `trade_updates` stream needs a
process that stays alive to consume it.

    GitHub  ->  code and configuration
                        |
                ALWAYS-ON SERVICE
                signal | risk | approval API | order manager | kill switch
                        |
                      Alpaca
                        |
                order and fill stream -> ledger

## The broker is authoritative

Local state says two AAPL shares; the broker says one, because the second
never filled. **The broker wins and the ledger is reconciled to it.** On every
restart the service asks first — what positions exist, what orders are open,
what filled while I was down — before it resumes trading. Reality is not
reconstructed from our own event history.

(The current code already reads positions from the broker rather than local
state, which is the same principle. The gap is orders in flight.)

## Three systems, three jobs

    RESEARCH SYSTEM      decides what deserves testing
    EXECUTION SERVICE    runs approved strategies, enforces risk
    OPERATOR INTERFACE   inspect, approve where appropriate, pause, query
