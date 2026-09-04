"""Broker interface. AlpacaBroker talks to the paper API; FakeBroker (tests)
implements the same surface. Alpaca imports are lazy so the test suite and
chat/report commands run without alpaca-py installed or keys configured."""
from __future__ import annotations
import os
import pandas as pd

from .session import ET


class BrokerError(RuntimeError):
    pass


class AlpacaBroker:
    """One instance per paper account.

    Each experiment arm now has its own account, so credentials are a
    constructor argument rather than a global. Two arms sharing an account
    would silently pool their positions and make the whole comparison
    meaningless — see `assert_distinct_accounts`.
    """

    def __init__(self, key_env: str = "ALPACA_API_KEY",
                 secret_env: str = "ALPACA_SECRET_KEY",
                 feed: str | None = None):
        self.key_env, self.secret_env = key_env, secret_env
        # The declared tape, passed on every data request. None means "let
        # Alpaca choose", which is what this repo did until 2026-09-04 and is
        # no longer allowed on a production path.
        self.feed = feed
        key = os.environ.get(key_env)
        secret = os.environ.get(secret_env)
        base = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        if not key or not secret:
            raise BrokerError(f"{key_env} / {secret_env} not set "
                              "(copy .env.example to .env and fill in paper keys)")
        if "paper" not in base:
            raise BrokerError("ALPACA_BASE_URL is not the paper endpoint. "
                              "Refusing to run: the paper evaluation has to pass "
                              "its pre-registered criteria before live keys go in.")
        from alpaca.trading.client import TradingClient
        from alpaca.data.historical import StockHistoricalDataClient
        self._trading = TradingClient(key, secret, paper=True)
        self._data = StockHistoricalDataClient(key, secret)
        self.dry_run = os.environ.get("DRY_RUN", "0") == "1"

    def market_open(self) -> bool:
        return bool(self._trading.get_clock().is_open)

    def trading_days(self, n: int) -> list:
        """The last n completed trading days, most recent last."""
        from datetime import datetime, timedelta
        from alpaca.trading.requests import GetCalendarRequest
        today = datetime.now(ET).date()
        days = self._trading.get_calendar(GetCalendarRequest(
            start=today - timedelta(days=int(n * 1.7) + 10), end=today))
        return [d.date for d in days if d.date < today][-n:]

    def session_today(self):
        """(open_utc, close_utc) for today from the exchange calendar, or None
        if today is not a trading day. Calendar, not a hardcoded 9:30-16:00:
        half days (day after Thanksgiving, Christmas Eve) close at 13:00 ET and
        a bot that does not know that carries positions it meant to flatten."""
        from datetime import datetime
        from alpaca.trading.requests import GetCalendarRequest
        from .session import to_session_utc
        today = datetime.now(ET).date()
        days = self._trading.get_calendar(GetCalendarRequest(start=today, end=today))
        if not days or days[0].date != today:
            return None
        d = days[0]
        return to_session_utc(d.date, d.open), to_session_utc(d.date, d.close)

    def now_et(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))

    def intraday_5min(self, symbols: list[str], day=None) -> dict[str, pd.DataFrame]:
        """5-minute bars per symbol for `day` (default today), ET timestamps,
        cols t/o/h/l/c. The `day` argument is what lets the threshold sweep
        replay past sessions through exactly this path."""
        from datetime import datetime, time as dtime
        from zoneinfo import ZoneInfo
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        et = ZoneInfo("America/New_York")
        day = day or datetime.now(et).date()
        start = datetime.combine(day, dtime(9, 30), et)
        end = datetime.combine(day, dtime(16, 0), et)
        req = StockBarsRequest(symbol_or_symbols=symbols,
                               timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                               start=start, end=end, **self._feed_kw())
        df = self._data.get_stock_bars(req).df.reset_index()
        out: dict[str, pd.DataFrame] = {}
        for sym in symbols:
            sub = df[df["symbol"] == sym].sort_values("timestamp")
            if sub.empty:
                continue
            out[sym] = pd.DataFrame({
                "t": pd.to_datetime(sub["timestamp"]).dt.tz_convert(et),
                "o": sub["open"].to_numpy(), "h": sub["high"].to_numpy(),
                "l": sub["low"].to_numpy(), "c": sub["close"].to_numpy()})
        return out

    def equity(self) -> float:
        return float(self._trading.get_account().equity)

    def cash(self) -> float:
        return float(self._trading.get_account().cash)

    def account_number(self) -> str:
        return str(self._trading.get_account().account_number)

    def account_regime(self) -> dict:
        """What the broker actually thinks this account is.

        FINRA's day-trading margin framework changed on 2026-06-04 with a
        transition period running to 2027-10-20, so implementations differ
        between firms and can change under us. A $50 account is below the
        $2,000 margin minimum, which usually means cash treatment and T+1
        settlement — but that is an assumption until the broker says so, and
        the fast arm's six-trades-a-day budget depends on the answer.
        """
        a = self._trading.get_account()
        return {"multiplier": str(getattr(a, "multiplier", "?")),
                "shorting_enabled": bool(getattr(a, "shorting_enabled", False)),
                "pattern_day_trader": bool(getattr(a, "pattern_day_trader", False)),
                "daytrade_count": int(getattr(a, "daytrade_count", 0) or 0),
                "daytrading_buying_power":
                    str(getattr(a, "daytrading_buying_power", "?")),
                "non_marginable_buying_power":
                    str(getattr(a, "non_marginable_buying_power", "?")),
                "status": str(getattr(a, "status", "?")),
                "trading_blocked": bool(getattr(a, "trading_blocked", False))}

    def positions(self) -> dict[str, float]:
        return {p.symbol: float(p.market_value)
                for p in self._trading.get_all_positions()}

    def positions_detail(self) -> dict[str, dict]:
        """Quantity and entry price per symbol, straight from the account.

        The intraday arms used to keep their own idea of what they held. Two
        sets of books drift, and the drift is silent. Now the account is the
        only record of position, and the arm keeps nothing but its own
        metadata (stop levels, trade counts)."""
        return {p.symbol: {"qty": float(p.qty),
                           "entry_px": float(p.avg_entry_price),
                           "market_value": float(p.market_value)}
                for p in self._trading.get_all_positions()}

    def daily_closes(self, symbols: list[str], days: int) -> dict[str, pd.Series]:
        from datetime import datetime, timedelta, timezone
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        start = datetime.now(timezone.utc) - timedelta(days=int(days * 1.7) + 30)
        req = StockBarsRequest(symbol_or_symbols=symbols,
                               timeframe=TimeFrame.Day, start=start,
                               **self._feed_kw())
        bars = self._data.get_stock_bars(req)
        out: dict[str, pd.Series] = {}
        df = bars.df.reset_index()
        for sym in symbols:
            sub = df[df["symbol"] == sym].sort_values("timestamp")
            out[sym] = pd.Series(sub["close"].to_numpy(), name=sym)
        return out

    def most_actives(self, n: int) -> list[str]:
        """Top-n most-active stocks by volume today (screener API).

        The one production data call the feed binding cannot cover: the
        screener endpoint takes no `feed` parameter. It returns a ranking of
        symbols rather than prices, and the movers arm's actual decisions are
        made from bars and quotes that do carry the declared feed — but the
        universe those decisions range over is selected by an endpoint whose
        source we cannot pin. Recorded here so the claim "every production
        call is bound" stays true as written and not by omission.
        """
        from alpaca.data.historical.screener import ScreenerClient
        from alpaca.data.requests import MostActivesRequest
        import os as _os
        client = ScreenerClient(_os.environ[self.key_env],
                                _os.environ[self.secret_env])
        resp = client.get_most_actives(MostActivesRequest(top=n))
        return [a.symbol for a in resp.most_actives][:n]

    def _feed_kw(self) -> dict:
        """`feed=` for every data request, or nothing if none is declared.

        Kept in one place so a new data call cannot quietly skip it.
        """
        return {"feed": self.feed} if self.feed else {}

    def data_plan_probe(self, symbol: str = "SPY") -> dict:
        """Which data feed is this account actually entitled to?

        Nothing in this repository has ever recorded the answer. The code has
        never passed `feed=`, and Alpaca's documented behaviour is to serve
        "the best available feed based on the user's subscription" — so three
        accounts running identical code can be reading three different tapes
        and the ledger would look the same either way. On 2026-09-01 that
        stopped being hypothetical.

        Three questions, asked directly:
          sip_recent   — SIP inside the last 15 minutes (needs Algo Trader Plus)
          sip_delayed  — SIP older than 15 minutes (available on the free plan)
          iex          — the free single-exchange feed

        Advisory only. It disables nothing; it writes down what we are
        actually reading, so the pre-registration can name a feed instead of
        assuming one.
        """
        from datetime import datetime, timedelta, timezone
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame

        now = datetime.now(timezone.utc)
        out: dict[str, object] = {"symbol": symbol, "asof": now.isoformat()}

        def ask(label, feed, end):
            try:
                req = StockBarsRequest(symbol_or_symbols=symbol,
                                       timeframe=TimeFrame.Day,
                                       start=now - timedelta(days=7),
                                       end=end, feed=feed)
                bars = self._data.get_stock_bars(req)
                out[label] = "ok" if len(bars.df) else "empty"
            except Exception as exc:                  # noqa: BLE001 - grading it
                msg = str(exc)
                out[label] = ("denied" if "subscription" in msg.lower()
                              else f"{type(exc).__name__}: {msg}"[:160])

        ask("sip_recent", "sip", now)
        ask("sip_delayed", "sip", now - timedelta(minutes=20))
        ask("iex", "iex", now)
        out["effective_feed"] = ("sip" if out.get("sip_recent") == "ok"
                                 else "sip_delayed" if out.get("sip_delayed") == "ok"
                                 else "iex" if out.get("iex") == "ok"
                                 else "unknown")
        return out

    def quote_snapshot(self, symbol: str) -> dict:
        """Best bid/ask and the midquote, right now.

        Recorded beside every fill so execution cost can later be measured
        against the prevailing midpoint (Harris, *Trading and Exchanges*,
        ch. 14) instead of assumed. This CHANGES NO METRIC — the
        pre-registered criterion still reads the modeled 5/15 bps. It only
        makes the assumption checkable after the fact.

        Never raises: a missing quote costs us a measurement, and a trade the
        strategy called for must not be lost to a data hiccup.
        """
        try:
            from alpaca.data.requests import StockLatestQuoteRequest
            q = self._data.get_stock_latest_quote(
                StockLatestQuoteRequest(symbol_or_symbols=symbol,
                                        **self._feed_kw()))[symbol]
            bid, ask = float(q.bid_price), float(q.ask_price)
            if bid <= 0 or ask <= 0 or ask < bid:
                return {"quote": None, "quote_error": f"crossed/empty {bid}/{ask}"}
            mid = (bid + ask) / 2
            # The exchange codes are the only evidence the API gives back
            # about where a quote came from: on the free plan both read "V"
            # (IEX). Recorded so the served source can be checked against the
            # requested one instead of taken on faith.
            return {"bid": round(bid, 4), "ask": round(ask, 4),
                    "mid": round(mid, 6),
                    "spread_bps": round((ask - bid) / mid * 1e4, 2),
                    "quote_ts": str(getattr(q, "timestamp", "")),
                    "requested_feed": self.feed or "unspecified",
                    "bid_exchange": str(getattr(q, "bid_exchange", "") or ""),
                    "ask_exchange": str(getattr(q, "ask_exchange", "") or "")}
        except Exception as exc:
            return {"quote": None, "quote_error": f"{type(exc).__name__}: {exc}"}

    def submit(self, order: dict) -> dict:
        if self.dry_run:
            return {**order, "status": "dry_run"}
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        kw = {"qty": order["qty"]} if order.get("qty") \
            else {"notional": round(order["notional"], 2)}
        if order.get("client_order_id"):
            # Broker-enforced idempotency. Alpaca rejects a duplicate
            # client_order_id, so a retried workflow, a dropped connection
            # after submission, or a restarted leg cannot place the same
            # order twice. Cheaper and far more reliable than trying to
            # reconstruct intent from our own state after a crash.
            kw["client_order_id"] = order["client_order_id"]
        req = MarketOrderRequest(
            symbol=order["symbol"],
            side=OrderSide.BUY if order["side"] == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            **kw,
        )
        try:
            resp = self._trading.submit_order(req)
        except Exception as exc:
            if "client_order_id" in str(exc).lower() or "duplicate" in str(exc).lower():
                return {**order, "status": "duplicate_suppressed",
                        "detail": str(exc)[:200]}
            raise
        return {**order, "status": str(resp.status),
                "broker_order_id": str(resp.id),
                "submitted_at": str(getattr(resp, "submitted_at", "")),
                "filled_qty": float(getattr(resp, "filled_qty", 0) or 0),
                "filled_avg_price": float(getattr(resp, "filled_avg_price", 0) or 0)}


    def close(self, symbol: str) -> dict:
        """Liquidate a position outright.

        Exits go through close_position rather than a sell order sized from
        our own idea of the quantity: the account knows exactly what it holds,
        and a rounding difference would leave dust behind that the arm thinks
        it has already sold."""
        if self.dry_run:
            return {"symbol": symbol, "side": "sell", "status": "dry_run"}
        resp = self._trading.close_position(symbol)
        return {"symbol": symbol, "side": "sell", "status": str(resp.status),
                "broker_order_id": str(resp.id)}


def assert_distinct_accounts(brokers: dict[str, object]) -> dict[str, str]:
    """Refuse to start if two arms point at the same account.

    A misconfigured secret would not raise anything — the arms would simply
    trade into one pot and every comparison in the study would be garbage
    while looking perfectly healthy. Fail loudly, before the open.
    """
    numbers: dict[str, str] = {}
    for arm, broker in brokers.items():
        numbers[arm] = broker.account_number()
    if len(set(numbers.values())) != len(numbers):
        raise BrokerError(f"arms share a paper account: {numbers}")
    return numbers
