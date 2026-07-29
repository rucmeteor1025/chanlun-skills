#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出单只股票的 v5t 结构 + 买卖点 + 轻量回测 HTML 报告
"""
import argparse
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../../.."))
CORE_DIR = os.path.join(PROJECT_ROOT, "投研系统/工具脚本/技术")
DATA_DIR = os.path.join(PROJECT_ROOT, "投研系统/工具脚本/数据")
sys.path.insert(0, CORE_DIR)
sys.path.insert(0, DATA_DIR)

from chan_core_v5t import ChanCoreV5T
from local_market_data import DEFAULT_LOCAL_MARKET_DB_DIR
from export_v5t_selected_report_html import (
    build_page,
    build_structure_chart_html,
    classify_exit,
    format_date,
    next_trade_pos,
    render_table,
    summarize_side,
)


DATA_PATH = os.path.join(DEFAULT_LOCAL_MARKET_DB_DIR, "stock_daily.parquet")
OUTPUT_DIR = os.path.join(CURRENT_DIR, "输出")


def load_stock_data(code: str, start_date: str = "2024-01-01") -> pd.DataFrame:
    df = pd.read_parquet(DATA_PATH)
    df["date"] = pd.to_datetime(df["tradingdate"])
    df = df[df["code"] == code].sort_values("date").copy()

    if "adjfactor" in df.columns:
        df["adjfactor"] = pd.to_numeric(df["adjfactor"], errors="coerce")
        latest_adj = df["adjfactor"].dropna().iloc[-1]
        ratio = df["adjfactor"] / latest_adj if latest_adj else np.nan
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce") * ratio

    df = df[df["date"] >= pd.Timestamp(start_date)].copy()
    if df.empty:
        raise ValueError(f"未找到数据: {code}")
    return df[["code", "date", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def build_single_report(name: str, code: str) -> str:
    sdf = load_stock_data(code)
    analyzer = ChanCoreV5T(
        sdf,
        symbol=code,
        instrument_type="stock",
        dynamic_min_amplitude=True,
        dynamic_pivot_rules=True,
    )
    analyzer.analyze()

    backtest_rows = []
    for sig in analyzer.buy_sell_points:
        actionable_date = pd.Timestamp(sig["actionable_date"])
        entry_pos = next_trade_pos(sdf, actionable_date)
        if entry_pos is None or entry_pos + 19 >= len(sdf):
            continue
        entry_row = sdf.iloc[entry_pos]
        entry_price = float(entry_row["open"])
        close_10 = float(sdf.iloc[entry_pos + 9]["close"])
        close_20 = float(sdf.iloc[entry_pos + 19]["close"])
        if sig["point_type"] == "buy":
            ret_10d = close_10 / entry_price - 1.0
            ret_20d = close_20 / entry_price - 1.0
        else:
            ret_10d = entry_price / close_10 - 1.0
            ret_20d = entry_price / close_20 - 1.0

        backtest_rows.append(
            {
                "signal_type": sig["type"],
                "point_type": sig["point_type"],
                "signal_date": format_date(sig["signal_date"]),
                "actionable_date": format_date(sig["actionable_date"]),
                "trade_date": format_date(entry_row["date"]),
                "lag_days": int(sig["signal_lag_days"]),
                "lag_bars": int(sig["signal_lag_bars"]),
                "entry_price": round(entry_price, 2),
                "ret_10d": f"{ret_10d:.2%}",
                "ret_20d": f"{ret_20d:.2%}",
                "exit_20d": classify_exit(
                    sig["point_type"],
                    float(sig["stop_loss"]),
                    float(sig["take_profit"]),
                    sdf.iloc[entry_pos : entry_pos + 20].reset_index(drop=True),
                ),
            }
        )

    backtest_df = pd.DataFrame(backtest_rows)
    pivot_df = pd.DataFrame(
        [
            {
                "level": p.get("level"),
                "start_date": format_date(p["start_date"]),
                "end_date": format_date(p["end_date"]),
                "ZD": round(float(p["low"]), 2),
                "ZG": round(float(p["high"]), 2),
                "GG": round(float(p["gg"]), 2),
                "DD": round(float(p["dd"]), 2),
                "stroke_count": int(p["stroke_count"]),
                "amplitude": f"{float(p['amplitude']):.2%}",
                "validity": p.get("validity", "-"),
                "reason": p.get("termination_reason", "-"),
            }
            for p in analyzer.pivots + analyzer.sub_level_pivots
        ]
    )
    signal_df = pd.DataFrame(
        [
            {
                "type": s["type"],
                "point_type": s["point_type"],
                "price": round(float(s["price"]), 2),
                "signal_date": format_date(s["signal_date"]),
                "actionable_date": format_date(s["actionable_date"]),
                "trade_date": format_date(s["trade_date"]),
                "lag_days": int(s["signal_lag_days"]),
                "lag_bars": int(s["signal_lag_bars"]),
                "stop_loss": round(float(s["stop_loss"]), 2),
                "take_profit": round(float(s["take_profit"]), 2),
                "desc": s["description"],
            }
            for s in analyzer.buy_sell_points
        ]
    )

    signal_breakdown = pd.DataFrame()
    if not backtest_df.empty:
        signal_breakdown = (
            backtest_df.groupby(["signal_type", "point_type"], as_index=False)
            .agg(
                n=("signal_type", "size"),
                avg_lag_days=("lag_days", "mean"),
                avg_lag_bars=("lag_bars", "mean"),
                ret20_win=("ret_20d", lambda s: float((pd.Series(s).str.rstrip("%").astype(float) > 0).mean())),
            )
        )
        signal_breakdown["avg_lag_days"] = signal_breakdown["avg_lag_days"].map(lambda x: round(float(x), 1))
        signal_breakdown["avg_lag_bars"] = signal_breakdown["avg_lag_bars"].map(lambda x: round(float(x), 1))
        signal_breakdown["ret20_win"] = signal_breakdown["ret20_win"].map(lambda x: f"{float(x):.1%}")

    summary = {}
    summary.update(summarize_side(backtest_df, "buy"))
    summary.update(summarize_side(backtest_df, "sell"))
    latest_signal = analyzer.buy_sell_points[-1] if analyzer.buy_sell_points else None
    chart_html = build_structure_chart_html(sdf, analyzer, name, code)

    cards = [
        ("走势", analyzer.trend_type.get("summary") if "summary" in analyzer.trend_type else analyzer.trend_type.get("type")),
        ("日线中枢", str(len(analyzer.pivots))),
        ("次级别中枢", str(len(analyzer.sub_level_pivots))),
        ("最新信号", "-" if latest_signal is None else f"{latest_signal['type']} / {format_date(latest_signal['trade_date'])}"),
        ("买点20日胜率", summary["buy_pos20"]),
        ("卖点20日胜率", summary["sell_pos20"]),
    ]
    card_html = "".join(
        f'<div class="card"><div class="card-label">{label}</div><div class="card-value">{value}</div></div>'
        for label, value in cards
    )

    section_html = f"""
    <section class="stock-section">
      <div class="section-head">
        <h2>{name} <span>{code}</span></h2>
        <div class="meta">轻量回测口径：actionable_date 后次日开盘进场</div>
      </div>
      <div class="chart-block">{chart_html}</div>
      <div class="card-grid">{card_html}</div>
      <h3>中枢</h3>
      {render_table(pivot_df)}
      <h3>买卖点</h3>
      {render_table(signal_df)}
      <h3>回测明细</h3>
      {render_table(backtest_df)}
      <h3>信号分型统计</h3>
      {render_table(signal_breakdown)}
    </section>
    """

    title = f"{name} {code} 中枢、买卖点与轻量回测报告"
    desc = "口径：本地前复权近似价格，买卖点按 actionable_date 确认，次日开盘进场，统计 10/20 日收益与 20 日内止盈止损先后。"
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return build_page(title, desc, section_html, generated_at)


def main() -> None:
    parser = argparse.ArgumentParser(description="导出单只股票的 v5t HTML 报告")
    parser.add_argument("--code", required=True, help="股票代码，如 688111.SH")
    parser.add_argument("--name", required=True, help="股票名称，如 金山办公")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    safe_code = args.code.replace(".", "_").replace("-", "_")
    output_path = os.path.join(OUTPUT_DIR, f"v5t_{args.name}_{safe_code}_中枢买卖点回测报告.html")
    content = build_single_report(args.name, args.code)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(output_path)


if __name__ == "__main__":
    main()
