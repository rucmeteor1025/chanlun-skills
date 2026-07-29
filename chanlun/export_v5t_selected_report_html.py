#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
导出指定股票的 v5t 结构 + 买卖点 + 轻量回测 HTML 报告
"""
import html
import os
import sys
from datetime import datetime
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs


CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../../.."))
CORE_DIR = os.path.join(PROJECT_ROOT, "投研系统/工具脚本/技术")
DATA_DIR = os.path.join(PROJECT_ROOT, "投研系统/工具脚本/数据")
sys.path.insert(0, CORE_DIR)
sys.path.insert(0, DATA_DIR)

from chan_core_v5t import ChanCoreV5T
from local_market_data import DEFAULT_LOCAL_MARKET_DB_DIR


DATA_PATH = os.path.join(DEFAULT_LOCAL_MARKET_DB_DIR, "stock_daily.parquet")
OUTPUT_DIR = os.path.join(CURRENT_DIR, "输出")
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "v5t_四股中枢买卖点回测报告.html")

SELECTED: List[Tuple[str, str]] = [
    ("中际旭创", "300308.SZ"),
    ("天孚通信", "300394.SZ"),
    ("寒武纪-U", "688256.SH"),
    ("海光信息", "688041.SH"),
]


def load_stock_data(start_date: str = "2024-01-01") -> pd.DataFrame:
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


def next_trade_pos(df: pd.DataFrame, actionable_date: pd.Timestamp):
    later = df.index[df["date"] > actionable_date]
    if len(later) == 0:
        return None
    return int(later[0])


def summarize_side(df: pd.DataFrame, side: str) -> Dict[str, str]:
    prefix = "buy" if side == "buy" else "sell"
    if df.empty or "point_type" not in df.columns:
        return {
            f"{prefix}_n": "0",
            f"{prefix}_lag": "-",
            f"{prefix}_pos10": "-",
            f"{prefix}_pos20": "-",
            f"{prefix}_med10": "-",
            f"{prefix}_med20": "-",
        }
    side_df = df[df["point_type"] == side]
    if side_df.empty:
        return {
            f"{prefix}_n": "0",
            f"{prefix}_lag": "-",
            f"{prefix}_pos10": "-",
            f"{prefix}_pos20": "-",
            f"{prefix}_med10": "-",
            f"{prefix}_med20": "-",
        }
    ret_10d = side_df["ret_10d"].astype(str).str.rstrip("%").astype(float) / 100.0
    ret_20d = side_df["ret_20d"].astype(str).str.rstrip("%").astype(float) / 100.0
    return {
        f"{prefix}_n": str(len(side_df)),
        f"{prefix}_lag": f"{side_df['lag_days'].mean():.1f}天 / {side_df['lag_bars'].mean():.1f} bars",
        f"{prefix}_pos10": f"{(ret_10d > 0).mean():.1%}",
        f"{prefix}_pos20": f"{(ret_20d > 0).mean():.1%}",
        f"{prefix}_med10": f"{ret_10d.median():.2%}",
        f"{prefix}_med20": f"{ret_20d.median():.2%}",
    }


def format_date(value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "-"
    return pd.Timestamp(value).strftime("%Y-%m-%d")


def render_table(df: pd.DataFrame) -> str:
    if df.empty:
        return '<div class="empty">无数据</div>'
    return df.to_html(index=False, classes="report-table", border=0, justify="left")


def build_structure_chart_html(sdf: pd.DataFrame, analyzer: ChanCoreV5T, name: str, code: str) -> str:
    fig = go.Figure()

    fig.add_trace(
        go.Candlestick(
            x=sdf["date"],
            open=sdf["open"],
            high=sdf["high"],
            low=sdf["low"],
            close=sdf["close"],
            name="K线",
            increasing_line_color="#c0392b",
            decreasing_line_color="#1f7a4d",
            increasing_fillcolor="#e4685d",
            decreasing_fillcolor="#5cab7d",
        )
    )

    for stroke in analyzer.strokes:
        color = "#c0392b" if stroke["direction"] == "up" else "#1f7a4d"
        fig.add_trace(
            go.Scatter(
                x=[stroke["start_date"], stroke["end_date"]],
                y=[stroke["start_price"], stroke["end_price"]],
                mode="lines+markers",
                line=dict(color=color, width=2.4),
                marker=dict(size=6, color=color),
                name="笔",
                showlegend=False,
                hovertemplate=(
                    f"{'向上笔' if stroke['direction'] == 'up' else '向下笔'}"
                    "<br>%{x|%Y-%m-%d}<br>%{y:.2f}<extra></extra>"
                ),
            )
        )

    pivot_styles = [
        (analyzer.pivots, "rgba(195, 121, 45, 0.18)", "#9b5d2e", "日线中枢"),
        (analyzer.sub_level_pivots, "rgba(52, 121, 173, 0.12)", "#3479ad", "次级别中枢"),
    ]
    for pivot_group, fill_color, line_color, label in pivot_styles:
        for i, pivot in enumerate(pivot_group, start=1):
            fig.add_shape(
                type="rect",
                x0=pivot["start_date"],
                x1=pivot["end_date"],
                y0=pivot["low"],
                y1=pivot["high"],
                fillcolor=fill_color,
                line=dict(color=line_color, width=1.2),
            )
            fig.add_hline(
                y=pivot["low"],
                line=dict(color=line_color, width=1, dash="dot"),
                opacity=0.45,
            )
            fig.add_hline(
                y=pivot["high"],
                line=dict(color=line_color, width=1, dash="dot"),
                opacity=0.45,
            )
            mid_date = pd.Timestamp(
                (pd.Timestamp(pivot["start_date"]).timestamp() + pd.Timestamp(pivot["end_date"]).timestamp()) / 2,
                unit="s",
            )
            fig.add_annotation(
                x=mid_date,
                y=pivot["high"],
                text=f"{label}{i} {pivot['stroke_count']}笔",
                showarrow=False,
                yshift=12,
                font=dict(size=10, color=line_color),
                bgcolor="rgba(255,255,255,0.92)",
                bordercolor=line_color,
                borderwidth=1,
            )

    for point in analyzer.buy_sell_points:
        is_buy = point["point_type"] == "buy"
        color = "#c0392b" if is_buy else "#1f7a4d"
        symbol = "triangle-up" if is_buy else "triangle-down"
        label = point["type"]
        fig.add_trace(
            go.Scatter(
                x=[point["signal_date"]],
                y=[point["price"]],
                mode="markers+text",
                text=[label],
                textposition="bottom center" if is_buy else "top center",
                marker=dict(
                    size=13,
                    color=color,
                    symbol=symbol,
                    line=dict(color="#ffffff", width=1),
                ),
                name="买卖点",
                showlegend=False,
                hovertemplate=(
                    f"{label} / {'买点' if is_buy else '卖点'}"
                    "<br>信号日: %{x|%Y-%m-%d}"
                    f"<br>价格: {float(point['price']):.2f}"
                    f"<br>确认日: {format_date(point['actionable_date'])}"
                    f"<br>交易日: {format_date(point['trade_date'])}"
                    f"<br>滞后: {int(point['signal_lag_days'])}天 / {int(point['signal_lag_bars'])} bars"
                    "<extra></extra>"
                ),
            )
        )

    trend_summary = analyzer.trend_type.get("summary") if isinstance(analyzer.trend_type, dict) else ""
    fig.update_layout(
        title=f"{name} {code} K线结构图",
        height=760,
        margin=dict(l=20, r=20, t=56, b=24),
        paper_bgcolor="#fffdf8",
        plot_bgcolor="#fffdf8",
        hovermode="x unified",
        xaxis=dict(
            title="日期",
            rangeslider=dict(visible=False),
            showgrid=False,
        ),
        yaxis=dict(
            title="价格",
            gridcolor="rgba(155, 93, 46, 0.10)",
            zeroline=False,
        ),
        font=dict(family="PingFang SC, Hiragino Sans GB, Microsoft YaHei, sans-serif", color="#1d1b18"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0.0),
    )
    fig.add_annotation(
        x=0.995,
        y=1.06,
        xref="paper",
        yref="paper",
        xanchor="right",
        yanchor="bottom",
        text=(
            f"走势: {trend_summary or '-'}"
            f" | 笔: {len(analyzer.strokes)}"
            f" | 日线中枢: {len(analyzer.pivots)}"
            f" | 次级别: {len(analyzer.sub_level_pivots)}"
        ),
        showarrow=False,
        font=dict(size=12, color="#6a655d"),
    )

    return fig.to_html(full_html=False, include_plotlyjs=False, config={"responsive": True})


def build_page(title: str, description: str, body_html: str, generated_at: str) -> str:
    return f"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
      <meta charset="utf-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1" />
      <title>{html.escape(title)}</title>
      <script>{get_plotlyjs()}</script>
      <style>
        :root {{
          --bg: #f5f1e8;
          --paper: #fffdf8;
          --ink: #1d1b18;
          --muted: #6a655d;
          --line: #ddd3c2;
          --accent: #9b5d2e;
          --accent-soft: #efe1d2;
          --good: #205f3b;
          --bad: #8a2f2f;
        }}
        * {{ box-sizing: border-box; }}
        body {{
          margin: 0;
          font-family: "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
          color: var(--ink);
          background:
            radial-gradient(circle at top right, rgba(155,93,46,0.12), transparent 28%),
            linear-gradient(180deg, #f6f0e5 0%, #f1ebe0 100%);
        }}
        .wrap {{
          max-width: 1400px;
          margin: 0 auto;
          padding: 32px 20px 48px;
        }}
        .hero {{
          background: var(--paper);
          border: 1px solid var(--line);
          border-radius: 20px;
          padding: 28px;
          box-shadow: 0 18px 40px rgba(67, 52, 35, 0.08);
        }}
        h1 {{
          margin: 0 0 10px;
          font-size: 32px;
          letter-spacing: 0.02em;
        }}
        .hero p {{
          margin: 0;
          color: var(--muted);
          line-height: 1.6;
        }}
        .stock-section {{
          margin-top: 24px;
          background: var(--paper);
          border: 1px solid var(--line);
          border-radius: 20px;
          padding: 24px;
          box-shadow: 0 18px 40px rgba(67, 52, 35, 0.08);
        }}
        .section-head {{
          display: flex;
          justify-content: space-between;
          gap: 12px;
          align-items: baseline;
          flex-wrap: wrap;
        }}
        .section-head h2 {{
          margin: 0;
          font-size: 28px;
        }}
        .section-head span {{
          color: var(--muted);
          font-size: 16px;
          margin-left: 8px;
        }}
        .meta {{
          color: var(--muted);
          font-size: 14px;
        }}
        .chart-block {{
          margin: 12px 0 22px;
          border: 1px solid var(--line);
          border-radius: 18px;
          overflow: hidden;
          background: linear-gradient(180deg, #fffdf8 0%, #fbf7f0 100%);
        }}
        .card-grid {{
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
          gap: 12px;
          margin: 18px 0 24px;
        }}
        .card {{
          padding: 14px 16px;
          border-radius: 16px;
          background: var(--accent-soft);
          border: 1px solid rgba(155, 93, 46, 0.15);
        }}
        .card-label {{
          color: var(--muted);
          font-size: 13px;
          margin-bottom: 6px;
        }}
        .card-value {{
          font-size: 18px;
          font-weight: 700;
        }}
        h3 {{
          margin: 24px 0 12px;
          font-size: 18px;
          color: var(--accent);
        }}
        .report-table {{
          width: 100%;
          border-collapse: collapse;
          font-size: 13px;
          overflow: hidden;
          border-radius: 12px;
          display: block;
          overflow-x: auto;
          white-space: nowrap;
        }}
        .report-table th,
        .report-table td {{
          padding: 10px 12px;
          border-bottom: 1px solid var(--line);
          text-align: left;
        }}
        .report-table th {{
          position: sticky;
          top: 0;
          background: #f5ede0;
          color: #4f4336;
        }}
        .empty {{
          padding: 14px 16px;
          border: 1px dashed var(--line);
          border-radius: 12px;
          color: var(--muted);
          background: #fbf7f0;
        }}
        @media (max-width: 720px) {{
          .wrap {{ padding: 18px 12px 32px; }}
          h1 {{ font-size: 26px; }}
          .section-head h2 {{ font-size: 22px; }}
        }}
      </style>
    </head>
    <body>
      <div class="wrap">
        <div class="hero">
          <h1>{html.escape(title)}</h1>
          <p>生成时间：{html.escape(generated_at)}。{html.escape(description)}</p>
        </div>
        {body_html}
      </div>
    </body>
    </html>
    """


def build_report() -> Tuple[str, List[Tuple[str, str]]]:
    stock = load_stock_data()
    sections: List[str] = []
    single_reports: List[Tuple[str, str]] = []
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for name, code in SELECTED:
        sdf = stock[stock["code"] == code].sort_values("date").reset_index(drop=True).copy()
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

        summary = {}
        summary.update(summarize_side(backtest_df, "buy"))
        summary.update(summarize_side(backtest_df, "sell"))
        latest_signal = analyzer.buy_sell_points[-1] if analyzer.buy_sell_points else None
        chart_html = build_structure_chart_html(sdf, analyzer, name, code)

        signal_breakdown = pd.DataFrame()
        if not backtest_df.empty:
            signal_breakdown = (
                backtest_df.groupby(["signal_type", "point_type"], as_index=False)
                .agg(
                    n=("signal_type", "size"),
                    avg_lag_days=("lag_days", "mean"),
                    avg_lag_bars=("lag_bars", "mean"),
                    ret20_win=("ret_20d", lambda s: float((pd.Series(s).str.rstrip('%').astype(float) > 0).mean())),
                )
            )
            signal_breakdown["avg_lag_days"] = signal_breakdown["avg_lag_days"].map(lambda x: round(float(x), 1))
            signal_breakdown["avg_lag_bars"] = signal_breakdown["avg_lag_bars"].map(lambda x: round(float(x), 1))
            signal_breakdown["ret20_win"] = signal_breakdown["ret20_win"].map(lambda x: f"{float(x):.1%}")

        cards = [
            ("走势", analyzer.trend_type.get("summary") if "summary" in analyzer.trend_type else analyzer.trend_type.get("type")),
            ("日线中枢", str(len(analyzer.pivots))),
            ("次级别中枢", str(len(analyzer.sub_level_pivots))),
            ("最新信号", "-" if latest_signal is None else f"{latest_signal['type']} / {format_date(latest_signal['trade_date'])}"),
            ("买点20日胜率", summary["buy_pos20"]),
            ("卖点20日胜率", summary["sell_pos20"]),
        ]

        card_html = "".join(
            f'<div class="card"><div class="card-label">{html.escape(label)}</div><div class="card-value">{html.escape(value)}</div></div>'
            for label, value in cards
        )

        section_html = f"""
        <section class="stock-section">
          <div class="section-head">
            <h2>{html.escape(name)} <span>{html.escape(code)}</span></h2>
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
        sections.append(section_html)

        single_title = f"{name} {code} 中枢、买卖点与轻量回测报告"
        single_desc = "口径：本地前复权近似价格，买卖点按 actionable_date 确认，次日开盘进场，统计 10/20 日收益与 20 日内止盈止损先后。"
        single_content = build_page(single_title, single_desc, section_html, generated_at)
        safe_code = code.replace(".", "_").replace("-", "_")
        single_path = os.path.join(OUTPUT_DIR, f"v5t_{name}_{safe_code}_中枢买卖点回测报告.html")
        single_reports.append((single_path, single_content))

    combined_title = "v5t 四股中枢、买卖点与轻量回测报告"
    combined_desc = "口径：本地前复权近似价格，买卖点按 actionable_date 确认，次日开盘进场，统计 10/20 日收益与 20 日内止盈止损先后。"
    return build_page(combined_title, combined_desc, "".join(sections), generated_at), single_reports


def main() -> None:
    content, single_reports = build_report()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    for path, single_content in single_reports:
        with open(path, "w", encoding="utf-8") as f:
            f.write(single_content)
    print(OUTPUT_PATH)
    for path, _ in single_reports:
        print(path)


if __name__ == "__main__":
    main()
