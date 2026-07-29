#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论完整分析 v5 - 多周期入口
"""
import argparse
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence, Tuple

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../../.."))
tech_dir = os.path.join(project_root, "投研系统/工具脚本/技术")
data_dir = os.path.join(project_root, "投研系统/工具脚本/数据")
output_dir = os.path.join(current_dir, "输出")
sys.path.insert(0, tech_dir)
sys.path.insert(0, data_dir)
sys.path.insert(0, project_root)

from chan_core_v5 import ChanCoreV5
from local_market_data import DEFAULT_LOCAL_MARKET_DB_DIR


INDEX_NAME_MAP = {
    "000001.SH": "上证指数",
    "000016.SH": "上证50",
    "000300.SH": "沪深300",
    "000688.SH": "科创50",
    "000852.SH": "中证1000",
    "000905.SH": "中证500",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
}

FREQ_LABELS = {
    "daily": "日线",
    "30min": "30分钟",
}

_TRADE_DATE_SET = None


def normalize_symbol(symbol: str) -> str:
    """标准化代码格式。"""
    symbol = symbol.strip().upper()
    if "." in symbol:
        return symbol

    if symbol.startswith("6"):
        return f"{symbol}.SH"
    if symbol.startswith("399"):
        return f"{symbol}.SZ"
    if symbol.startswith(("0", "3")):
        return f"{symbol}.SZ"
    return symbol


def is_index_symbol(symbol: str) -> bool:
    """根据代码规则判断是否为指数。"""
    symbol = symbol.upper()
    if symbol.endswith(".CSI") or symbol.endswith(".SI"):
        return True
    if symbol.endswith(".SZ") and symbol.startswith("399"):
        return True
    if symbol.endswith(".SH") and symbol.startswith("000"):
        return True
    return False


def get_market_db_dir() -> Path:
    return Path(DEFAULT_LOCAL_MARKET_DB_DIR)


def get_reference_datetime(end_date: Optional[str]) -> pd.Timestamp:
    if end_date:
        return pd.Timestamp(datetime.strptime(end_date, "%Y%m%d")) + pd.Timedelta(hours=15)
    return pd.Timestamp.now()


def get_trade_date_set() -> set:
    global _TRADE_DATE_SET
    if _TRADE_DATE_SET is not None:
        return _TRADE_DATE_SET

    try:
        import akshare as ak

        df = ak.tool_trade_date_hist_sina()
        _TRADE_DATE_SET = set(pd.to_datetime(df["trade_date"]).dt.normalize())
    except Exception:
        _TRADE_DATE_SET = set()
    return _TRADE_DATE_SET


def is_trade_day(ts: pd.Timestamp) -> bool:
    ts = pd.Timestamp(ts).normalize()
    trade_dates = get_trade_date_set()
    if trade_dates:
        return ts in trade_dates
    return ts.weekday() < 5


def get_latest_trade_day(reference_dt: pd.Timestamp) -> pd.Timestamp:
    d = pd.Timestamp(reference_dt).normalize()
    trade_dates = get_trade_date_set()
    if trade_dates:
        while d not in trade_dates:
            d -= pd.Timedelta(days=1)
        return d

    while d.weekday() >= 5:
        d -= pd.Timedelta(days=1)
    return d


def count_trade_days_after(last_dt: pd.Timestamp, reference_dt: pd.Timestamp) -> int:
    last_day = pd.Timestamp(last_dt).normalize()
    target_day = get_latest_trade_day(reference_dt)
    if last_day >= target_day:
        return 0

    count = 0
    d = last_day + pd.Timedelta(days=1)
    while d <= target_day:
        if is_trade_day(d):
            count += 1
        d += pd.Timedelta(days=1)
    return count


def read_parquet_by_code(path: Path, code_col: str, code_value: str, columns: Sequence[str]) -> pd.DataFrame:
    filters = [(code_col, "==", code_value)]
    try:
        return pd.read_parquet(path, filters=filters, columns=list(columns))
    except Exception:
        df = pd.read_parquet(path, columns=list(columns))
        if code_col not in df.columns:
            raise
        mask = df[code_col].astype(str).str.upper() == code_value.upper()
        return df.loc[mask].copy()


def load_with_schema(
    path: Path,
    code_value: str,
    schema_candidates: Sequence[Tuple[str, str]],
    value_columns: Sequence[str],
) -> Tuple[pd.DataFrame, str, str]:
    last_error = None
    for code_col, date_col in schema_candidates:
        columns = [code_col, date_col, *value_columns]
        try:
            df = read_parquet_by_code(path, code_col, code_value, columns)
            return df, code_col, date_col
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        raise last_error
    raise ValueError(f"无法读取 parquet: {path}")


def normalize_ohlcv(df: pd.DataFrame, date_col: str, end_dt: Optional[pd.Timestamp]) -> pd.DataFrame:
    out = df.copy()
    out = out.rename(columns={date_col: "date", "amount": "amt"})
    out["date"] = pd.to_datetime(out["date"], errors="coerce")
    out = out.dropna(subset=["date"])

    if end_dt is not None:
        out = out[out["date"] <= end_dt]

    for col in ["open", "high", "low", "close", "volume", "amt", "adjfactor"]:
        if col in out.columns:
            out[col] = pd.to_numeric(out[col], errors="coerce")

    if "volume" not in out.columns:
        out["volume"] = 0

    keep_cols = [col for col in ["date", "open", "high", "low", "close", "volume"] if col in out.columns]
    out = out[keep_cols]
    out = out.sort_values("date").drop_duplicates(subset=["date"], keep="last").reset_index(drop=True)
    return out


def warn_daily_staleness(symbol_full: str, latest_dt: pd.Timestamp, reference_dt: pd.Timestamp) -> None:
    latest_trade_day = get_latest_trade_day(reference_dt)
    allowed_latest = latest_trade_day - pd.Timedelta(days=1)
    if latest_dt.normalize() >= allowed_latest:
        return

    update_cmd = (
        f"python3 '{project_root}/投研系统/工具脚本/数据/update_market.py'"
    )
    print(
        f"⚠️  日线数据可能过期：{symbol_full} 最新 {latest_dt.strftime('%Y-%m-%d')}，"
        f"最近交易日 {latest_trade_day.strftime('%Y-%m-%d')}"
    )
    print(f"   建议先更新日线库：{update_cmd}")


def load_daily_data(symbol_full: str, count: int, end_date: Optional[str]) -> pd.DataFrame:
    market_dir = get_market_db_dir()
    is_index = is_index_symbol(symbol_full)
    parquet = market_dir / ("index_daily.parquet" if is_index else "stock_daily.parquet")
    if not parquet.exists():
        raise FileNotFoundError(f"未找到本地日线库：{parquet}")

    schema_candidates = [("code", "tradingdate")]
    value_columns = ["open", "high", "low", "close", "volume", "amt"]
    if not is_index:
        schema_candidates = [("code", "tradingdate"), ("stockcode", "tradingdate")]
        value_columns.append("adjfactor")

    raw_df, _, date_col = load_with_schema(
        parquet,
        symbol_full,
        schema_candidates=schema_candidates,
        value_columns=value_columns,
    )
    if raw_df.empty:
        raise ValueError(f"本地日线库中未找到 {symbol_full}")

    reference_dt = get_reference_datetime(end_date)
    df = normalize_ohlcv(raw_df, date_col=date_col, end_dt=reference_dt)
    if df.empty:
        raise ValueError(f"{symbol_full} 在指定日期范围内无日线数据")

    warn_daily_staleness(symbol_full, df["date"].max(), reference_dt)
    return df.tail(count).reset_index(drop=True)


def load_30min_local_data(symbol_full: str, end_date: Optional[str]) -> Tuple[pd.DataFrame, Path]:
    market_dir = get_market_db_dir()
    parquet = market_dir / "stock_30min.parquet"
    if not parquet.exists():
        return pd.DataFrame(), parquet

    raw_df, _, date_col = load_with_schema(
        parquet,
        symbol_full,
        schema_candidates=[("stockcode", "datetime"), ("code", "tradingdate")],
        value_columns=["open", "high", "low", "close", "volume", "amt"],
    )
    if raw_df.empty:
        return pd.DataFrame(), parquet

    df = normalize_ohlcv(raw_df, date_col=date_col, end_dt=get_reference_datetime(end_date))
    return df, parquet


def get_30min_fetch_window(
    count: int,
    reference_dt: pd.Timestamp,
    latest_dt: Optional[pd.Timestamp] = None,
) -> Tuple[str, str]:
    end_dt = reference_dt
    if latest_dt is not None:
        start_dt = latest_dt - pd.Timedelta(days=15)
    else:
        lookback_days = max(120, int(count / 8) * 3)
        start_dt = reference_dt - pd.Timedelta(days=lookback_days)

    start_dt = start_dt.floor("min")
    end_dt = end_dt.floor("min")
    return start_dt.strftime("%Y-%m-%d %H:%M:%S"), end_dt.strftime("%Y-%m-%d %H:%M:%S")


def normalize_30min_download(df: pd.DataFrame, symbol_full: str) -> pd.DataFrame:
    out = df.copy()
    if "tradingdate" in out.columns and "datetime" not in out.columns:
        out = out.rename(columns={"tradingdate": "datetime"})
    if "amount" in out.columns and "amt" not in out.columns:
        out = out.rename(columns={"amount": "amt"})

    out["stockcode"] = symbol_full
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"])

    for col in ["open", "high", "low", "close", "volume", "amt"]:
        if col not in out.columns:
            out[col] = 0
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out[["stockcode", "datetime", "open", "high", "low", "close", "volume", "amt"]]
    out = out.sort_values("datetime").drop_duplicates(subset=["stockcode", "datetime"], keep="last")
    return out.reset_index(drop=True)


def fetch_30min_from_jucc(
    symbol_full: str,
    count: int,
    reference_dt: pd.Timestamp,
    latest_dt: Optional[pd.Timestamp],
) -> Tuple[Optional[pd.DataFrame], str]:
    try:
        from jucc_wrapper import get_Ashare_stock_data_30min
    except Exception:
        return None, "JUCC 不可用（非内网环境）"

    start_time, end_time = get_30min_fetch_window(count, reference_dt, latest_dt)
    try:
        df = get_Ashare_stock_data_30min(symbol_full, start_time, end_time)
    except Exception as exc:
        return None, f"JUCC 拉取失败：{exc}"

    if df is None or df.empty:
        return None, "JUCC 返回空数据"
    return normalize_30min_download(df, symbol_full), "JUCC 拉取成功"


def fetch_30min_from_ifind(
    symbol_full: str,
    count: int,
    reference_dt: pd.Timestamp,
    latest_dt: Optional[pd.Timestamp],
) -> Tuple[Optional[pd.DataFrame], str]:
    try:
        from ifind_data import get_stock_30min
    except Exception as exc:
        return None, f"iFinD 模块加载失败：{exc}"

    start_time, end_time = get_30min_fetch_window(count, reference_dt, latest_dt)
    try:
        df = get_stock_30min(symbol_full, start_time, end_time)
    except Exception as exc:
        return None, f"iFinD 拉取失败：{exc}"

    if df is None or df.empty:
        return None, "iFinD 返回空数据或额度不可用"
    return normalize_30min_download(df, symbol_full), "iFinD 拉取成功"


def merge_stock_30min_parquet(parquet: Path, new_df: pd.DataFrame) -> int:
    if new_df.empty:
        return 0

    parquet.parent.mkdir(parents=True, exist_ok=True)
    if parquet.exists():
        existing = pd.read_parquet(parquet)
        if "code" in existing.columns and "stockcode" not in existing.columns:
            existing = existing.rename(columns={"code": "stockcode"})
        if "tradingdate" in existing.columns and "datetime" not in existing.columns:
            existing = existing.rename(columns={"tradingdate": "datetime"})
        if "amount" in existing.columns and "amt" not in existing.columns:
            existing = existing.rename(columns={"amount": "amt"})
    else:
        existing = pd.DataFrame(columns=["stockcode", "datetime", "open", "high", "low", "close", "volume", "amt"])

    for col in ["stockcode", "datetime", "open", "high", "low", "close", "volume", "amt"]:
        if col not in existing.columns:
            existing[col] = pd.NA

    existing = existing[["stockcode", "datetime", "open", "high", "low", "close", "volume", "amt"]].copy()
    existing["datetime"] = pd.to_datetime(existing["datetime"], errors="coerce")

    combined = pd.concat([existing, new_df], ignore_index=True)
    combined["datetime"] = pd.to_datetime(combined["datetime"], errors="coerce")
    combined = combined.dropna(subset=["datetime"])
    combined = combined.sort_values(["stockcode", "datetime"])
    combined = combined.drop_duplicates(subset=["stockcode", "datetime"], keep="last").reset_index(drop=True)

    before_rows = len(existing.dropna(subset=["datetime"]))
    tmp_path = parquet.with_suffix(".tmp")
    combined.to_parquet(tmp_path, index=False)
    tmp_path.replace(parquet)
    return max(len(combined) - before_rows, 0)


def prompt_30min_fill_choice() -> str:
    print("请选择补齐方式：")
    print("  1. iFinD（输入 1）- 消耗月度额度，请谨慎")
    print("  2. 跳过，使用现有数据（输入 2）")
    print("  3. 退出（输入 3）")

    while True:
        choice = input("请输入 1 / 2 / 3：").strip()
        if choice in {"1", "2", "3"}:
            return choice
        print("输入无效，请重新输入。")


def ensure_30min_data(symbol_full: str, count: int, end_date: Optional[str]) -> pd.DataFrame:
    if is_index_symbol(symbol_full):
        raise ValueError("30分钟当前仅支持 A 股股票（读取 stock_30min.parquet）")

    reference_dt = get_reference_datetime(end_date)
    df_local, parquet = load_30min_local_data(symbol_full, end_date=end_date)
    latest_dt = df_local["date"].max() if not df_local.empty else None
    stale_trade_days = count_trade_days_after(latest_dt, reference_dt) if latest_dt is not None else 999

    if not df_local.empty and stale_trade_days <= 2:
        return df_local.tail(count).reset_index(drop=True)

    if latest_dt is None:
        print(f"⚠️  {symbol_full} 在 stock_30min.parquet 中不存在，尝试自动补齐")
    else:
        print(
            f"⚠️  {symbol_full} 30分钟数据较旧：最新 {latest_dt.strftime('%Y-%m-%d %H:%M')}，"
            f"距今已超过 {stale_trade_days} 个交易日"
        )

    fetched_df, message = fetch_30min_from_jucc(symbol_full, count, reference_dt, latest_dt)
    if fetched_df is not None and not fetched_df.empty:
        added_rows = merge_stock_30min_parquet(parquet, fetched_df)
        print(f"✅ {message}，已写入 {added_rows} 行到 {parquet}")
        df_local, _ = load_30min_local_data(symbol_full, end_date=end_date)
        if not df_local.empty:
            return df_local.tail(count).reset_index(drop=True)
    else:
        print(message)

    choice = prompt_30min_fill_choice()
    if choice == "3":
        raise SystemExit(0)
    if choice == "1":
        fetched_df, message = fetch_30min_from_ifind(symbol_full, count, reference_dt, latest_dt)
        if fetched_df is not None and not fetched_df.empty:
            added_rows = merge_stock_30min_parquet(parquet, fetched_df)
            print(f"✅ {message}，已写入 {added_rows} 行到 {parquet}")
            df_local, _ = load_30min_local_data(symbol_full, end_date=end_date)
        else:
            print(message)

    if df_local.empty:
        raise ValueError(f"{symbol_full} 仍无可用 30 分钟数据")

    print("⚠️  使用现有 30 分钟数据继续分析")
    return df_local.tail(count).reset_index(drop=True)


def load_market_data(symbol: str, freq: str, count: int = 500, end_date: Optional[str] = None):
    print("=" * 80)
    print(f"加载市场数据 - {FREQ_LABELS[freq]}")
    print("=" * 80)

    symbol_full = normalize_symbol(symbol)
    display_name = INDEX_NAME_MAP.get(symbol_full, symbol_full.split(".")[0])
    print(f"股票代码: {symbol_full}")
    print(f"周期: {FREQ_LABELS[freq]}")
    print(f"数据目录: {get_market_db_dir()}")

    if freq == "daily":
        df = load_daily_data(symbol_full, count=count, end_date=end_date)
    elif freq == "30min":
        df = ensure_30min_data(symbol_full, count=count, end_date=end_date)
    else:
        raise ValueError(f"不支持的周期: {freq}")

    if df is None or df.empty:
        raise ValueError(f"未获取到数据: {symbol_full} ({freq})")

    print(f"✅ 成功获取 {len(df)} 条K线数据")
    print(f"时间范围: {df['date'].min().strftime('%Y-%m-%d %H:%M')} 至 {df['date'].max().strftime('%Y-%m-%d %H:%M')}")
    print()
    return df, display_name, symbol_full


def plot_complete_chart(df_raw, analyzer, stock_name, symbol, freq_label):
    """绘制完整图表。"""
    print("\n" + "=" * 80)
    print(f"生成完整图表 - {freq_label}")
    print("=" * 80)

    df_merged = analyzer.df_merged
    xaxis_title = "时间" if freq_label == "30分钟" else "日期"

    fig = make_subplots(
        rows=6,
        cols=1,
        subplot_titles=(
            f"步骤0：原始K线（{len(df_raw)}条）",
            f"步骤1：K线合并（{len(df_merged)}条）",
            f"步骤2：分型识别（{len(analyzer.fractals)}个）",
            f"步骤3：画笔（{len(analyzer.strokes)}条，幅度≥{analyzer.effective_min_amplitude * 100:.2f}%）",
            f"步骤4：识别中枢（{len(analyzer.pivots)}个中枢）",
            f"步骤5：买卖点标注（{len(analyzer.buy_sell_points)}个买卖点）",
        ),
        vertical_spacing=0.04,
        row_heights=[0.16, 0.16, 0.16, 0.16, 0.16, 0.20],
    )

    fig.add_trace(
        go.Candlestick(
            x=df_raw["date"],
            open=df_raw["open"],
            high=df_raw["high"],
            low=df_raw["low"],
            close=df_raw["close"],
            name="原始K线",
            increasing_line_color="red",
            decreasing_line_color="green",
            showlegend=False,
        ),
        row=1,
        col=1,
    )

    fig.add_trace(
        go.Candlestick(
            x=df_merged["date"],
            open=df_merged["open"],
            high=df_merged["high"],
            low=df_merged["low"],
            close=df_merged["close"],
            name="合并K线",
            increasing_line_color="red",
            decreasing_line_color="green",
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    fig.add_trace(
        go.Candlestick(
            x=df_merged["date"],
            open=df_merged["open"],
            high=df_merged["high"],
            low=df_merged["low"],
            close=df_merged["close"],
            increasing_line_color="red",
            decreasing_line_color="green",
            showlegend=False,
        ),
        row=3,
        col=1,
    )

    for fractal in analyzer.fractals:
        if 0 <= fractal["index"] < len(df_merged):
            date = df_merged.iloc[fractal["index"]]["date"]
            color = "blue" if fractal["type"] == "top" else "orange"
            symbol_name = "triangle-down" if fractal["type"] == "top" else "triangle-up"
            fig.add_trace(
                go.Scatter(
                    x=[date],
                    y=[fractal["price"]],
                    mode="markers",
                    marker=dict(size=8, color=color, symbol=symbol_name),
                    showlegend=False,
                ),
                row=3,
                col=1,
            )

    fig.add_trace(
        go.Candlestick(
            x=df_merged["date"],
            open=df_merged["open"],
            high=df_merged["high"],
            low=df_merged["low"],
            close=df_merged["close"],
            increasing_line_color="red",
            decreasing_line_color="green",
            showlegend=False,
        ),
        row=4,
        col=1,
    )

    for idx, stroke in enumerate(analyzer.strokes):
        if 0 <= stroke["start_idx"] < len(df_merged) and 0 <= stroke["end_idx"] < len(df_merged):
            start_date = df_merged.iloc[stroke["start_idx"]]["date"]
            end_date = df_merged.iloc[stroke["end_idx"]]["date"]
            color = "red" if stroke["direction"] == "up" else "green"
            fig.add_trace(
                go.Scatter(
                    x=[start_date, end_date],
                    y=[stroke["start_price"], stroke["end_price"]],
                    mode="lines+markers",
                    line=dict(color=color, width=2),
                    marker=dict(size=5),
                    showlegend=False,
                    text=f"笔{idx + 1}",
                    hovertemplate=f"笔{idx + 1}<br>%{{y:.2f}}<extra></extra>",
                ),
                row=4,
                col=1,
            )

    fig.add_trace(
        go.Candlestick(
            x=df_merged["date"],
            open=df_merged["open"],
            high=df_merged["high"],
            low=df_merged["low"],
            close=df_merged["close"],
            increasing_line_color="red",
            decreasing_line_color="green",
            showlegend=False,
        ),
        row=5,
        col=1,
    )

    for stroke in analyzer.strokes:
        if 0 <= stroke["start_idx"] < len(df_merged) and 0 <= stroke["end_idx"] < len(df_merged):
            start_date = df_merged.iloc[stroke["start_idx"]]["date"]
            end_date = df_merged.iloc[stroke["end_idx"]]["date"]
            color = "red" if stroke["direction"] == "up" else "green"
            fig.add_trace(
                go.Scatter(
                    x=[start_date, end_date],
                    y=[stroke["start_price"], stroke["end_price"]],
                    mode="lines+markers",
                    line=dict(color=color, width=2),
                    marker=dict(size=5),
                    showlegend=False,
                ),
                row=5,
                col=1,
            )

    for idx, pivot in enumerate(analyzer.pivots):
        if 0 <= pivot["start_idx"] < len(df_merged) and 0 <= pivot["end_idx"] < len(df_merged):
            start_date = pivot["start_date"]
            end_date = pivot["end_date"]
            zd = pivot["low"]
            zg = pivot["high"]

            fig.add_shape(
                type="rect",
                x0=start_date,
                x1=end_date,
                y0=zd,
                y1=zg,
                fillcolor="rgba(255, 165, 0, 0.2)",
                line=dict(color="orange", width=0),
                row=5,
                col=1,
            )
            fig.add_shape(
                type="line",
                x0=start_date,
                x1=end_date,
                y0=zd,
                y1=zd,
                line=dict(color="red", width=2, dash="solid"),
                row=5,
                col=1,
            )
            fig.add_shape(
                type="line",
                x0=start_date,
                x1=end_date,
                y0=zg,
                y1=zg,
                line=dict(color="green", width=2, dash="solid"),
                row=5,
                col=1,
            )

            mid_date = pd.Timestamp((start_date.timestamp() + end_date.timestamp()) / 2, unit="s")
            stroke_indices = ",".join(str(i + 1) for i in pivot["stroke_indices"])
            fig.add_annotation(
                x=mid_date,
                y=zg,
                text=f"中枢{idx + 1}({pivot['stroke_count']}笔: {stroke_indices})",
                showarrow=False,
                font=dict(size=9, color="orange", family="Arial"),
                bgcolor="rgba(255,255,255,0.95)",
                bordercolor="orange",
                borderwidth=1.5,
                xanchor="center",
                yanchor="bottom",
                row=5,
                col=1,
            )

    fig.add_trace(
        go.Candlestick(
            x=df_merged["date"],
            open=df_merged["open"],
            high=df_merged["high"],
            low=df_merged["low"],
            close=df_merged["close"],
            name="K线",
            showlegend=False,
        ),
        row=6,
        col=1,
    )

    for stroke in analyzer.strokes:
        if 0 <= stroke["start_idx"] < len(df_merged) and 0 <= stroke["end_idx"] < len(df_merged):
            start_date = df_merged.iloc[stroke["start_idx"]]["date"]
            end_date = df_merged.iloc[stroke["end_idx"]]["date"]
            color = "red" if stroke["direction"] == "up" else "green"
            fig.add_trace(
                go.Scatter(
                    x=[start_date, end_date],
                    y=[stroke["start_price"], stroke["end_price"]],
                    mode="lines+markers",
                    line=dict(color=color, width=2),
                    marker=dict(size=5),
                    showlegend=False,
                ),
                row=6,
                col=1,
            )

    for pivot in analyzer.pivots:
        if 0 <= pivot["start_idx"] < len(df_merged) and 0 <= pivot["end_idx"] < len(df_merged):
            start_date = pivot["start_date"]
            end_date = pivot["end_date"]
            zd = pivot["low"]
            zg = pivot["high"]

            fig.add_shape(
                type="rect",
                x0=start_date,
                x1=end_date,
                y0=zd,
                y1=zg,
                fillcolor="rgba(255, 165, 0, 0.2)",
                line=dict(color="orange", width=0),
                row=6,
                col=1,
            )
            fig.add_shape(
                type="line",
                x0=start_date,
                x1=end_date,
                y0=zd,
                y1=zd,
                line=dict(color="red", width=2, dash="solid"),
                row=6,
                col=1,
            )
            fig.add_shape(
                type="line",
                x0=start_date,
                x1=end_date,
                y0=zg,
                y1=zg,
                line=dict(color="green", width=2, dash="solid"),
                row=6,
                col=1,
            )

    for point in analyzer.buy_sell_points:
        point_date = point["date"]
        point_price = point["price"]
        if point["point_type"] == "buy":
            color = "red"
            symbol_mark = "▲"
            y_offset = -80
        else:
            color = "green"
            symbol_mark = "▼"
            y_offset = 80

        fig.add_annotation(
            x=point_date,
            y=point_price,
            text=f"{symbol_mark} {point['type']}",
            showarrow=True,
            arrowhead=2,
            arrowsize=1,
            arrowwidth=2,
            arrowcolor=color,
            ax=0,
            ay=y_offset,
            font=dict(size=10, color=color, family="Arial Black"),
            bgcolor="rgba(255,255,255,0.95)",
            bordercolor=color,
            borderwidth=2,
            row=6,
            col=1,
        )

    if analyzer.trend_type:
        trend_summary = (
            analyzer.trend_type.get("summary")
            or analyzer.trend_type.get("type")
            or analyzer.trend_type.get("description")
            or "未知"
        )
        fig.add_annotation(
            x=0.5,
            y=1.05,
            xref="paper",
            yref="paper",
            text=f"走势类型: {trend_summary}",
            showarrow=False,
            font=dict(size=14, color="blue"),
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="blue",
            borderwidth=2,
        )

    fig.update_layout(
        title=f"{stock_name}({symbol}) - 缠论完整分析v5 - {freq_label}",
        height=1800,
        template="plotly_white",
        showlegend=False,
        xaxis_rangeslider_visible=False,
        xaxis2_rangeslider_visible=False,
        xaxis3_rangeslider_visible=False,
        xaxis4_rangeslider_visible=False,
        xaxis5_rangeslider_visible=False,
        xaxis6_rangeslider_visible=False,
    )

    for row in range(1, 7):
        fig.update_yaxes(title_text="价格", row=row, col=1)
    fig.update_xaxes(title_text=xaxis_title, row=6, col=1)
    return fig


def format_point_date(ts: pd.Timestamp, freq: str) -> str:
    fmt = "%Y-%m-%d %H:%M" if freq == "30min" else "%Y-%m-%d"
    return pd.Timestamp(ts).strftime(fmt)


def print_analysis_summary(analyzer: ChanCoreV5, freq: str) -> None:
    label = FREQ_LABELS[freq]
    trend_summary = "未知"
    if analyzer.trend_type:
        trend_summary = (
            analyzer.trend_type.get("summary")
            or analyzer.trend_type.get("type")
            or analyzer.trend_type.get("description")
            or "未知"
        )

    print(
        f"【{label}】合并K线={len(analyzer.df_merged)} "
        f"笔={len(analyzer.strokes)} 中枢={len(analyzer.pivots)} 走势={trend_summary}"
    )

    if analyzer.pivots:
        pivot = analyzer.pivots[-1]
        print(
            f"  最新中枢：{format_point_date(pivot['start_date'], freq)}~"
            f"{format_point_date(pivot['end_date'], freq)}  "
            f"ZG={pivot['high']:.2f} ZD={pivot['low']:.2f}"
        )
    else:
        print("  最新中枢：无")

    if analyzer.buy_sell_points:
        point = analyzer.buy_sell_points[-1]
        print(
            f"  最新买卖点：{point['type']} "
            f"{format_point_date(point['date'], freq)} {point['price']:.2f}"
        )
    else:
        print("  最新买卖点：无")
    print()


def build_output_path(output_arg: Optional[str], symbol: str, freq: str, multi_freq: bool) -> str:
    os.makedirs(output_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if output_arg:
        target = Path(output_arg)
        if multi_freq:
            suffix = target.suffix or ".html"
            stem = target.stem if target.suffix else target.name
            return str(target.with_name(f"{stem}_{freq}{suffix}"))
        return str(target)

    return os.path.join(output_dir, f"缠论分析v5_{symbol}_{freq}_{timestamp}.html")


def analyze_one_frequency(symbol: str, freq: str, count: int, end_date: Optional[str], output_arg: Optional[str], multi_freq: bool):
    df, stock_name, symbol_full = load_market_data(symbol, freq=freq, count=count, end_date=end_date)

    print("=" * 80)
    print(f"执行缠论分析v5 - {FREQ_LABELS[freq]}")
    print("=" * 80)
    analyzer = ChanCoreV5(
        df,
        min_amplitude=0.005,
        pivot_min_amplitude=0.02,
        pivot_min_strokes=3,
        pivot_max_strokes=9,
        dynamic_min_amplitude=True,
        symbol=symbol_full,
        instrument_type="auto",
    )
    analyzer.analyze()
    print_analysis_summary(analyzer, freq=freq)

    fig = plot_complete_chart(df, analyzer, stock_name, symbol_full, FREQ_LABELS[freq])
    output_file = build_output_path(output_arg, symbol, freq, multi_freq)
    fig.write_html(output_file)
    print(f"✅ {FREQ_LABELS[freq]}图表已保存: {output_file}")
    print()


def main():
    parser = argparse.ArgumentParser(description="缠论完整分析v5（支持日线 / 30分钟）")
    parser.add_argument("symbol", type=str, help="股票代码，如 300308 或 300308.SZ")
    parser.add_argument("--count", type=int, default=500, help="K线数量（默认：500）")
    parser.add_argument("--end-date", type=str, default=None, help="截止日期 YYYYMMDD（默认：今日）")
    parser.add_argument("--output", type=str, default=None, help="输出文件名（多周期时自动追加后缀）")
    parser.add_argument(
        "--freq",
        nargs="+",
        default=["daily"],
        choices=["daily", "30min"],
        help="分析周期，可选 daily / 30min，支持同时传多个（默认：daily）",
    )
    args = parser.parse_args()

    freqs = list(dict.fromkeys(args.freq))
    for freq in freqs:
        analyze_one_frequency(
            symbol=args.symbol,
            freq=freq,
            count=args.count,
            end_date=args.end_date,
            output_arg=args.output,
            multi_freq=len(freqs) > 1,
        )

    print("=" * 80)
    print("✅ 分析完成")
    print("=" * 80)


if __name__ == "__main__":
    main()
