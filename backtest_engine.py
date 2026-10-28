import os
import pandas as pd
import numpy as np

# --- STRICT PARAMETER BOUNDARIES FOR THE 5K FUNDING PIPS ACCOUNT ---
INITIAL_BALANCE = 5000.00
TARGET_PROFIT = 1000.00
MAX_DAILY_LOSS_LIMIT = 150.00    # Hard daily kill-switch buffer ($50 room under the $200 limit)
MAX_STATIC_LOSS_LIMIT = 400.00   # Hard overall floor limit ($4,600)
BASE_RISK = 8.00                 # Scaled down to $8 to protect the daily circuit breaker
RISK_TO_REWARD = 4.0             # Structural 1:4 payout symmetry

def run_backtest(csv_path):
    if not os.path.exists(csv_path):
        print(f"Error: Target data file not found at {csv_path}")
        return

    print(f"🔄 Ingesting historical data bundle: {csv_path}...")
    df = pd.read_csv(csv_path)
    
    # --- FIXED ILOC INDEX SYNTAX BUG ---
    print("🔍 Parsing Unix 'time_epoch' column into Standardized Time Indices...")
    if df['time_epoch'].iloc[0] > 1e11:
        df['time_standardized'] = pd.to_datetime(df['time_epoch'], unit='ms')
    else:
        df['time_standardized'] = pd.to_datetime(df['time_epoch'], unit='s')
    df.set_index('time_standardized', inplace=True)
    
    ohlc_dict = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'tick_volume': 'sum'}
    df.columns = [c.lower() for c in df.columns]
    
    df_m15 = df.resample('15min').apply(ohlc_dict).dropna().copy()
    df_h1 = df.resample('1h').apply(ohlc_dict).dropna().copy()
    df_h1['h1_trend_ma'] = df_h1['close'].rolling(50).mean().shift(1)
    
    df_m15 = df_m15.join(df_h1['h1_trend_ma'], how='left')
    df_m15['h1_trend_ma'] = df_m15['h1_trend_ma'].ffill()
    
    df_m15['ist_time'] = df_m15.index.tz_localize('UTC').tz_convert('Asia/Kolkata')
    df_m15['time_str'] = df_m15['ist_time'].dt.strftime('%H:%M')
    df_m15['date'] = df_m15['ist_time'].dt.date
    
    high_low = df_m15['high'] - df_m15['low']
    high_close = np.abs(df_m15['high'] - df_m15['close'].shift())
    low_close = np.abs(df_m15['low'] - df_m15['close'].shift())
    df_m15['atr'] = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1).rolling(20).mean()
    
    df_m15['is_london'] = (df_m15['time_str'] >= '13:30') & (df_m15['time_str'] <= '15:30')
    london_data = df_m15[df_m15['is_london']]
    daily_highs = london_data.groupby('date')['high'].max()
    daily_lows = london_data.groupby('date')['low'].min()
    
    df_m15['london_high'] = df_m15['date'].map(daily_highs)
    df_m15['london_low'] = df_m15['date'].map(daily_lows)
    
    print("🏃 Running Prop-Compliant Daily-Reset Simulation...")
    balance = INITIAL_BALANCE
    peak_balance = INITIAL_BALANCE
    max_equity_drawdown = 0.0
    trade_logs = []
    active_trade = None
    traded_dates = set()
    
    # Violation tracking flags
    static_limit_breached = False
    daily_limit_breached = False
    target_achieved = False
    
    # Operational day states
    current_day_tracking = None
    day_start_balance = INITIAL_BALANCE
    day_losses = 0.0
    consecutive_losses = 0
    excel_rows = []
    
    for timestamp, row in df_m15.iterrows():
        current_date = row['date']
        
        # Monitor day boundaries to calculate daily drawdown resets
        if current_date != current_day_tracking:
            current_day_tracking = current_date
            day_start_balance = balance
            day_losses = 0.0
            
        if balance > peak_balance:
            peak_balance = balance
            
        # Overall Max Drawdown Tracking
        if (peak_balance - balance) > max_equity_drawdown:
            max_equity_drawdown = peak_balance - balance
            
        # Hard Rule Validation Checkpoints
        if (INITIAL_BALANCE - balance) >= MAX_STATIC_LOSS_LIMIT:
            static_limit_breached = True
            break
        if (day_start_balance - balance) >= MAX_DAILY_LOSS_LIMIT:
            daily_limit_breached = True
            break
        if (balance - INITIAL_BALANCE) >= TARGET_PROFIT:
            target_achieved = True
            break
            
        if pd.isna(row['london_high']) or pd.isna(row['london_low']) or pd.isna(row['atr']) or pd.isna(row['h1_trend_ma']):
            continue
            
        if active_trade is None:
            # Skip new trades if the day's safety allocation has already been drained
            if (day_start_balance - balance) >= (MAX_DAILY_LOSS_LIMIT - 40.00):
                continue
                
            if '18:30' <= row['time_str'] <= '20:30' and current_date not in traded_dates:
                if consecutive_losses == 0:
                    current_risk = BASE_RISK
                elif consecutive_losses == 1:
                    current_risk = BASE_RISK * 1.5
                elif consecutive_losses == 2:
                    current_risk = BASE_RISK * 2.5
                else:
                    current_risk = BASE_RISK * 3.5
                
                # Bearish Reversal Sweep
                if row['high'] > row['london_high'] and row['close'] < row['london_high'] and row['close'] < row['h1_trend_ma']:
                    sl_dist = row['atr'] * 1.6
                    active_trade = {
                        'id': len(trade_logs) + 1, 'type': 'SELL', 'entry_time': str(timestamp),
                        'entry': row['close'], 'sl': row['close'] + sl_dist, 
                        'tp': row['close'] - (sl_dist * RISK_TO_REWARD), 'risk': current_risk
                    }
                    traded_dates.add(current_date)
                    
                # Bullish Reversal Sweep
                elif row['low'] < row['london_low'] and row['close'] > row['london_low'] and row['close'] > row['h1_trend_ma']:
                    sl_dist = row['atr'] * 1.6
                    active_trade = {
                        'id': len(trade_logs) + 1, 'type': 'BUY', 'entry_time': str(timestamp),
                        'entry': row['close'], 'sl': row['close'] - sl_dist, 
                        'tp': row['close'] + (sl_dist * RISK_TO_REWARD), 'risk': current_risk
                    }
                    traded_dates.add(current_date)
        else:
            if active_trade['type'] == 'BUY':
                if row['low'] <= active_trade['sl']:
                    balance -= active_trade['risk']
                    excel_rows.append({
                        'Trade_ID': active_trade['id'], 'Type': 'BUY', 'Entry_Time': active_trade['entry_time'],
                        'Exit_Time': str(timestamp), 'Outcome': 'LOSS (SL)', 'Net_PnL': -active_trade['risk'], 'Account_Equity': balance
                    })
                    trade_logs.append(-active_trade['risk'])
                    consecutive_losses += 1
                    active_trade = None
                elif row['high'] >= active_trade['tp']:
                    pnl = active_trade['risk'] * RISK_TO_REWARD
                    balance += pnl
                    excel_rows.append({
                        'Trade_ID': active_trade['id'], 'Type': 'BUY', 'Entry_Time': active_trade['entry_time'],
                        'Exit_Time': str(timestamp), 'Outcome': 'WIN (TP)', 'Net_PnL': pnl, 'Account_Equity': balance
                    })
                    trade_logs.append(pnl)
                    consecutive_losses = 0
                    active_trade = None
                    
            elif active_trade['type'] == 'SELL':
                if row['high'] >= active_trade['sl']:
                    balance -= active_trade['risk']
                    excel_rows.append({
                        'Trade_ID': active_trade['id'], 'Type': 'SELL', 'Entry_Time': active_trade['entry_time'],
                        'Exit_Time': str(timestamp), 'Outcome': 'LOSS (SL)', 'Net_PnL': -active_trade['risk'], 'Account_Equity': balance
                    })
                    trade_logs.append(-active_trade['risk'])
                    consecutive_losses += 1
                    active_trade = None
                elif row['low'] <= active_trade['tp']:
                    pnl = active_trade['risk'] * RISK_TO_REWARD
                    balance += pnl
                    excel_rows.append({
                        'Trade_ID': active_trade['id'], 'Type': 'SELL', 'Entry_Time': active_trade['entry_time'],
                        'Exit_Time': str(timestamp), 'Outcome': 'WIN (TP)', 'Net_PnL': pnl, 'Account_Equity': balance
                    })
                    trade_logs.append(pnl)
                    consecutive_losses = 0
                    active_trade = None

    pnl_series = pd.Series(trade_logs)
    total_trades = len(trade_logs)
    wins = (pnl_series > 0).sum()
    losses = total_trades - wins
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0.0
    
    gross_profit = pnl_series[pnl_series > 0].sum()
    gross_loss = abs(pnl_series[pnl_series < 0].sum())
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else 99.9

    print("\n" + "="*55)
    print("🛡️ PROP-COMPLIANT FINISHED METRICS INDEX REPORT")
    print("="*55)
    print(f"• Total Executed Trades:    {total_trades}")
    if total_trades > 0:
        print(f"• Win / Loss Breakdown:     {wins} W / {losses} L")
        print(f"• Strategy Strike Win Rate: {win_rate:.2f}%")
        print(f"• System Profit Factor:     {profit_factor:.2f}")
    print(f"• Peak Historical Drawdown: ${max_equity_drawdown:.2f}")
    print(f"• Final Account Balance:    ${balance:.2f} (Net: ${balance - INITIAL_BALANCE:.2f})")
    print(f"• Evaluation Challenge Pass:{'YES 🏆' if target_achieved else 'NO'}")
    print(f"• Daily Reset Rule Breach:  {'YES 🛑 (FAILED)' if daily_limit_breached else 'NO  (Fully Safe)'}")
    print(f"• Static Limit Rule Breach: {'YES 🛑 (FAILED)' if static_limit_breached else 'NO  (Fully Safe)'}")
    print("=======================================================")

    log_df = pd.DataFrame(excel_rows)
    log_df.to_excel("complete_backtest_trade_logs.xlsx", index=False)

if __name__ == '__main__':
    target_csv = r"C:\Users\<USER>\Desktop\quant_lab\research_bundle\mt5_export\XAUUSD_M5_20231022_20261022_UTC.csv"
    run_backtest(target_csv)
