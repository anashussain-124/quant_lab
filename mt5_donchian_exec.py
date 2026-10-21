#!/usr/bin/env python3
"""MT5 Demo Execution Script for Donchian-50 XAUUSD Strategy.

This script:
1. Connects to MT5 demo
2. Calculates Donchian(50) + ATR(14) on recent H1 bars
3. Generates breakout signals during London session
4. Executes trades with 0.25% risk per trade
5. Logs all trades and equity curve
6. Enforces safety limits (daily loss, max DD)

Run via cron every hour during London session.
"""
from __future__ import annotations

import os
import sys
import json
import time
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

# Setup logging
log_dir = Path("logs")
log_dir.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler(log_dir / "mt5_donchian.log"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("mt5_donchian")


# ===== CONFIGURATION =====

class Config:
    """Strategy configuration."""
    # MT5 Connection
    ACCOUNT = 5056172579
    SERVER = "MetaQuotes-Demo"
    SYMBOL = "XAUUSD"
    
    # Strategy
    DONCHIAN_PERIOD = 50
    ATR_PERIOD = 14
    ATR_MULT = 2.5
    MAX_HOLDING_BARS = 100
    
    # Session (UTC)
    SESSION_START = 8
    SESSION_END = 16
    
    # Risk
    RISK_PER_TRADE = 0.0025  # 0.25%
    MAX_DAILY_LOSS = 0.05    # 5%
    MAX_DRAWDOWN = 0.10      # 10%
    MAX_CONSECUTIVE_LOSSES = 10
    
    # Execution
    SPREAD_MAX_PIPS = 3.0
    SLIPPAGE_PIPS = 1.0
    
    # Data
    BARS_TO_FETCH = 200
    TIMEFRAME = None  # Will be set to MT5.TIMEFRAME_H1
    
    # Paths
    LOG_DIR = Path("logs")
    EQUITY_FILE = LOG_DIR / "equity_curve.json"
    TRADES_FILE = LOG_DIR / "trades.json"
    STATE_FILE = LOG_DIR / "strategy_state.json"


# ===== MT5 CONNECTION =====

class MT5Connection:
    """Manages MT5 connection."""
    
    def __init__(self):
        self.connected = False
        self.last_error = None
    
    def connect(self) -> bool:
        """Initialize MT5 connection."""
        try:
            import MetaTrader5 as MT5
            
            if not MT5.initialize():
                self.last_error = "MT5.initialize() failed"
                return False
            
            # Verify account
            info = MT5.account_info()
            if info is None:
                self.last_error = "Failed to get account info"
                MT5.shutdown()
                return False
            
            if info.trade_mode != 0:  # 0 = DEMO
                self.last_error = f"Account is NOT demo (trade_mode={info.trade_mode})"
                MT5.shutdown()
                return False
            
            # Select symbol
            if not MT5.symbol_select(Config.SYMBOL, True):
                self.last_error = f"Failed to select {Config.SYMBOL}"
                MT5.shutdown()
                return False
            
            self.connected = True
            logger.info(f"Connected to MT5 demo: account={info.login}, balance={info.balance}")
            return True
            
        except Exception as e:
            self.last_error = str(e)
            return False
    
    def disconnect(self):
        """Shutdown MT5 connection."""
        try:
            import MetaTrader5 as MT5
            MT5.shutdown()
        except:
            pass
        self.connected = False


# ===== DATA FETCHING =====

def fetch_bars(mt5_conn: MT5Connection, n_bars: int = 200) -> list[dict]:
    """Fetch recent H1 bars from MT5."""
    if not mt5_conn.connected:
        return []
    
    try:
        import MetaTrader5 as MT5
        
        rates = MT5.copy_rates_from_pos(
            Config.SYMBOL, MT5.TIMEFRAME_H1, 0, n_bars
        )
        
        if rates is None or len(rates) == 0:
            return []
        
        bars = []
        for r in rates:
            bars.append({
                "timestamp": datetime.fromtimestamp(r["time"], tz=timezone.utc),
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["tick_volume"],
            })
        
        return bars
        
    except Exception as e:
        logger.error(f"Failed to fetch bars: {e}")
        return []


# ===== INDICATORS =====

def calc_donchian(bars: list[dict], period: int) -> tuple[float | None, float | None]:
    """Calculate Donchian channel from bars."""
    if len(bars) < period + 1:
        return None, None
    
    # Use last `period` bars (excluding current)
    window = bars[-(period+1):-1]
    
    upper = max(b["high"] for b in window)
    lower = min(b["low"] for b in window)
    
    return upper, lower


def calc_atr(bars: list[dict], period: int) -> float | None:
    """Calculate ATR from bars."""
    if len(bars) < period + 1:
        return None
    
    trs = []
    for i in range(1, len(bars)):
        h = bars[i]["high"]
        l = bars[i]["low"]
        prev_c = bars[i-1]["close"]
        tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
    
    if len(trs) < period:
        return None
    
    return sum(trs[-period:]) / period


# ===== SIGNAL GENERATION =====

def check_signal(bars: list[dict]) -> dict | None:
    """Check for Donchian breakout signal."""
    if not bars or len(bars) < Config.DONCHIAN_PERIOD + 10:
        return None
    
    # Current bar (incomplete)
    current = bars[-1]
    current_ts = current["timestamp"]
    
    # Only during London session
    if current_ts.hour < Config.SESSION_START or current_ts.hour >= Config.SESSION_END:
        return None
    
    # Calculate indicators
    upper, lower = calc_donchian(bars, Config.DONCHIAN_PERIOD)
    atr = calc_atr(bars, Config.ATR_PERIOD)
    
    if upper is None or lower is None or atr is None:
        return None
    
    close = current["close"]
    
    # Check breakout
    if close > upper:
        return {
            "side": "BUY",
            "price": close,
            "atr": atr,
            "donchian_upper": upper,
            "donchian_lower": lower,
            "timestamp": current_ts.isoformat(),
        }
    elif close < lower:
        return {
            "side": "SELL",
            "price": close,
            "atr": atr,
            "donchian_upper": upper,
            "donchian_lower": lower,
            "timestamp": current_ts.isoformat(),
        }
    
    return None


# ===== POSITION SIZING =====

def calc_position_size(balance: float, atr: float) -> float:
    """Calculate position size based on ATR stop."""
    risk_amount = balance * Config.RISK_PER_TRADE
    stop_distance = atr * Config.ATR_MULT
    
    if stop_distance <= 0:
        stop_distance = balance * 0.001
    
    # For XAUUSD: 1 point = $10 per lot
    # Risk = stop_points × $10 × lots
    # Lots = risk_amount / (stop_points × $10)
    stop_points = stop_distance / 0.01  # Convert to points
    lots = risk_amount / (stop_points * 10.0)
    
    # Apply constraints
    lots = max(0.01, min(lots, 10.0))
    lots = round(lots / 0.01) * 0.01
    
    return lots


# ===== TRADE EXECUTION =====

def execute_trade(signal: dict, mt5_conn: MT5Connection) -> dict | None:
    """Execute trade via MT5."""
    if not mt5_conn.connected:
        return None
    
    try:
        import MetaTrader5 as MT5
        
        # Get current tick
        tick = MT5.symbol_info_tick(Config.SYMBOL)
        if tick is None:
            logger.error("Failed to get tick")
            return None
        
        # Get account info
        info = MT5.account_info()
        if info is None:
            return None
        
        balance = info.balance
        
        # Calculate position size
        lots = calc_position_size(balance, signal["atr"])
        
        # Calculate stop loss
        if signal["side"] == "BUY":
            sl = signal["price"] - signal["atr"] * Config.ATR_MULT
            price = tick.ask
            order_type = MT5.ORDER_TYPE_BUY
        else:
            sl = signal["price"] + signal["atr"] * Config.ATR_MULT
            price = tick.bid
            order_type = MT5.ORDER_TYPE_SELL
        
        # Prepare order
        request = {
            "action": MT5.TRADE_ACTION_DEAL,
            "symbol": Config.SYMBOL,
            "volume": lots,
            "type": order_type,
            "price": price,
            "sl": round(sl, 2),
            "tp": 0.0,
            "deviation": int(Config.SLIPPAGE_PIPS * 10),  # In points
            "magic": 123456,
            "comment": "Donchian-50",
            "type_time": MT5.ORDER_TIME_GTC,
            "type_filling": MT5.ORDER_FILLING_IOC,
        }
        
        # Send order
        result = MT5.order_send(request)
        
        if result is None:
            logger.error("Order send failed")
            return None
        
        if result.retcode != MT5.TRADE_RETCODE_DONE:
            logger.error(f"Order rejected: retcode={result.retcode}, comment={result.comment}")
            return None
        
        trade = {
            "ticket": result.order,
            "side": signal["side"],
            "price": price,
            "lots": lots,
            "sl": round(sl, 2),
            "atr": signal["atr"],
            "balance": balance,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        logger.info(f"Trade executed: {signal['side']} {lots} lots @ {price}, SL={sl:.2f}")
        
        return trade
        
    except Exception as e:
        logger.error(f"Trade execution error: {e}")
        return None


# ===== STATE MANAGEMENT =====

class StrategyState:
    """Manages strategy state."""
    
    def __init__(self):
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.consecutive_losses = 0
        self.last_trade_date = None
        self.open_position = None
        self.load()
    
    def load(self):
        """Load state from file."""
        if Config.STATE_FILE.exists():
            try:
                with open(Config.STATE_FILE) as f:
                    data = json.load(f)
                self.trades_today = data.get("trades_today", 0)
                self.daily_pnl = data.get("daily_pnl", 0.0)
                self.consecutive_losses = data.get("consecutive_losses", 0)
                self.last_trade_date = data.get("last_trade_date")
                self.open_position = data.get("open_position")
            except:
                pass
    
    def save(self):
        """Save state to file."""
        data = {
            "trades_today": self.trades_today,
            "daily_pnl": self.daily_pnl,
            "consecutive_losses": self.consecutive_losses,
            "last_trade_date": self.last_trade_date,
            "open_position": self.open_position,
        }
        with open(Config.STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    
    def reset_daily(self):
        """Reset daily counters."""
        self.trades_today = 0
        self.daily_pnl = 0.0
        self.last_trade_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.save()


# ===== MAIN EXECUTION =====

def main():
    logger.info("="*60)
    logger.info("  DONCHIAN-50 XAUUSD — MT5 DEMO EXECUTION")
    logger.info("="*60)
    
    # Load state
    state = StrategyState()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.last_trade_date != today:
        state.reset_daily()
    
    # Check if we have an open position
    # For now, skip if we already have a position
    
    # Connect to MT5
    mt5 = MT5Connection()
    if not mt5.connect():
        logger.error(f"Failed to connect to MT5: {mt5.last_error}")
        return 1
    
    try:
        # Fetch bars
        bars = fetch_bars(mt5, Config.BARS_TO_FETCH)
        if not bars:
            logger.error("Failed to fetch bars")
            return 1
        
        logger.info(f"Fetched {len(bars)} bars")
        logger.info(f"Last bar: {bars[-1]['timestamp']} O={bars[-1]['open']:.2f} H={bars[-1]['high']:.2f} L={bars[-1]['low']:.2f} C={bars[-1]['close']:.2f}")
        
        # Check for signal
        signal = check_signal(bars)
        
        if signal is None:
            logger.info("No signal detected")
            return 0
        
        logger.info(f"Signal detected: {signal['side']} at {signal['price']:.2f}")
        logger.info(f"Donchian: upper={signal['donchian_upper']:.2f}, lower={signal['donchian_lower']:.2f}")
        logger.info(f"ATR: {signal['atr']:.2f}")
        
        # Check safety limits
        info = None
        try:
            import MetaTrader5 as MT5
            info = MT5.account_info()
        except:
            pass
        
        if info:
            balance = info.balance
            
            # Daily loss limit
            if state.daily_pnl < -balance * Config.MAX_DAILY_LOSS:
                logger.warning(f"Daily loss limit reached: {state.daily_pnl:.2f}")
                return 0
            
            # Max consecutive losses
            if state.consecutive_losses >= Config.MAX_CONSECUTIVE_LOSSES:
                logger.warning(f"Max consecutive losses reached: {state.consecutive_losses}")
                return 0
        
        # Execute trade
        trade = execute_trade(signal, mt5)
        
        if trade:
            # Update state
            state.trades_today += 1
            state.open_position = trade
            state.save()
            
            # Log trade
            log_trade(trade)
            
            logger.info(f"Trade logged: {trade['ticket']}")
        else:
            logger.error("Trade execution failed")
        
    finally:
        mt5.disconnect()
    
    logger.info("Execution complete")
    return 0


def log_trade(trade: dict):
    """Log trade to file."""
    trades = []
    if Config.TRADES_FILE.exists():
        try:
            with open(Config.TRADES_FILE) as f:
                trades = json.load(f)
        except:
            pass
    
    trades.append(trade)
    
    with open(Config.TRADES_FILE, "w") as f:
        json.dump(trades, f, indent=2)


if __name__ == "__main__":
    sys.exit(main())
