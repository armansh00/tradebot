"""Prove each account can fetch the data its arm trades on — before the bell.

2026-09-01: the fast and movers arms had just been moved onto their own paper
accounts. Both accounts authenticated, both accepted orders, both reported
equity. Neither carried the market-data entitlement the original account has,
so the first intraday bar request of the day came back

    APIError: subscription does not permit querying recent SIP data

and every intraday tick died the same way, all day, twice. Nothing in the
system objected until a human read the ledger the next morning.

Two lessons, and this module is both of them:

1. Credentials working is not data working. An account is only commissioned
   for an arm once it has fetched, through the production code path, the exact
   request that arm issues during the session.
2. A data failure must disable the arm loudly rather than degrade it quietly.
   In particular this module never falls back to another feed. SIP and IEX are
   different information sets — IEX is roughly 2-3% of consolidated volume, so
   its NBBO, its last trade and its bar closes are not the SIP's. Swapping
   feeds to make the error go away would keep the arm running under a name it
   no longer earns, against a pre-registration written on SIP. If the
   entitlement is missing, the correct outcomes are: get the entitlement, or
   re-declare the data source and re-run the design. Not: trade anyway.

The probes deliberately test PERMISSION AND REACHABILITY, not data presence.
An empty result before the opening bell is normal; an exception is not.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

BLOCKING = "blocking"
ADVISORY = "advisory"


@dataclass(frozen=True)
class ProbeResult:
    arm: str
    probe: str
    ok: bool
    severity: str          # BLOCKING | ADVISORY
    detail: str = ""

    @property
    def disqualifying(self) -> bool:
        return (not self.ok) and self.severity == BLOCKING

    def as_dict(self) -> dict:
        return {"arm": self.arm, "probe": self.probe, "ok": self.ok,
                "severity": self.severity, "detail": self.detail}


def _plan(broker) -> dict:
    """What tape is this account actually reading? Advisory, never blocking."""
    if not hasattr(broker, "data_plan_probe"):
        return {}
    try:
        return broker.data_plan_probe()
    except Exception as exc:                          # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


@dataclass
class PreflightReport:
    results: list[ProbeResult] = field(default_factory=list)
    disabled: set[str] = field(default_factory=set)
    plans: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """Every arm cleared every blocking probe."""
        return bool(self.results) and not self.failures

    @property
    def any_enabled(self) -> bool:
        """At least one arm may trade. False means there is no session to run,
        only a report to write."""
        return bool(self.results) and bool(
            {"slow", "fast", "movers"} - self.disabled)

    @property
    def failures(self) -> list[ProbeResult]:
        return [r for r in self.results if r.disqualifying]

    def summary(self) -> str:
        lines = []
        for r in self.results:
            mark = "ok  " if r.ok else ("FAIL" if r.severity == BLOCKING else "warn")
            lines.append(f"[{mark}] {r.arm}/{r.probe} {r.detail}".rstrip())
        for arm, plan in sorted(self.plans.items()):
            if plan:
                lines.append(f"[feed] {arm} effective={plan.get('effective_feed')} "
                             f"sip_recent={plan.get('sip_recent')} "
                             f"sip_delayed={plan.get('sip_delayed')} "
                             f"iex={plan.get('iex')}")
        if self.disabled:
            lines.append("DATA_PREFLIGHT_FAIL — arms disabled: "
                         + ", ".join(sorted(self.disabled)))
        return "\n".join(lines)


def _probe(arm: str, name: str, fn, *, severity: str = BLOCKING,
           empty_is_failure: bool = True) -> ProbeResult:
    """Run one production data call and grade it.

    An exception is always a failure: it is the account being told no. An
    empty-but-successful result is a failure only where emptiness cannot
    happen legitimately (a daily-bar history, a screener), and is tolerated
    where it can (intraday bars requested before the first bar exists).
    """
    try:
        value = fn()
    except Exception as exc:                       # noqa: BLE001 - grading it
        return ProbeResult(arm, name, False, severity,
                           f"{type(exc).__name__}: {exc}"[:300])
    n = len(value) if hasattr(value, "__len__") else 1
    if not n and empty_is_failure:
        return ProbeResult(arm, name, False, severity, "returned nothing")
    if not n:
        return ProbeResult(arm, name, True, severity, "reachable, no rows yet")
    return ProbeResult(arm, name, True, severity, f"{n} rows")


def probe_slow(cfg, broker) -> list[ProbeResult]:
    """The slow arm reads daily closes and nothing else. History is never
    legitimately empty, so an empty answer is an entitlement problem wearing
    a disguise."""
    syms = list(cfg.universe)[:2]
    days = int(cfg.strategy.mom_lookback_days)
    return [_probe("slow", "daily_closes",
                   lambda: broker.daily_closes(syms, days))]


def probe_intraday(cfg, broker, arm: str) -> list[ProbeResult]:
    """fast and movers both read today's 5-minute bars, and movers reads the
    screener first. The bar request is issued exactly as `run_fast_once`
    issues it — same symbols, same timeframe, same session window — because a
    probe shaped differently from the real call is a probe that can pass while
    the real call is refused. That is precisely how 2026-09-01 happened."""
    f = cfg.fast if arm == "fast" else cfg.movers
    out: list[ProbeResult] = []
    universe = list(f.universe)

    if f.universe_mode == "most_active":
        try:
            screened = list(broker.most_actives(f.universe_size))
            out.append(ProbeResult(arm, "most_actives", bool(screened), BLOCKING,
                                   f"{len(screened)} symbols" if screened
                                   else "returned nothing"))
        except Exception as exc:                   # noqa: BLE001 - grading it
            screened = []
            out.append(ProbeResult(arm, "most_actives", False, BLOCKING,
                                   f"{type(exc).__name__}: {exc}"[:300]))
        universe = screened or list(cfg.universe)  # still test the bar path

    universe = universe[:2] or list(cfg.universe)[:2]
    out.append(_probe(arm, "intraday_5min",
                      lambda: broker.intraday_5min(universe),
                      empty_is_failure=False))

    # Quotes are recorded beside fills for execution-cost measurement; they
    # feed no pre-registered metric and `quote_snapshot` is fail-open by
    # contract. Losing them costs a measurement, not a decision — advisory.
    # `quote_snapshot` swallows its own errors by design, so grading it needs
    # the returned payload, not the absence of an exception.
    try:
        snap = broker.quote_snapshot(universe[0]) or {}
        err = snap.get("quote_error")
        out.append(ProbeResult(arm, "quote_snapshot", not err, ADVISORY,
                               err or f"mid {snap.get('mid')}"))
    except Exception as exc:                       # noqa: BLE001 - grading it
        out.append(ProbeResult(arm, "quote_snapshot", False, ADVISORY,
                               f"{type(exc).__name__}: {exc}"[:300]))
    return out


def _notify(report: PreflightReport) -> None:
    """Say it where a human will see it without going looking: the job log and
    the Actions run summary. The ledger is the record; this is the alarm."""
    text = report.summary()
    print(text, file=sys.stderr)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        try:
            with open(path, "a") as fh:
                fh.write("### tradebot data preflight\n```\n" + text + "\n```\n")
        except OSError:
            pass


def run_preflight(cfg, brokers: dict, *, ledger=None, notify=_notify) -> PreflightReport:
    """Commission every arm against its own account. Returns which ones may
    trade today. Arms are disabled individually: a dead screener entitlement
    on the movers account is no reason to stop the slow arm."""
    report = PreflightReport()

    for arm in ("slow", "fast", "movers"):
        broker = brokers.get(arm)
        if broker is None:
            report.results.append(
                ProbeResult(arm, "broker", False, BLOCKING, "no broker configured"))
            report.disabled.add(arm)
            continue
        results = (probe_slow(cfg, broker) if arm == "slow"
                   else probe_intraday(cfg, broker, arm))
        report.results.extend(results)
        report.plans[arm] = _plan(broker)
        if any(r.disqualifying for r in results):
            report.disabled.add(arm)

    if ledger is not None:
        for r in report.results:
            ledger.write("preflight", **r.as_dict())
        for arm, plan in report.plans.items():
            if plan:
                ledger.write("data_plan", arm=arm, **plan)
        if report.disabled:
            ledger.write("preflight", status="DATA_PREFLIGHT_FAIL",
                         disabled=sorted(report.disabled),
                         reason=[r.detail for r in report.failures])

    if report.disabled and notify:
        notify(report)
    return report
