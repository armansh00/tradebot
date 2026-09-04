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
