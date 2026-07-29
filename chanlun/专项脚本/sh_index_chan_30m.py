#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
宽基指数 30分钟缠论分析（读本地 index_30min.parquet）
数据来源：JUCC idx_min_bars 15min → resample 30min，每日16:30自动更新

可用代码：
  000016.SH  上证50
  000300.SH  沪深300
  000905.SH  中证500
  000852.SH  中证1000
  000688.SH  科创50
  399006.SZ  创业板指

运行方式：
  python 投研系统/工具脚本/技术/缠论/专项脚本/sh_index_chan_30m.py
  python 投研系统/工具脚本/技术/缠论/专项脚本/sh_index_chan_30m.py 000300.SH
"""
from __future__ import annotations
import sys, os, io, warnings
warnings.filterwarnings("ignore")

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pandas as pd
from pathlib import Path
from chan_core_v5t import ChanCoreV5T

HERE     = Path(__file__).resolve().parent
DATA_DIR = HERE.parent.parent.parent.parent / "本地数据"
PARQUET  = DATA_DIR / "index_30min.parquet"

INDEX_NAMES = {
    "000016.SH": "上证50",
    "000300.SH": "沪深300",
    "000905.SH": "中证500",
    "000852.SH": "中证1000",
    "000688.SH": "科创50",
    "399006.SZ": "创业板指",
}


def load_local(code: str) -> pd.DataFrame:
    if not PARQUET.exists():
        print(f"[ERROR] 本地数据不存在: {PARQUET}")
        print("  请先运行: python 投研系统/工具脚本/数据/update_market.py --module local --table index30min")
        return None

    df_all = pd.read_parquet(PARQUET)
    df = df_all[df_all["code"] == code].copy()

    if df.empty:
        available = df_all["code"].unique().tolist()
        print(f"[ERROR] {code} 不在本地数据中")
        print(f"  可用代码: {available}")
        return None

    df = df.rename(columns={"tradingdate": "date"})
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").reset_index(drop=True)
    print(f"[OK] 本地数据: {code}  {len(df)}条  "
          f"({df['date'].iloc[0].strftime('%Y-%m-%d %H:%M')} → "
          f"{df['date'].iloc[-1].strftime('%Y-%m-%d %H:%M')})")
    return df


def run_chan(df: pd.DataFrame, code: str) -> None:
    name = INDEX_NAMES.get(code, code)
    chan = ChanCoreV5T(df, symbol=code, instrument_type="index")
    chan.analyze()
    summary = chan.get_summary()
    last_close = df["close"].iloc[-1]
    last_date  = df["date"].iloc[-1]

    print()
    print("=" * 60)
    print(f"  {name} {code}  30分钟缠论分析")
    print(f"  最新: {last_date.strftime('%Y-%m-%d %H:%M')}  收盘: {last_close:.2f}")
    print("=" * 60)
    print(f"  K线: {len(df)} → 合并后: {len(chan.df_merged)}")
    print(f"  分型: {summary['total_fractals']}  笔: {summary['total_strokes']}")
    print(f"  中枢: {summary['total_pivots']}  买卖点: {summary['total_buy_sell_points']}")
    print(f"  走势类型: {summary['trend_type']}")

    if chan.strokes:
        print()
        print("── 最近 10 笔 ──────────────────────────────────────────")
        for s in chan.strokes[-10:]:
            d = "↑" if s["direction"] == "up" else "↓"
            print(f"  {d} {s['start_date'].strftime('%m/%d %H:%M')} "
                  f"{s['start_price']:.2f} → "
                  f"{s['end_date'].strftime('%m/%d %H:%M')} "
                  f"{s['end_price']:.2f}  "
                  f"({s['amplitude']*100:+.2f}%)")

    if chan.pivots:
        print()
        print("── 中枢列表 ─────────────────────────────────────────────")
        for i, p in enumerate(chan.pivots, 1):
            width = (p["high"] - p["low"]) / p["low"] * 100
            print(f"  [{i}] {p['start_date'].strftime('%m/%d %H:%M')}~"
                  f"{p['end_date'].strftime('%m/%d %H:%M')} "
                  f"ZG={p['high']:.2f} ZD={p['low']:.2f} 宽={width:.2f}%")

    if chan.buy_sell_points:
        print()
        print("── 买卖点 ───────────────────────────────────────────────")
        for bs in chan.buy_sell_points:
            print(f"  {bs['type']:4s} | {bs['date'].strftime('%m/%d %H:%M')} "
                  f"| 价格: {bs['price']:.2f}")

    print()
    print("── 当前状态判断 ─────────────────────────────────────────")
    if chan.strokes:
        last_s = chan.strokes[-1]
        direction = "上涨笔" if last_s["direction"] == "up" else "下跌笔"
        print(f"  当前所在笔: {direction}  起点={last_s['start_price']:.2f}  当前={last_close:.2f}")
        amp = (last_close - last_s["start_price"]) / last_s["start_price"] * 100
        print(f"  笔内涨跌: {amp:+.2f}%")
    if chan.pivots:
        last_p = chan.pivots[-1]
        if last_close > last_p["high"]:
            print(f"  当前价在最近中枢(ZG={last_p['high']:.2f})之上 → 多头趋势延伸")
        elif last_close < last_p["low"]:
            print(f"  当前价在最近中枢(ZD={last_p['low']:.2f})之下 → 空头趋势延伸")
        else:
            print(f"  当前价在最近中枢内 ZD={last_p['low']:.2f}~ZG={last_p['high']:.2f}")
    print("=" * 60)


if __name__ == "__main__":
    code = sys.argv[1] if len(sys.argv) > 1 else "000300.SH"
    df = load_local(code)
    if df is not None and len(df) >= 20:
        run_chan(df, code)
    else:
        print("[ERROR] 数据不足，无法分析")
