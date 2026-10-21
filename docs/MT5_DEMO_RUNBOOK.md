# MT5 Demo Trading Algorithm — Operational Runbook

## 1. System Overview

This runbook covers setup, configuration, monitoring, and operational procedures for running the **Unified Donchian-50 XAUUSD Demo Trading Algorithm** (`mt5_quant_algo.py`) with FundingPips compliance enforcement.

> [!WARNING]
> **DEMO ONLY**: Per project safety rules (`AGENTS.md` A2), this system only connects to MetaTrader 5 demo accounts. Any attempt to use live credentials or live trading modes is automatically rejected and halts the system.

---

## 2. Environment & Prerequisites

### 2.1 Hardware and OS
- **Operating System**: Windows 10/11 or Windows Server (MT5 Python API is Windows-only).
- **Python**: Python 3.12+ (64-bit).
- **Virtual Environment**: Located at `C:\Users\<USER>\quant-lab-venv`.

### 2.2 MetaTrader 5 Setup
1. Download and install MetaTrader 5 from your demo broker or [MetaQuotes](https://www.metatrader5.com/).
2. Open a **Demo Account** (e.g. MetaQuotes-Demo or FundingPips Demo server).
3. Ensure **Algo Trading** is enabled in MT5:
   - Navigate to: `Tools -> Options -> Expert Advisors`.
   - Check `Allow algorithmic trading`.
4. Ensure the terminal is running and logged into your demo account before launching the live demo loop.

---

## 3. Configuration

### 3.1 FundingPips Challenge Settings (`configs/fundingpips.yaml`)
Edit `configs/fundingpips.yaml` to specify your challenge profile and risk preferences:
```yaml
prop_firm:
  profile_id: "fundingpips_2step_standard_5pct_p1"
  safety_buffer_pct: 0.005 # 0.5% buffer ($50 on $10k)
  state_file: "data/compliance_state.json"
  warn_threshold_pct: 0.70

research:
  donchian_period: 50
  atr_period: 14
  min_atr_usd: 8.0
  session_start_utc: "08:00"
  session_end_utc: "16:00"
```

### 3.2 Environment Labels (`configs/config.yaml`)
Ensure environment is set to `SIMULATED` or `MT5_DEMO`:
```yaml
environment: "SIMULATED"
```

---

## 4. Operational CLI Commands

Activate the virtual environment:
```powershell
& "C:\Users\<USER>\quant-lab-venv\Scripts\Activate.ps1"
```

### 4.1 System & Compliance Status
Check connection health, demo mode validation, account balance, and FundingPips loss limits:
```powershell
python mt5_quant_algo.py status
```
*Expected Output:*
```text
=================================================================
 QUANT RESEARCH LAB — MT5 DEMO ALGORITHM STATUS
=================================================================
Safety Check       : PASS (DEMO verified (Account: 5056172579, Server: MetaQuotes-Demo))
Environment        : SIMULATED
Strategy           : Donchian-50 Breakout (XAUUSD H1)
Prop Firm Model    : FundingPips 2-Step Standard (5% daily) – Phase 1
Rule Status        : EMPIRICAL
Compliance State   : COMPLIANT
-----------------------------------------------------------------
Current Balance    : $10,000.00
Total Realized P&L : $0.00
Daily Room Left    : $500.00 / $500.00
Total Room Left    : $1,000.00 / $1,000.00
Trading Days Met   : 0 (Min required: 3)
=================================================================
```

### 4.2 Historical Backtest
Run backtest on historical H1 data with broker-accurate contract sizing and daily loss simulation:
```powershell
# Default 0.25% risk per trade
python mt5_quant_algo.py backtest

# Test with 0.50% risk per trade
python mt5_quant_algo.py backtest --risk 0.005
```

### 4.3 Walk-Forward Validation
Execute rolling walk-forward test windows:
```powershell
python mt5_quant_algo.py validate
```

### 4.4 Single Evaluation Cycle (Test / Cron)
Execute a single cycle on closed candles without entering an infinite loop:
```powershell
python mt5_quant_algo.py run-demo --once
```

### 4.5 Continuous Demo Loop
Run the live polling loop (evaluates every 60 seconds):
```powershell
python mt5_quant_algo.py run-demo --interval 60
```
*Press Ctrl+C to initiate graceful shutdown.*

### 4.6 Compliance Report
Generate full JSON compliance audit report:
```powershell
python mt5_quant_algo.py report
```

---

## 5. Automated Scheduling (Windows Task Scheduler)

To run hourly at the top of the hour during the London trading session (08:00–16:00 UTC):

1. Open **Task Scheduler** in Windows.
2. Create Task: `QuantLab_MT5_Hourly`.
3. Trigger: Hourly at `:01` (to ensure the prior H1 bar is closed).
4. Action: Start a program:
   - **Program/script**: `C:\Users\<USER>\quant-lab-venv\Scripts\python.exe`
   - **Arguments**: `mt5_quant_algo.py run-demo --once`
   - **Start in**: `c:\Users\<USER>\Desktop\donchian_model`

---

## 6. Logs & Monitoring

- **Structured Logs**: Written to standard output and `logs/mt5_donchian.log`.
- **Compliance State**: Persisted in `data/compliance_state.json`.
- **Algo State (Duplicate Prevention)**: Persisted in `data/algo_state.json`.

Key log events to inspect:
- `EXEC_ORDER_SUBMITTED`: Order passed safety gate and was submitted.
- `SAFETY_GATE_REJECTED`: Order vetoed by pre-trade checklist.
- `COMPLIANCE_REJECTED`: Order vetoed by FundingPips risk limit.
- `DEMO_CHECK_FAILED`: Account is not demo mode — critical security event.

---

## 7. Emergency Procedures

### 7.1 Emergency Stop / Kill Switch
If unexpected behavior occurs, immediately halt the system:
1. Kill the python process (`Ctrl+C` in console or Task Manager).
2. To prevent subsequent cron runs from placing orders, manually set the state to HALTED:
   ```powershell
   python -c "from engine.prop_firm_compliance import PropFirmComplianceEngine, get_profile; p = get_profile('fundingpips_2step_standard_5pct_p1'); eng = PropFirmComplianceEngine(p, 'data/compliance_state.json'); eng.halt('Operator Emergency Stop')"
   ```
3. Close open positions directly in the MetaTrader 5 terminal:
   - In MT5, press `Ctrl+T` to open the Toolbox.
   - Right-click any open position -> `Close Position`.
