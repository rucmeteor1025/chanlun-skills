#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
腾讯 00700.HK 30分钟缠论分析
运行方式：python 投研系统/工具脚本/技术/缠论/专项脚本/tencent_chan_30m.py
"""
from __future__ import annotations
import sys, os, time, warnings
warnings.filterwarnings("ignore")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import akshare as ak
import pandas as pd
from chan_core_v5t import ChanCoreV5T

# ── 1. 拉取数据 ────────────────────────────────────────────────────────────────
def fetch_tencent_30m(retries: int = 3, delay: int = 10) -> pd.DataFrame:
    for i in range(retries):
        try:
            df_raw = ak.stock_hk_hist_min_em(
                symbol="00700", period="30", adjust="qfq",
                start_date="2026-01-02 09:30:00",
                end_date="2099-01-01 00:00:00",
            )
            # 列名顺序: 时间,开盘,收盘,最高,最低,涨跌幅,涨跌额,成交量,成交额,振幅,换手率
            df_raw.columns = [
                "date", "open", "close", "high", "low",
                "pct", "chg", "volume", "amount", "amplitude", "turnover"
            ]
            df = df_raw[["date", "open", "high", "low", "close", "volume"]].copy()
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)
            print(f"[OK] 获取腾讯30min数据: {len(df)}条 "
                  f"({df['date'].iloc[0].date()} → {df['date'].iloc[-1].date()})")
            return df
        except Exception as e:
            print(f"[尝试{i+1}/{retries}] 失败: {e}")
            if i < retries - 1:
                print(f"  等待 {delay}s 后重试...")
                time.sleep(delay)
    return None

# ── 2. 缠论分析 ────────────────────────────────────────────────────────────────
def run_chan(df: pd.DataFrame) -> None:
    chan = ChanCoreV5T(df, symbol="00700.HK", instrument_type="stock")
    chan.analyze()
    summary = chan.get_summary()
    last_close = df["close"].iloc[-1]
    last_date = df["date"].iloc[-1]

    print()
    print("=" * 55)
    print(f"  腾讯控股 00700.HK  30分钟缠论分析")
    print(f"  最新: {last_date}  收盘: {last_close:.1f} HKD")
    print("=" * 55)
    print(f"  K线: {len(df)} → 合并后: {len(chan.df_merged)}")
    print(f"  分型: {summary['total_fractals']}  笔: {summary['total_strokes']}")
    print(f"  中枢: {summary['total_pivots']}  买卖点: {summary['total_buy_sell_points']}")
    print(f"  走势类型: {summary['trend_type']}")

    # 最近10笔
    if chan.strokes:
        print()
        print("── 最近 10 笔 ──────────────────────────────────")
        for s in chan.strokes[-10:]:
            d = "↑" if s["direction"] == "up" else "↓"
            print(f"  {d} {s['start_date'].strftime('%m/%d %H:%M')} "
                  f"{s['start_price']:.1f} → "
                  f"{s['end_date'].strftime('%m/%d %H:%M')} "
                  f"{s['end_price']:.1f}  "
                  f"({s['amplitude']*100:+.1f}%)")

    # 所有中枢
    if chan.pivots:
        print()
        print("── 中枢列表 ─────────────────────────────────────")
        for i, p in enumerate(chan.pivots, 1):
            width = (p["high"] - p["low"]) / p["low"] * 100
            print(f"  [{i}] {p['start_date'].strftime('%m/%d')}~{p['end_date'].strftime('%m/%d')} "
                  f"ZG={p['high']:.1f} ZD={p['low']:.1f} 宽={width:.1f}%")

    # 买卖点
    if chan.buy_sell_points:
        print()
        print("── 买卖点 ───────────────────────────────────────")
        for bs in chan.buy_sell_points:
            print(f"  {bs['type']:4s} | {bs['date'].strftime('%m/%d %H:%M')} "
                  f"| 价格: {bs['price']:.1f}")

    # 简要判断
    print()
    print("── 当前状态判断 ─────────────────────────────────")
    if chan.strokes:
        last_s = chan.strokes[-1]
        direction = "上涨笔" if last_s["direction"] == "up" else "下跌笔"
        print(f"  当前所在笔: {direction}  起点={last_s['start_price']:.1f}  "
              f"当前={last_close:.1f}")
        amp = (last_close - last_s["start_price"]) / last_s["start_price"] * 100
        print(f"  笔内涨跌: {amp:+.1f}%")
    if chan.pivots:
        last_p = chan.pivots[-1]
        if last_close > last_p["high"]:
            print(f"  当前价在最近中枢(ZG={last_p['high']:.1f})之上 → 多头趋势延伸")
        elif last_close < last_p["low"]:
            print(f"  当前价在最近中枢(ZD={last_p['low']:.1f})之下 → 空头趋势延伸")
        else:
            print(f"  当前价在最近中枢内 ZD={last_p['low']:.1f}~ZG={last_p['high']:.1f}")
    print("=" * 55)


if __name__ == "__main__":
    df = fetch_tencent_30m(retries=3, delay=15)
    if df is not None and len(df) >= 20:
        run_chan(df)
    else:
        print("[ERROR] 数据获取失败，请稍后重试（东财API限流约5~10分钟解封）")
