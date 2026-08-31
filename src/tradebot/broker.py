"""Broker interface. AlpacaBroker talks to the paper API; FakeBroker (tests)
implements the same surface. Alpaca imports are lazy so the test suite and
chat/report commands run without alpaca-py installed or keys configured."""
from __future__ import annotations
import os
import pandas as pd


class BrokerError(RuntimeError):
    pass


class AlpacaBroker:
    def __init__(self):
        key = os.environ.get("ALPACA_API_KEY")
        secret = os.environ.get("ALPACA_SECRET_KEY")
        base = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
        if not key or not secret:
            raise BrokerError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set "
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

    def session_today(self):
        """(open_utc, close_utc) for today from the exchange calendar, or None
        if today is not a trading day. Calendar, not a hardcoded 9:30-16:00:
        half days (day after Thanksgiving, Christmas Eve) close at 13:00 ET and
        a bot that does not know that carries positions it meant to flatten."""
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        from alpaca.trading.requests import GetCalendarRequest
        et = ZoneInfo("America/New_York")
        today = datetime.now(et).date()
        days = self._trading.get_calendar(GetCalendarRequest(start=today, end=today))
        if not days or days[0].date != today:
            return None
        d = days[0]
        to_utc = lambda t: (datetime.combine(d.date, t)
                            .replace(tzinfo=et).astimezone(timezone.utc))
        return to_utc(d.open), to_utc(d.close)

    def now_et(self):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York"))

    def intraday_5min(self, symbols: list[str]) -> dict[str, pd.DataFrame]:
        """Today's 5-minute bars per symbol, ET timestamps, cols t/o/h/l/c."""
        from datetime import datetime, time as dtime
        from zoneinfo import ZoneInfo
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        et = ZoneInfo("America/New_York")
        start = datetime.combine(datetime.now(et).date(), dtime(9, 30), et)
        req = StockBarsRequest(symbol_or_symbols=symbols,
                               timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                               start=start)
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

    def positions(self) -> dict[str, float]:
        return {p.symbol: float(p.market_value)
                for p in self._trading.get_all_positions()}

    def daily_closes(self, symbols: list[str], days: int) -> dict[str, pd.Series]:
        from datetime import datetime, timedelta, timezone
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        start = datetime.now(timezone.utc) - timedelta(days=int(days * 1.7) + 30)
        req = StockBarsRequest(symbol_or_symbols=symbols,
                               timeframe=TimeFrame.Day, start=start)
        bars = self._data.get_stock_bars(req)
        out: dict[str, pd.Series] = {}
        df = bars.df.reset_index()
        for sym in symbols:
            sub = df[df["symbol"] == sym].sort_values("timestamp")
            out[sym] = pd.Series(sub["close"].to_numpy(), name=sym)
        return out

    def most_actives(self, n: int) -> list[str]:
        """Top-n most-active stocks by volume today (screener API)."""
        from alpaca.data.historical.screener import ScreenerClient
        from alpaca.data.requests import MostActivesRequest
        import os as _os
        client = ScreenerClient(_os.environ["ALPACA_API_KEY"],
                                _os.environ["ALPACA_SECRET_KEY"])
        resp = client.get_most_actives(MostActivesRequest(top=n))
        return [a.symbol for a in resp.most_actives][:n]

    def submit(self, order: dict) -> dict:
        if self.dry_run:
            return {**order, "status": "dry_run"}
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        req = MarketOrderRequest(
            symbol=order["symbol"],
            notional=round(order["notional"], 2),
            side=OrderSide.BUY if order["side"] == "buy" else OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        )
        resp = self._trading.submit_order(req)
        return {**order, "status": str(resp.status), "broker_order_id": str(resp.id)}
