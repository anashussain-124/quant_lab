"""Integrated, fail-closed FundingPips/XAUUSD research runner.

This command coordinates existing v4 audit and cost-decomposition interfaces.
It does not read the final holdout, place orders, or convert R into dollars
without verified XAUUSD contract specifications and position sizing.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import os
import platform
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from data.holdout import ResearchDataAccessor
from data.v4 import audit_ohlcv, sha256_file
from hypotheses.registry import hypothesis_registry
from risk.profile_loader import load_profile
from scripts.cost_decomposition import _params, metrics_for_trades, trades_for_template


STANDARD_URL = "https://help.fundingpips.com/hc/en-us/articles/34501809112081-2-Step-Standard"
PRO_URL = "https://help.fundingpips.com/hc/en-us/articles/34502027344017-2-Step-Pro-Model"
OBJECTIVES_URL = "https://fundingpips.com/trading-objectives"
VERIFIED_DATE = "2026-10-22"


@dataclass(frozen=True)
class FundingPipsProfile:
    name: str
    daily_loss: float
    max_loss: float
    phase1_target: float
    phase2_target: float
    min_trading_days: int
    daily_loss_addon: bool
    source_url: str
    verification_date: str
    evaluation_rules: str
    master_rules: str


PROFILES: dict[str, FundingPipsProfile] = {
    "standard-5": FundingPipsProfile(
        "standard-5", 0.05, 0.10, 0.08, 0.05, 3, False, STANDARD_URL,
        VERIFIED_DATE, "2-Step Standard evaluation", "Master restrictions differ; overnight/weekend holding requires applicable add-on"),
    "standard-3": FundingPipsProfile(
        "standard-3", 0.03, 0.10, 0.08, 0.05, 0, True, STANDARD_URL,
        VERIFIED_DATE, "2-Step Standard evaluation with 3% daily-loss add-on", "Master restrictions differ; overnight/weekend holding requires applicable add-on"),
    "pro": FundingPipsProfile(
        "pro", 0.03, 0.06, 0.06, 0.06, 0, False, PRO_URL,
        VERIFIED_DATE, "2-Step Pro evaluation", "Master restrictions differ; overnight/weekend holding requires applicable add-on"),
}


def _default_input() -> Path:
    candidates = sorted((ROOT / "data" / "store" / "v4").glob("mt5_XAUUSD_M5_*.parquet"))
    if not candidates:
        return ROOT / "data/store/v4/mt5_XAUUSD_M5_a052314f19605be8.parquet"
    return candidates[-1]


def _run_dir(output_root: Path) -> Path:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = output_root / run_id
    suffix = 1
    while path.exists():
        path = output_root / f"{run_id}_{suffix}"
        suffix += 1
    path.mkdir(parents=True, exist_ok=False)
    return path


def _load_training(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"permitted input dataset not found: {path}")
    frame = pd.read_parquet(path)
    if "timestamp" in frame.columns:
        frame = frame.set_index("timestamp")
    frame = frame.sort_index()
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("input must have a timezone-aware timestamp index")
    audit = audit_ohlcv(frame, "5min")
    training = ResearchDataAccessor(frame).training_data()
    metadata = {
        "path": str(path), "sha256": sha256_file(path), "rows_full": len(frame),
        "rows_pre_holdout": len(training), "first_utc": frame.index[0].isoformat(),
        "last_utc": frame.index[-1].isoformat(), "pre_holdout_last_utc": training.index[-1].isoformat(),
        "audit": audit, "holdout_accessed": False,
    }
    return training, metadata


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _profiles(selected: str) -> list[FundingPipsProfile]:
    if selected == "compare":
        return list(PROFILES.values())
    if selected not in PROFILES:
        raise ValueError(f"unknown profile: {selected}")
    return [PROFILES[selected]]


def _preflight(input_path: Path, output_root: Path) -> dict[str, Any]:
    required = ["pandas", "numpy", "scipy", "pyarrow"]
    dependencies: dict[str, str] = {}
    for name in required:
        try:
            module = importlib.import_module(name)
            dependencies[name] = str(getattr(module, "__version__", "available"))
        except Exception as exc:
            dependencies[name] = f"MISSING: {exc}"
    profile_files = {p.name: load_profile(p) for p in sorted((ROOT / "risk/profiles").glob("*.yaml"))}
    training, data = _load_training(input_path)
    output_root.mkdir(parents=True, exist_ok=True)
    probe = output_root / ".write_probe"
    probe.write_text("preflight", encoding="utf-8")
    probe.unlink()
    return {
        "python": platform.python_version(), "executable": sys.executable,
        "dependencies": dependencies, "input": data, "output_root": str(output_root),
        "output_writable": True, "profiles_loaded": sorted(profile_files),
        "interfaces": {"cost_decomposition": callable(trades_for_template),
                       "holdout_reader_not_called": True, "five_templates": len(hypothesis_registry()) == 5},
        "training_rows_checked": len(training),
    }


def _baseline(training: pd.DataFrame, input_path: Path, run_dir: Path,
              registry: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    metric_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    for spec in hypothesis_registry():
        default, _ = _params(spec, registry)
        plan, trades = trades_for_template(training, spec, default)
        spread_price = float(training["spread"].mean()) * 0.01 if "spread" in training else 0.01
        spread_bps = max(0.01, spread_price / float(training["close"].mean()) * 10000.0)
        metric_rows.append(metrics_for_trades(training, spec, default, "default", plan, trades,
                                               spread_bps, spread_price))
        for number, trade in enumerate(trades):
            trade_rows.append({"strategy": spec.name, "trade_number": number,
                               "signal_index": trade.signal_index, "entry_index": trade.entry_index,
                               "exit_index": trade.exit_index, "side": trade.side,
                               "gross_pnl_model_units": str(trade.gross_pnl),
                               "costs_model_units": str(trade.costs),
                               "net_pnl_model_units": str(trade.net_pnl),
                               "exit_reason": trade.exit_reason,
                               "account_currency_pnl": "UNAVAILABLE_NO_CONTRACT_SIZING"})
    _write_csv(run_dir / "strategy_metrics.csv", metric_rows)
    _write_csv(run_dir / "trade_log.csv", trade_rows)
    return metric_rows, trade_rows


def _risk_rows(profiles: list[FundingPipsProfile], account_size: float,
               risk_per_trade: float, trade_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for profile in profiles:
        rows.append({
            "profile": profile.name, "account_size_usd": account_size,
            "risk_per_trade_assumption": risk_per_trade,
            "risk_dollars_assumption": account_size * risk_per_trade,
            "daily_loss_limit_usd": account_size * profile.daily_loss,
            "static_max_loss_usd": account_size * profile.max_loss,
            "phase1_target_usd": account_size * profile.phase1_target,
            "phase2_target_usd": account_size * profile.phase2_target,
            "min_trading_days": profile.min_trading_days,
            "closed_trade_breaches": "NOT_CERTIFIED",
            "floating_equity_breaches": "INCOMPLETE",
            "challenge_result": "INCOMPLETE",
            "reason": "XAUUSD contract size, tick value, lot sizing, and floating equity are unavailable; R cannot be certified as account-currency P&L.",
            "source_url": profile.source_url, "verification_date": profile.verification_date,
            "evaluation_rules": profile.evaluation_rules, "master_rules": profile.master_rules,
            "trade_rows_available": len(trade_rows),
        })
    return rows


def _compare_existing(input_path: Path, metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare fixed-default metrics with the existing cost report."""
    digest = hashlib.sha256(input_path.read_bytes()).hexdigest()[:16]
    existing = ROOT / "reports_v4" / "costs" / f"cost_decomposition_{digest}.csv"
    if not existing.exists():
        return {"status": "UNAVAILABLE", "path": str(existing),
                "reason": "matching report not found"}
    prior = pd.read_csv(existing)
    prior = prior.loc[prior["parameter_set"] == "default"].set_index("hypothesis")
    differences: list[dict[str, Any]] = []
    for row in metrics:
        name = str(row["hypothesis"])
        if name not in prior.index:
            differences.append({"hypothesis": name, "reason": "missing from prior report"})
            continue
        for column in ("mean_gross_R", "mean_cost_R", "mean_net_R", "trades"):
            old = float(prior.loc[name, column])
            new = float(row[column])
            if abs(old - new) > 1e-10:
                differences.append({"hypothesis": name, "metric": column, "old": old, "new": new})
    return {"status": "MATCH" if not differences else "MISMATCH", "path": str(existing),
            "differences": differences}


def _write_report(run_dir: Path, manifest: dict[str, Any], metrics: list[dict[str, Any]],
                  risk_rows: list[dict[str, Any]], mode: str) -> None:
    lines = ["FUNDINGPIPS XAUUSD INTEGRATED RUNNER", f"mode={mode}",
             f"run_dir={run_dir}", f"branch={manifest['git_branch']}",
             f"revision={manifest['git_revision']}",
             f"dataset={manifest['dataset']['path']}",
             f"period={manifest['dataset']['first_utc']} .. {manifest['dataset']['pre_holdout_last_utc']}",
             f"pre_holdout_rows={manifest['dataset']['rows_pre_holdout']}",
             "final_holdout_accessed=False",
             f"existing_cost_report_comparison={manifest.get('existing_cost_report_comparison', {}).get('status', 'NOT_RUN')}", ""]
    lines += ["STRATEGY BASELINE"]
    for row in metrics:
        lines.append(f"{row['hypothesis']}: trades={row['trades']} gross_R={row['mean_gross_R']:.6f} cost_R={row['mean_cost_R']:.6f} net_R={row['mean_net_R']:.6f}")
    lines += ["", f"account_size_usd={manifest['account_size_usd']}",
              f"risk_per_trade={manifest['risk_per_trade']} (RESEARCH ASSUMPTION ONLY)", "",
              "PROFILE RISK"]
    for row in risk_rows:
        lines.append(f"{row['profile']}: target={row['phase1_target_usd']:.2f}/{row['phase2_target_usd']:.2f} daily_loss={row['daily_loss_limit_usd']:.2f} max_loss={row['static_max_loss_usd']:.2f} result=INCOMPLETE")
    lines += ["", "LIMITATIONS", "- Account-currency P&L is not certified.",
              "- Floating losses cannot be reconstructed from the current trade ledger.",
              "- No live or demo orders were submitted.",
              "- Results are historical BACKTEST/SIMULATED outputs only."]
    (run_dir / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(mode: str, input_path: Path, output_root: Path, profile: str,
        account_size: float, risk_per_trade: float, registry: Path) -> Path:
    if mode not in {"preflight", "smoke", "baseline"}:
        raise ValueError(f"unknown mode: {mode}")
    if account_size <= 0 or not 0 < risk_per_trade < 1:
        raise ValueError("account size must be positive and risk per trade must be in (0, 1)")
    run_dir = _run_dir(output_root)
    preflight = _preflight(input_path, output_root)
    manifest: dict[str, Any] = {
        "run_id": run_dir.name, "created_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode, "git_branch": "unknown", "git_revision": "unknown",
        "account_size_usd": account_size, "risk_per_trade": risk_per_trade,
        "profile_selection": profile, "official_sources": [STANDARD_URL, PRO_URL, OBJECTIVES_URL],
        "dataset": preflight["input"], "preflight": preflight,
        "execution_assumptions": ["next-bar-open causal fills", "observed spread model", "no contract sizing", "no floating-equity reconstruction"],
    }
    try:
        import subprocess
        manifest["git_branch"] = subprocess.check_output(["git", "branch", "--show-current"], cwd=ROOT, text=True).strip()
        manifest["git_revision"] = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception as exc:
        manifest["git_error"] = str(exc)
    if mode == "preflight":
        (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
        print(json.dumps(manifest, indent=2, default=str))
        print(f"reports={run_dir}")
        return run_dir
    training, _ = _load_training(input_path)
    if mode == "smoke":
        training = training.iloc[:5000].copy()
    metrics, trades = _baseline(training, input_path, run_dir, registry)
    comparison = ({"status": "NOT_COMPARABLE_SMOKE",
                   "reason": "smoke intentionally uses the first 5,000 pre-holdout rows"}
                  if mode == "smoke" else _compare_existing(input_path, metrics))
    risk_rows = _risk_rows(_profiles(profile), account_size, risk_per_trade, trades)
    _write_csv(run_dir / "profile_risk_summary.csv", risk_rows)
    manifest["strategy_count"] = len(metrics)
    manifest["trade_count"] = len(trades)
    manifest["risk_certification"] = "INCOMPLETE"
    manifest["existing_cost_report_comparison"] = comparison
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    _write_report(run_dir, manifest, metrics, risk_rows, mode)
    print((run_dir / "report.txt").read_text(encoding="utf-8"))
    print(f"manifest={run_dir / 'manifest.json'}")
    print(f"strategy_metrics={run_dir / 'strategy_metrics.csv'}")
    print(f"trade_log={run_dir / 'trade_log.csv'}")
    print(f"profile_risk={run_dir / 'profile_risk_summary.csv'}")
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preflight", "smoke", "baseline"))
    parser.add_argument("--input", type=Path, default=_default_input())
    parser.add_argument("--output-root", type=Path, default=ROOT / "reports_v4/fundingpips")
    parser.add_argument("--profile", choices=("standard-5", "standard-3", "pro", "compare"), default="compare")
    parser.add_argument("--account-size", type=float, default=5000.0)
    parser.add_argument("--risk-per-trade", type=float, default=0.0025)
    parser.add_argument("--registry", type=Path, default=ROOT / "experiments/registry.jsonl")
    args = parser.parse_args()
    run(args.mode, args.input, args.output_root, args.profile, args.account_size, args.risk_per_trade, args.registry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
