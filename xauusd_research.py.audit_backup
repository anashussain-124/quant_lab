import csv, json, math, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from collections import defaultdict

INITIAL = 5000.0
RISK_FRACTION = 0.0025
MAX_RISK = 12.50
RR = 4.0
ATR_N = 20
EMA_N = 50
STOP_ATR = 1.6
SPREAD = 0.31
SLIPPAGE = 0.10
COMMISSION = 5.0
CONTRACT = 100.0
DAILY_LIMIT = 150.0
OVERALL_LIMIT = 500.0
TARGET = 400.0
IST = timezone(timedelta(hours=5, minutes=30))
UTC3 = timezone(timedelta(hours=3))

def stamp(row):
    for key in ("time_utc", "time", "datetime", "time_epoch", "timestamp"):
        if key not in row or not row[key]:
            continue
        value = row[key].strip()
        if key in ("time_epoch", "timestamp") or value.replace(".", "", 1).isdigit():
            number = float(value)
            return datetime.fromtimestamp(
                number / 1000 if number > 1e11 else number, timezone.utc
            )
        value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    raise ValueError("No supported timestamp column found")

def load(path):
    bars = {}
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header")
        reader.fieldnames = [x.strip().lower() for x in reader.fieldnames]
        for raw in reader:
            r = {str(k).strip().lower(): v for k, v in raw.items() if k}
            try:
                t = stamp(r)
                o, h, l, c = (float(r[k]) for k in ("open","high","low","close"))
            except (ValueError, TypeError, KeyError):
                continue
            if min(o,h,l,c) <= 0 or h < max(o,c,l) or l > min(o,c,h):
                raise ValueError("Invalid OHLC data at " + str(t))
            # Resample using UTC clock boundaries, 5-minute source bars.
            key = int(t.timestamp()) // 300 * 300
            t = datetime.fromtimestamp(key, timezone.utc)
            if t not in bars:
                bars[t] = [o,h,l,c,1]
            else:
                b = bars[t]
                b[1], b[2], b[3], b[4] = max(b[1],h), min(b[2],l), c, b[4]+1
    out = []
    for t, (o,h,l,c,n) in sorted(bars.items()):
        out.append({"t":t,"o":o,"h":h,"l":l,"c":c,"n":n})
    if not out:
        raise ValueError("No valid bars loaded. Check CSV columns and path.")
    return out

def aggregate(bars, seconds):
    result = {}
    for b in bars:
        epoch = int(b["t"].timestamp())
        key = datetime.fromtimestamp(epoch // seconds * seconds, timezone.utc)
        if key not in result:
            result[key] = {"t":key,"o":b["o"],"h":b["h"],"l":b["l"],"c":b["c"],"n":1}
        else:
            x=result[key]
            x["h"]=max(x["h"],b["h"])
            x["l"]=min(x["l"],b["l"])
            x["c"]=b["c"]
            x["n"]+=1
    # Discard incomplete M15/H1 candles.
    return [x for x in result.values() if x["n"] == seconds // 300]

def ema(values, period):
    out, alpha, current = [None]*len(values), 2/(period+1), None
    for i, value in enumerate(values):
        current = value if current is None else alpha*value+(1-alpha)*current
        if i >= period-1:
            out[i] = current
    return out

def indicators(bars):
    tr=[]
    for i,b in enumerate(bars):
        prev=bars[i-1]["c"] if i else b["c"]
        tr.append(max(b["h"]-b["l"],abs(b["h"]-prev),abs(b["l"]-prev)))
    atr=[None]*len(bars)
    for i in range(ATR_N-1,len(bars)):
        atr[i]=sum(tr[i-ATR_N+1:i+1])/ATR_N
    return atr

def main():
    if len(sys.argv)<2:
        print('Usage: python xauusd_research.py "PATH_TO_M5_CSV"')
        return
    bars5=load(sys.argv[1])
    bars15=aggregate(bars5,900)
    bars60=aggregate(bars5,3600)
    if len(bars15)<300 or len(bars60)<EMA_N+2:
        raise ValueError("Insufficient complete M15/H1 bars for this test.")

    h1ema=ema([b["c"] for b in bars60],EMA_N)
    # Map only the last completed H1 EMA to each M15 bar.
    h1map={}
    for i,b in enumerate(bars60):
        if i>0 and h1ema[i-1] is not None:
            h1map[b["t"]]=h1ema[i-1]
    h1keys=sorted(h1map)
    atr=indicators(bars15)

    def completed_ema(t):
        eligible=[x for x in h1keys if x<=t]
        return h1map[eligible[-1]] if eligible else None

    # Range is only available after 15:30 IST; eight complete M15 bars required.
    sessions=defaultdict(list)
    for i,b in enumerate(bars15):
        local=b["t"].astimezone(IST)
        if "13:30" <= local.strftime("%H:%M") < "15:30":
            sessions[local.date()].append(i)
    ranges={}
    for day, ids in sessions.items():
        if len(ids)==8:
            ranges[day]=(max(bars15[i]["h"] for i in ids),
                         min(bars15[i]["l"] for i in ids))

    balance=INITIAL
    daily_start={}
    trades=[]
    active=None
    used=set()
    peak=INITIAL
    maxdd=0.0
    halted_days=set()
    stopped=False
    i=0

    while i<len(bars15):
        b=bars15[i]
        local=b["t"].astimezone(IST)
        propday=b["t"].astimezone(UTC3).date()
        if propday not in daily_start:
            daily_start[propday]=balance

        # Manage open trade with conservative stop-first tie handling.
        if active:
            reason=None
            exitref=None
            if active["side"]=="BUY":
                if b["l"]<=active["sl"]:
                    reason,exitref="SL",active["sl"]
                elif b["h"]>=active["tp"]:
                    reason,exitref="TP",active["tp"]
            else:
                if b["h"]>=active["sl"]:
                    reason,exitref="SL",active["sl"]
                elif b["l"]<=active["tp"]:
                    reason,exitref="TP",active["tp"]
            if reason is None and local.strftime("%H:%M")>="21:30":
                reason,exitref="TIME",b["c"]
            if reason:
                side=active["side"]
                exitpx=exitref-(SPREAD/2+SLIPPAGE) if side=="BUY" else exitref+(SPREAD/2+SLIPPAGE)
                gross=((exitpx-active["entry"]) if side=="BUY" else (active["entry"]-exitpx))*CONTRACT*active["lots"]
                fees=COMMISSION*active["lots"]/2
                net=gross-fees
                balance+=gross-fees
                active["net"]=net
                active["exit_time"]=b["t"].isoformat()
                active["exit_reason"]=reason
                active["gross"]=gross
                active["fees"]+=fees
                active["balance"]=balance
                trades.append(active)
                active=None

        peak=max(peak,balance)
        maxdd=max(maxdd,peak-balance)
        equity=balance
        if active:
            mark=b["c"]-(SPREAD/2) if active["side"]=="BUY" else b["c"]+(SPREAD/2)
            equity += ((mark-active["entry"]) if active["side"]=="BUY" else (active["entry"]-mark))*CONTRACT*active["lots"]
        if daily_start[propday]-equity>=DAILY_LIMIT:
            halted_days.add(propday)
        if INITIAL-equity>=OVERALL_LIMIT:
            stopped=True
        if balance-INITIAL>=TARGET:
            stopped=True
        if stopped:
            break

        # Signals on closed M15 candles; entry on next M15 open.
        day=local.date()
        tm=local.strftime("%H:%M")
        if (active is None and day in ranges and day not in used
            and day not in halted_days and "18:30"<=tm<="20:15"
            and i+1<len(bars15) and atr[i] is not None):
            rh,rl=ranges[day]
            trend=completed_ema(b["t"])
            side=None
            if trend is not None:
                if b["h"]>rh and b["c"]<rh and b["c"]<trend:
                    side="SELL"
                elif b["l"]<rl and b["c"]>rl and b["c"]>trend:
                    side="BUY"
            nxt=bars15[i+1]
            nxtlocal=nxt["t"].astimezone(IST)
            if side and nxtlocal.date()==day and nxtlocal.strftime("%H:%M")<="20:30":
                dist=STOP_ATR*atr[i]
                risk=min(balance*RISK_FRACTION,MAX_RISK)
                lots=math.floor((risk/(dist*CONTRACT+COMMISSION))*100)/100 if dist>0 else 0
                if lots>=0.01:
                    ref=nxt["o"]
                    entry=ref+(SPREAD/2+SLIPPAGE) if side=="BUY" else ref-(SPREAD/2+SLIPPAGE)
                    sl=ref-dist if side=="BUY" else ref+dist
                    tp=ref+dist*RR if side=="BUY" else ref-dist*RR
                    active={"side":side,"signal_time":b["t"].isoformat(),
                        "entry_time":nxt["t"].isoformat(),"entry_ref":ref,
                        "entry":entry,"sl":sl,"tp":tp,"lots":lots,
                        "risk_budget":risk,"fees":COMMISSION*lots/2}
                    balance-=COMMISSION*lots/2
                    used.add(day)
        i+=1

    # Mark open position at end rather than silently dropping it.
    if active:
        b=bars15[-1]
        side=active["side"]
        exitpx=b["c"]-(SPREAD/2+SLIPPAGE) if side=="BUY" else b["c"]+(SPREAD/2+SLIPPAGE)
        gross=((exitpx-active["entry"]) if side=="BUY" else (active["entry"]-exitpx))*CONTRACT*active["lots"]
        fees=COMMISSION*active["lots"]/2
        balance+=gross-fees
        active.update(exit_time=b["t"].isoformat(),exit_reason="END_DATA",
                      gross=gross,fees=active["fees"]+fees,net=gross-active["fees"]-fees,balance=balance)
        trades.append(active)

    wins=[x for x in trades if x.get("net",0)>0]
    losses=[x for x in trades if x.get("net",0)<0]
    gp=sum(x["net"] for x in wins)
    gl=abs(sum(x["net"] for x in losses))
    pf=gp/gl if gl else (float("inf") if gp else 0)
    report={
        "input_m5_bars":len(bars5),"complete_m15_bars":len(bars15),
        "start_utc":bars15[0]["t"].isoformat(),"end_utc":bars15[-1]["t"].isoformat(),
        "trades":len(trades),"wins":len(wins),"losses":len(losses),
        "win_rate_pct":round(100*len(wins)/len(trades),2) if trades else 0,
        "profit_factor":round(pf,4) if math.isfinite(pf) else "infinity",
        "net_pnl_usd":round(balance-INITIAL,2),"final_balance_usd":round(balance,2),
        "max_balance_drawdown_usd":round(maxdd,2),
        "phase1_target_hit":balance-INITIAL>=TARGET,
        "assumptions":{"risk_fraction":RISK_FRACTION,"max_risk_usd":MAX_RISK,
          "risk_reward":RR,"spread_price_units":SPREAD,"slippage_per_side":SLIPPAGE,
          "commission_round_turn_per_lot_usd":COMMISSION,
          "daily_loss_assumption_usd":DAILY_LIMIT,"overall_loss_assumption_usd":OVERALL_LIMIT}
    }
    out=Path("xauusd_results")
    out.mkdir(exist_ok=True)
    with open(out/"trades.csv","w",newline="",encoding="utf-8") as f:
        fields=["signal_time","entry_time","exit_time","side","entry_ref","entry","sl","tp",
                "exit_reason","lots","risk_budget","gross","fees","net","balance"]
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore")
        w.writeheader()
        for t in trades:
            w.writerow(t)
    with open(out/"summary.json","w",encoding="utf-8") as f:
        json.dump(report,f,indent=2)
    print("\n=== XAUUSD RESEARCH BACKTEST (NOT VERIFIED) ===")
    for k,v in report.items():
        print(f"{k}: {v}")
    print("\nFiles saved to:",out.resolve())
    print("Research only. OHLC fills, historical spread and prop-rule compliance remain unverified.")

if __name__=="__main__":
    main()
