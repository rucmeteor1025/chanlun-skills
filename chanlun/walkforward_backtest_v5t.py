#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v5t 买卖点严格 walk-forward 回测

规则：
1. 每个交易日只用截至当日收盘的数据重新运行分析；
2. 仅接收当日 newly actionable 的信号；
3. 以次日开盘价作为进场价；
4. 默认统计 10/20 日收益，以及 20 日内止盈/止损先后。
"""
import argparse
import os
import sys
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../../.."))
TECHNICAL_DIR = os.path.join(PROJECT_ROOT, "投研系统/工具脚本/技术")
DATA_DIR = os.path.join(PROJECT_ROOT, "投研系统/工具脚本/数据")
sys.path.insert(0, TECHNICAL_DIR)
sys.path.insert(0, DATA_DIR)

from chan_core_v5t import ChanCoreV5T
from local_market_data import DEFAULT_LOCAL_MARKET_DB_DIR


DATA_PATH = os.path.join(DEFAULT_LOCAL_MARKET_DB_DIR, "stock_daily.parquet")
DEFAULT_OUTPUT = os.path.join(CURRENT_DIR, "输出", "walkforward_backtest_v5t_summary.csv")
DEFAULT_DETAILS = os.path.join(CURRENT_DIR, "输出", "walkforward_backtest_v5t_details.csv")

THEMES: Dict[str, List[Tuple[str, str]]] = {
    "光模块光通信": [
        ("中际旭创", "300308.SZ"),
        ("新易盛", "300502.SZ"),
        ("光迅科技", "002281.SZ"),
        ("华工科技", "000988.SZ"),
        ("天孚通信", "300394.SZ"),
        ("剑桥科技", "603083.SH"),
        ("太辰光", "300570.SZ"),
    ],
    "国产算力芯片": [
        ("海光信息", "688041.SH"),
        ("寒武纪-U", "688256.SH"),
        ("芯原股份", "688521.SH"),
        ("龙芯中科", "688047.SH"),
        ("景嘉微", "300474.SZ"),
        ("云天励飞-U", "688343.SH"),
        ("澜起科技", "688008.SH"),
    ],
}


def load_local_stock_data(start_date: str) -> pd.DataFrame:
    df = pd.read_parquet(DATA_PATH)
    df["date"] = pd.to_datetime(df["tradingdate"])
    df = df.sort_values(["code", "date"]).copy()

    if "adjfactor" in df.columns:
        df["adjfactor"] = pd.to_numeric(df["adjfactor"], errors="coerce")
        latest_adj = df.groupby("code")["adjfactor"].transform("last")
        ratio = df["adjfactor"] / latest_adj.replace(0, np.nan)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce") * ratio

    df = df[df["date"] >= pd.Timestamp(start_date)].copy()
    return df[["code", "date", "open", "high", "low", "close", "volume"]]


def classify_exit(point_type: str, stop_loss: float, take_profit: float, future_bars: pd.DataFrame) -> str:
    if future_bars.empty:
        return "no_data"
    for _, row in future_bars.iterrows():
        low = float(row["low"])
        high = float(row["high"])
        if point_type == "buy":
            hit_stop = low <= stop_loss
            hit_target = high >= take_profit
        else:
            hit_stop = high >= stop_loss
            hit_target = low <= take_profit

        if hit_stop and hit_target:
            return "both_same_bar"
        if hit_target:
            return "target_first"
        if hit_stop:
            return "stop_first"
    return "open"


def summarize_side(df: pd.DataFrame, side: str) -> Dict[str, Optional[float]]:
    side_df = df[df["point_type"] == side]
    prefix = "buy" if side == "buy" else "sell"
    if side_df.empty:
        return {
            f"{prefix}_n": 0,
            f"{prefix}_avg_lag_bars": None,
            f"{prefix}_pos10": None,
            f"{prefix}_pos20": None,
            f"{prefix}_med10": None,
            f"{prefix}_med20": None,
            f"{prefix}_tp_first": None,
            f"{prefix}_sl_first": None,
        }

    exit_counts = side_df["exit_20d"].value_counts(normalize=True)
    return {
        f"{prefix}_n": int(len(side_df)),
        f"{prefix}_avg_lag_bars": round(float(side_df["signal_lag_bars"].mean()), 2),
        f"{prefix}_pos10": round(float((side_df["ret_10d"] > 0).mean()), 3),
        f"{prefix}_pos20": round(float((side_df["ret_20d"] > 0).mean()), 3),
        f"{prefix}_med10": round(float(side_df["ret_10d"].median()), 4),
        f"{prefix}_med20": round(float(side_df["ret_20d"].median()), 4),
        f"{prefix}_tp_first": round(float(exit_counts.get("target_first", 0.0)), 3),
        f"{prefix}_sl_first": round(float(exit_counts.get("stop_first", 0.0)), 3),
    }


def run_walkforward_for_stock(
    df: pd.DataFrame,
    name: str,
    code: str,
    theme: str,
    min_bars: int,
) -> List[Dict]:
    df = df.sort_values("date").reset_index(drop=True).copy()
    if len(df) < min_bars + 21:
        return []

    records: List[Dict] = []
    seen = set()

    for end_pos in range(min_bars - 1, len(df) - 21):
        hist = df.iloc[: end_pos + 1].copy()
        current_date = pd.Timestamp(hist.iloc[-1]["date"])

        analyzer = ChanCoreV5T(
            hist,
            symbol=code,
            instrument_type="stock",
            dynamic_min_amplitude=True,
            dynamic_pivot_rules=True,
        )
        analyzer.analyze()

        for sig in analyzer.buy_sell_points:
            actionable_date = pd.Timestamp(sig["actionable_date"])
            if actionable_date != current_date:
                continue

            signal_key = (
                sig["type"],
                sig["point_type"],
                str(pd.Timestamp(sig["signal_date"]).date()),
                int(sig.get("stroke_idx", -1)),
                int(sig.get("pivot_idx", -1)),
            )
            if signal_key in seen:
                continue
            seen.add(signal_key)

            entry_pos = end_pos + 1
            if entry_pos + 19 >= len(df):
                continue

            entry_row = df.iloc[entry_pos]
            entry_date = pd.Timestamp(entry_row["date"])
            entry_price = float(entry_row["open"])
            future_20 = df.iloc[entry_pos : entry_pos + 20].reset_index(drop=True)
            close_10 = float(df.iloc[entry_pos + 9]["close"])
            close_20 = float(df.iloc[entry_pos + 19]["close"])

            if sig["point_type"] == "buy":
                ret_10d = close_10 / entry_price - 1.0
                ret_20d = close_20 / entry_price - 1.0
            else:
                ret_10d = entry_price / close_10 - 1.0
                ret_20d = entry_price / close_20 - 1.0

            records.append(
                {
                    "theme": theme,
                    "name": name,
                    "code": code,
                    "signal_type": sig["type"],
                    "point_type": sig["point_type"],
                    "signal_date": pd.Timestamp(sig["signal_date"]),
                    "actionable_date": actionable_date,
                    "trade_date": entry_date,
                    "entry_price": entry_price,
                    "signal_lag_days": int(sig["signal_lag_days"]),
                    "signal_lag_bars": int(sig["signal_lag_bars"]),
                    "ret_10d": ret_10d,
                    "ret_20d": ret_20d,
                    "exit_20d": classify_exit(
                        sig["point_type"],
                        float(sig["stop_loss"]),
                        float(sig["take_profit"]),
                        future_20,
                    ),
                }
            )

    return records


def iter_members(theme_filter: Optional[str]) -> Iterable[Tuple[str, str, str]]:
    for theme, members in THEMES.items():
        if theme_filter and theme != theme_filter:
            continue
        for name, code in members:
            yield theme, name, code


def build_summaries(details: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    theme_rows = []
    for theme, group in details.groupby("theme"):
        row = {"theme": theme}
        row.update(summarize_side(group, "buy"))
        row.update(summarize_side(group, "sell"))
        theme_rows.append(row)

    signal_rows = []
    for (theme, signal_type), group in details.groupby(["theme", "signal_type"]):
        exit_counts = group["exit_20d"].value_counts(normalize=True)
        signal_rows.append(
            {
                "theme": theme,
                "signal_type": signal_type,
                "point_type": group["point_type"].iloc[0],
                "n": int(len(group)),
                "avg_lag_bars": round(float(group["signal_lag_bars"].mean()), 2),
                "pos10": round(float((group["ret_10d"] > 0).mean()), 3),
                "pos20": round(float((group["ret_20d"] > 0).mean()), 3),
                "med10": round(float(group["ret_10d"].median()), 4),
                "med20": round(float(group["ret_20d"].median()), 4),
                "tp_first": round(float(exit_counts.get("target_first", 0.0)), 3),
                "sl_first": round(float(exit_counts.get("stop_first", 0.0)), 3),
            }
        )

    stock_rows = []
    for (theme, name, code), group in details.groupby(["theme", "name", "code"]):
        row = {"theme": theme, "name": name, "code": code}
        row.update(summarize_side(group, "buy"))
        row.update(summarize_side(group, "sell"))
        stock_rows.append(row)

    return (
        pd.DataFrame(theme_rows),
        pd.DataFrame(signal_rows),
        pd.DataFrame(stock_rows),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="v5t 严格 walk-forward 回测")
    parser.add_argument("--theme", choices=list(THEMES.keys()), help="只回测某一个主题")
    parser.add_argument("--start-date", default="2024-01-01", help="数据起始日期，YYYY-MM-DD")
    parser.add_argument("--min-bars", type=int, default=120, help="开始回测前最少历史K线数")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="主题汇总CSV输出路径")
    parser.add_argument("--details-output", default=DEFAULT_DETAILS, help="明细CSV输出路径")
    args = parser.parse_args()

    all_stock = load_local_stock_data(args.start_date)
    records: List[Dict] = []
    for theme, name, code in iter_members(args.theme):
        df = all_stock[all_stock["code"] == code].copy()
        records.extend(run_walkforward_for_stock(df, name, code, theme, args.min_bars))

    if not records:
        print("没有生成可回测信号。")
        return

    details = pd.DataFrame(records).sort_values(["theme", "trade_date", "code", "signal_type"])
    theme_summary, signal_summary, stock_summary = build_summaries(details)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    theme_summary.to_csv(args.output, index=False, encoding="utf-8-sig")
    details.to_csv(args.details_output, index=False, encoding="utf-8-sig")

    print("THEME_SUMMARY")
    print(theme_summary.to_dict("records"))
    print("SIGNAL_SUMMARY")
    print(signal_summary.sort_values(["theme", "point_type", "signal_type"]).to_dict("records"))
    print("STOCK_SUMMARY")
    print(stock_summary.sort_values(["theme", "buy_med20"], ascending=[True, False]).to_dict("records"))
    print("OUTPUTS")
    print({"summary_csv": args.output, "details_csv": args.details_output, "total_signals": int(len(details))})


if __name__ == "__main__":
    main()
