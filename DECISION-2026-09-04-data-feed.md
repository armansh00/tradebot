# Declared data source: SIP. Decision of 2026-09-04.

**Decision (Arman, 2026-09-04):** keep SIP as the declared feed for all three
arms and obtain the entitlement. Do not substitute IEX.

**Reasoning:** the fast and movers strategies were designed and tested against
consolidated-tape data. IEX is one exchange. Swapping the feed would not be a
repair, it would be a different experiment run under the old name — different
NBBO, different prints, different bar closes, and therefore different signals
at every threshold the sweep examined. The alternative was admissible but
expensive in the only currency that matters here: amend the specification to
IEX, re-run the historical work on IEX-compatible data, mint a new strategy
version hash, and recommission from zero. Preserving the pre-registration is
worth $99 a month; discovering afterwards that the arm was never testing what
the registry says it was testing is not.

## What has to be bought

Alpaca **Algo Trader Plus**, $99/month (Trading API, equities). Per Alpaca's
own documentation the Basic plan gives real-time IEX only and restricts
historical SIP queries to data at least 15 minutes old, which is precisely the
refusal the arms have been hitting:

> `subscription does not permit querying recent SIP data`

Purchased from the Alpaca dashboard under Plans & Features. **Arman buys it;
this is not something the bot or an assistant does.**

## The question nobody has answered yet

Whether one Algo Trader Plus subscription covers all three paper accounts, or
whether the entitlement attaches per account. Alpaca's docs describe the plan
as a property of the authenticated *user*, but the observed behaviour argues
the other way: identical code against three accounts, one of which reads
recent data and two of which are refused. Something is per-account.

This is answered by measurement, not by reading. Buy one subscription, run
`data-preflight`, and read the `data_plan` records. If the intraday accounts
clear, one subscription is enough. If they do not, the choices are three
subscriptions, or serving data to all three arms from the single entitled key
while each arm keeps its own trading credentials.

That second option deserves a note, because it looks like a trick and is not
one: the arms must have **separate books** — that is what makes the comparison
between them meaningful — but nothing in the design requires them to hold
separate *data* entitlements. The same SIP bytes read through one key or three
are the same information set, so the pre-registration is untouched. Whether
Alpaca's subscriber terms permit it is a question for Alpaca, and it must be
asked before it is done.

## What this decision exposed

The repository has never recorded which feed it was reading. The code passes
no `feed=` parameter, and Alpaca serves "the best available feed based on the
user's subscription" — so three accounts running identical code can be reading
three different tapes and leave a ledger that looks the same in every case.

The slow arm has been trading since before any of this and nobody knows, from
the record, whether its daily bars came from the consolidated tape or from
IEX. For daily closes on six liquid ETFs the difference is small. Small is not
the same as declared.

`data_plan_probe` now asks each account directly, every preflight — SIP inside
15 minutes, SIP outside 15 minutes, IEX — and writes the answer to the ledger
as a `data_plan` event. Advisory, never blocking. Once the entitlement is in
place the next step is to pass `feed` explicitly on every production call, so
the declared source and the served source can never drift apart silently
again. That change touches the trade path and belongs to recommissioning, not
to today.

## Commissioning status, unchanged by this decision

- **Slow arm:** commissioning evidence exists.
- **Fast and movers:** NOT COMMISSIONED. The market-data preflight prevents
  entry into their execution path, so no evidence about them is being
  generated at all.


---

## Binding the feed (branch `feed-binding`, not yet merged)

Prepared but deliberately unmerged: turning SIP from an assumption into an
enforced dependency will disable any arm that cannot obtain it, and until the
subscription exists that includes the slow arm — the only one currently
producing evidence. Merging before the purchase would stop the working arm to
prove a point. The sequence stays: buy, probe, document, merge, tag,
recommission.

What the branch does:

- `config.yaml` declares `data: {feed: sip, require_declared_feed: true}`.
  Changing that value is an amendment to the pre-registration, visible in the
  diff, not a runtime decision.
- Every production data call passes it: daily bars, intraday bars, latest
  quotes. One helper (`_feed_kw`) supplies it, so a new data call cannot
  quietly skip one.
- Quotes record `requested_feed` alongside the bid and ask exchange codes.
  Bars carry no source field, so a quote's exchange codes are the only
  evidence the API returns about where the data came from — on the free plan
  both read `V` (IEX). Requested and served can now be compared instead of
  assumed equal.
- Preflight probes the same declared feed through the same endpoints the
  trading path uses, and an arm served anything else does not trade.
  `sip_delayed` counts as anything else: fifteen-minute-old consolidated data
  is a different information set from live consolidated data, and the intraday
  arms are built on exactly that difference.
- When the probe itself fails, the check abstains rather than guesses. A
  provenance gap is not evidence that the wrong feed was served, and refusing
  to trade on a failed probe would be a different error from the one this
  check exists to prevent.

**One production call cannot be bound.** The most-actives screener endpoint
takes no `feed` parameter. It returns a ranking of symbols, not prices, and
the movers arm's decisions are made from bars and quotes that do carry the
declared feed — but the universe those decisions range over is selected by an
endpoint whose source we cannot pin. Stated here so that "every production
call is bound" is true as written rather than true by omission.

**There is no streaming path.** Nothing in `src/tradebot/` opens a websocket
today, so the streaming half of the binding is not yet written. It will be
needed if the arms ever move off polled bars.

## The slow arm's earlier record

Left as it is. From this point the feed is explicit and recorded; before it,
the honest statement is *feed not recorded*, and that is what the record will
continue to say. Reconstructing which tape those sessions read — from bar
volumes, or by re-querying the same windows under a known feed — would produce
an inference presented in the same place as measurements. The gap is smaller
than the confusion that would replace it.
