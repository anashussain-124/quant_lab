"""MetaTrader 5 adapter for XAUUSD.

Per AGENTS.md A2 & A4:
- Connect to MT5 demo environment
- Verify trade_mode == DEMO before every order
- Normalize timestamps to UTC
- All prices as Decimal
"""
from __future__ import annotations

from typing import Any
from decimal import Decimal
import pandas as pd
import numpy as np

from adapters import BaseAdapter, TickData, OrderBook, DataQualityScore
from engine.logger import AuditLogger


class MetaTrader5Adapter(BaseAdapter):
    """Real MetaTrader 5 adapter for XAUUSD."""

    def __init__(
        self,
        account: int = 5056172579,
        server: str = "MetaQuotes-Demo",
        symbol: str = "XAUUSD",
    ) -> None:
        super().__init__("mt5")
        self.account = account
        self.server = server
        self.symbol = symbol
        self.audit = AuditLogger()
        self._connected = False
        self._initialized = False

    def initialize(self) -> bool:
        """Initialize MT5 connection and verify DEMO mode."""
        import MetaTrader5 as MT5

        self._initialized = MT5.initialize()
        if not self._initialized:
            self.audit.log(
                event="MT5_INIT_FAILED",
                action="Failed to initialize MetaTrader5",
                details={},
                actor="mt5_adapter",
            )
            return False

        # Verify DEMO mode
        info = MT5.account_info()
        if info is None:
            self.audit.log(
                event="MT5_NO_ACCOUNT",
                action="Could not get account info",
                details={},
                actor="mt5_adapter",
            )
            return False

        if info.trade_mode != 0:  # 0 = DEMO
            self.audit.log(
                event="MT5_NOT_DEMO",
                action=f"Account is not in DEMO mode: {info.trade_mode}",
                details={"trade_mode": info.trade_mode},
                actor="mt5_adapter",
            )
            return False

        # Select symbol
        if not MT5.symbol_select(self.symbol, True):
            self.audit.log(
                event="MT5_SYMBOL_SELECT_FAILED",
                action=f"Could not select {self.symbol}",
                details={"symbol": self.symbol},
                actor="mt5_adapter",
            )
            return False

        self._connected = True
        self.audit.log(
            event="MT5_CONNECTED",
            action=f"Connected to MT5 demo: {self.symbol}",
            details={
                "account": self.account,
                "server": self.server,
                "balance": info.balance,
                "leverage": info.leverage,
            },
            actor="mt5_adapter",
        )
        return True

    def shutdown(self) -> None:
        """Shutdown MT5 connection."""
        import MetaTrader5 as MT5
        MT5.shutdown()
        self._connected = False

    @property
    def is_demo(self) -> bool:
        """Check if account is in DEMO mode."""
        import MetaTrader5 as MT5
        info = MT5.account_info()
        return info is not None and info.trade_mode == 0

    @property
    def is_connected(self) -> bool:
        """Check if MT5 is connected."""
        return self._connected and self._initialized

    def fetch_ticks(self, symbol: str = "", limit: int = 100) -> list[TickData]:
        """Fetch REAL tick data from MT5."""
        import MetaTrader5 as MT5

        if not self.is_connected:
            return []

        symbol = symbol or self.symbol
        ticks: list[TickData] = []

        try:
            mt5_ticks = MT5.copy_ticks_from_pos(symbol, 0, limit, MT5.COPY_TICKS_ALL)
            if mt5_ticks is None:
                return ticks

            for mt5_tick in mt5_ticks:
                tick = TickData(
                    symbol=symbol,
                    timestamp=pd.Timestamp(mt5_tick["time"], unit="s", tz="UTC"),
                    bid=Decimal(str(mt5_tick["bid"])),
                    ask=Decimal(str(mt5_tick["ask"])),
                    volume=Decimal(str(mt5_tick["volume"])),
                    sequence=mt5_tick["last_volume"],
                )
                ticks.append(tick)
        except Exception as e:
            self.audit.log(
                event="FETCH_ERROR",
                action=f"Failed to fetch MT5 ticks: {e}",
                details={"symbol": symbol},
                actor="mt5_adapter",
            )

        return ticks

    def fetch_order_book(self, symbol: str = "") -> OrderBook | None:
        """Fetch current order book from MT5."""
        import MetaTrader5 as MT5

        if not self.is_connected:
            return None

        symbol = symbol or self.symbol
        info = MT5.symbol_info(symbol)
        if info is None:
            return None

        bids = [(Decimal(str(info.bid)), 1)]
        asks = [(Decimal(str(info.ask)), 1)]

        return OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=pd.Timestamp.now(tz="UTC"),
            sequence=0,
        )

    def fetch_historical(
        self, symbol: str = "", timeframe: str = "1h", limit: int = 1000
    ) -> pd.DataFrame:
        """Fetch REAL historical OHLCV data from MT5."""
        import MetaTrader5 as MT5

        if not self.is_connected:
            return pd.DataFrame()

        symbol = symbol or self.symbol

        timeframe_map = {
            "1m": MT5.TIMEFRAME_M1,
            "5m": MT5.TIMEFRAME_M5,
            "15m": MT5.TIMEFRAME_M15,
            "30m": MT5.TIMEFRAME_M30,
            "1h": MT5.TIMEFRAME_H1,
            "4h": MT5.TIMEFRAME_H4,
            "1d": MT5.TIMEFRAME_D1,
            "1w": MT5.TIMEFRAME_W1,
        }

        tf = timeframe_map.get(timeframe, MT5.TIMEFRAME_H1)

        try:
            # Try with requested limit first, then fall back to smaller
            for try_limit in [limit, 500, 100, 50]:
                rates = MT5.copy_rates_from_pos(symbol, tf, 0, try_limit)
                if rates is not None and len(rates) > 0:
                    break

            if rates is None or len(rates) == 0:
                self.audit.log(
                    event="NO_DATA",
                    action=f"No historical data for {symbol} {timeframe}",
                    details={"symbol": symbol, "timeframe": timeframe},
                    actor="mt5_adapter",
                )
                return pd.DataFrame()

            df = pd.DataFrame(rates)
            df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
            df = df.rename(columns={
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "tick_volume": "volume",
            })
            df = df[["timestamp", "open", "high", "low", "close", "volume"]]
            df = df.sort_values("timestamp").reset_index(drop=True)

            # Compute quality
            quality = self.compute_quality(df)
            if not quality.passed:
                self.audit.log(
                    event="DATA_QUALITY_LOW",
                    action=f"Quality score {quality.score:.2f} for {symbol}",
                    details=quality.to_dict(),
                    actor="mt5_adapter",
                )

            return df

        except Exception as e:
            self.audit.log(
                event="FETCH_ERROR",
                action=f"Failed to fetch MT5 historical: {e}",
                details={"symbol": symbol, "timeframe": timeframe},
                actor="mt5_adapter",
            )
            return pd.DataFrame()

    def get_account_info(self) -> dict[str, Any]:
        """Get real account information."""
        import MetaTrader5 as MT5

        info = MT5.account_info()
        if info is None:
            return {}

        return {
            "login": info.login,
            "trade_mode": info.trade_mode,
            "trade_mode_demo": info.trade_mode == 0,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "margin_free": info.margin_free,
            "leverage": info.leverage,
            "server": info.server,
            "currency": info.currency,
        }

    def get_symbol_info(self, symbol: str = "") -> dict[str, Any]:
        """Get real symbol specifications."""
        import MetaTrader5 as MT5

        symbol = symbol or self.symbol
        info = MT5.symbol_info(symbol)
        if info is None:
            return {}

        return {
            "symbol": symbol,
            "bid": info.bid,
            "ask": info.ask,
            "spread": info.spread,
            "contract_size": info.trade_contract_size,
            "min_lot": info.volume_min,
            "max_lot": info.volume_max,
            "lot_step": info.volume_step,
            "point": info.point,
            "tick_value": info.trade_tick_value,
            "tick_size": info.trade_tick_size,
        }

    def check_connectivity(self) -> bool:
        """Check MT5 connectivity."""
        import MetaTrader5 as MT5
        try:
            MT5.terminal_info()
            return True
        except Exception:
            return False


def create_mt5_adapter(
    account: int = 5056172579,
    server: str = "MetaQuotes-Demo",
    symbol: str = "XAUUSD",
) -> MetaTrader5Adapter:
    """Create an MT5 adapter."""
    return MetaTrader5Adapter(account=account, server=server, symbol=symbol)
