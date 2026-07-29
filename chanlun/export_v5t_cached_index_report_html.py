#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
基于完整本地 index_daily.parquet 导出指数 v5t 结构 + 买卖点 + 轻量回测 HTML 报告
"""
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../../.."))
CORE_DIR = os.path.join(PROJECT_ROOT, "投研系统/工具脚本/技术")
sys.path.insert(0, CORE_DIR)

from chan_core_v5t import ChanCoreV5T
from export_v5t_selected_report_html import (
    build_page,
    build_structure_chart_html,
    classify_exit,
    format_date,
    next_trade_pos,
    render_table,
    summarize_side,
)


OUTPUT_DIR = os.path.join(CURRENT_DIR, "输出")
LOCAL_INDEX_DAILY = Path(PROJECT_ROOT) / "投研系统" / "本地数据" / "index_daily.parquet"

SELECTED: List[Dict[str, str]] = [
    {
        "name": "上证指数",
        "symbol": "000001.SH",
        "output_name": "v5t_上证指数_000001_SH_中枢买卖点回测报告.html",
    },
    {
        "name": "恒生科技",
        "symbol": "HSTECH.HI",
        "output_name": "v5t_恒生科技_HSTECH_HI_中枢买卖点回测报告.html",
    },
]


def load_local_index_table() -> pd.DataFrame:
    if not LOCAL_INDEX_DAILY.exists():
        raise FileNotFoundError(f"未找到本地指数日线: {LOCAL_INDEX_DAILY}")

    df = pd.read_parquet(
        LOCAL_INDEX_DAILY,
        columns=["code", "tradingdate", "open", "high", "low", "close", "volume"],
    ).copy()
    df["code"] = df["code"].astype(str).str.upper()
    df["tradingdate"] = pd.to_datetime(df["tradingdate"])
    return df


def load_local_index_data(df_all: pd.DataFrame, code: str) -> pd.DataFrame:
    df = df_all[df_all["code"] == code.upper()].copy()
    if df.empty:
        raise ValueError(f"本地 index_daily.parquet 中未找到指数: {code}")

    df = df.rename(columns={"tradingdate": "date"})
    df = df.sort_values("date").reset_index(drop=True)
    if "volume" not in df.columns:
        df["volume"] = 0
    return df[["date", "open", "high", "low", "close", "volume"]]


def build_report() -> Tuple[str, List[Tuple[str, str]]]:
    sections: List[str] = []
    single_reports: List[Tuple[str, str]] = []
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    df_all = load_local_index_table()

    for item in SELECTED:
        name = item["name"]
        code = item["symbol"]
        sdf = load_local_index_data(df_all, code)
        analyzer = ChanCoreV5T(
            sdf,
            symbol=code,
            instrument_type="index",
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
            <div class="meta">数据口径：本地 index_daily.parquet，买卖点按 actionable_date 确认，次日开盘进场</div>
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
        sections.append(section_html)

        title = f"{name} {code} 中枢、买卖点与轻量回测报告"
        desc = "口径：完整本地 index_daily.parquet，买卖点按 actionable_date 确认，次日开盘进场，统计 10/20 日收益与 20 日内止盈止损先后。"
        single_reports.append(
            (
                os.path.join(OUTPUT_DIR, item["output_name"]),
                build_page(title, desc, section_html, generated_at),
            )
        )

    combined_title = "v5t 上证与恒生科技中枢、买卖点与轻量回测报告"
    combined_desc = "口径：完整本地 index_daily.parquet，买卖点按 actionable_date 确认，次日开盘进场，统计 10/20 日收益与 20 日内止盈止损先后。"
    combined_html = build_page(combined_title, combined_desc, "".join(sections), generated_at)
    return combined_html, single_reports


def main() -> None:
    content, single_reports = build_report()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    combined_path = os.path.join(OUTPUT_DIR, "v5t_上证_恒生科技_中枢买卖点回测报告.html")
    with open(combined_path, "w", encoding="utf-8") as f:
        f.write(content)
    print(combined_path)
    for path, single_content in single_reports:
        with open(path, "w", encoding="utf-8") as f:
            f.write(single_content)
        print(path)


if __name__ == "__main__":
    main()
