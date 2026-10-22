"""Decompose realized trade costs for a saved historical V3 run.

This is BACKTEST evidence only. It does not infer missing tick-level costs;
the report explicitly labels spread/slippage limitations.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import pandas as pd


def summarize(trades: pd.DataFrame, bars: pd.DataFrame) -> dict[str, object]:
    if trades.empty:
        return {"trades": 0}
    gross_r = trades["gross_pnl"] / trades["risk_budget"].replace(0, pd.NA)
    net_r = trades["r"]
    cost_r = gross_r - net_r
    wins = net_r[net_r > 0]
    losses = net_r[net_r < 0]
    gross_wins = gross_r[gross_r > 0].sum()
    gross_losses = abs(gross_r[gross_r < 0].sum())
    net_wins = wins.sum()
    net_losses = abs(losses.sum())
    avg_stop = (trades["entry"] - trades["sl"]).abs().mean()
    avg_spread = float(bars["spread"].mean()) if "spread" in bars else float("nan")
    avg_spread_price = avg_spread * 0.01
    return {
        "trades": int(len(trades)),
        "mean_gross_R": float(gross_r.mean()),
        "mean_cost_R": float(cost_r.mean()),
        "mean_net_R": float(net_r.mean()),
        "win_rate": float((net_r > 0).mean()),
        "average_win_R": float(wins.mean()) if len(wins) else 0.0,
        "average_loss_R": float(losses.mean()) if len(losses) else 0.0,
        "gross_profit_factor": float(gross_wins / gross_losses) if gross_losses else float("inf"),
        "net_profit_factor": float(net_wins / net_losses) if net_losses else float("inf"),
        "average_spread_fraction_of_stop": float(avg_spread_price / avg_stop) if avg_stop else float("nan"),
        "break_even_win_rate": float(abs(losses.mean()) / (wins.mean() + abs(losses.mean())))
        if len(wins) and len(losses)
        else float("nan"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    bars = pd.read_parquet(args.input)
    rows: list[dict[str, object]] = []
    for report_path in sorted(args.reports.glob("*_research_report.json")):
        report = json.loads(report_path.read_text(encoding="utf-8"))
        name = str(report["strategy"])
        trade_path = args.reports / f"{name}_research_trades.csv"
        if not trade_path.exists():
            continue
        row = {"strategy": name, "data_hash": report.get("data_hash"), "code_version": report.get("code_version")}
        row.update(summarize(pd.read_csv(trade_path), bars))
        rows.append(row)
    args.outdir.mkdir(parents=True, exist_ok=True)
    data_hash = rows[0]["data_hash"] if rows else "unknown"
    out = args.outdir / f"cost_decomposition_{data_hash}_{str(rows[0].get('code_version', 'unknown'))[:12]}.csv"
    if rows:
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    fields = list(rows[0]) if rows else ["strategy", "trades"]
    print(" | ".join(fields))
    for row in rows:
        print(" | ".join(str(row.get(field, "")) for field in fields))
    print(f"saved={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
