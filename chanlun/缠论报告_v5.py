#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论固定报告 v5

输出字段：
- 是否可交易
- 风险等级
- 建议观察位
"""
import argparse
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../../.."))
tech_dir = os.path.join(project_root, "投研系统/工具脚本/技术")
data_dir = os.path.join(project_root, "投研系统/工具脚本/数据")
sys.path.insert(0, tech_dir)
sys.path.insert(0, data_dir)

from chan_core_v5 import ChanCoreV5
from data_api import get_index_daily_with_indicators, get_stock_daily_with_indicators


def _normalize_symbol(symbol: str, kind: str) -> str:
    s = symbol.strip().upper()
    if "." in s:
        return s
    if kind == "index":
        if s.startswith("399"):
            return f"{s}.SZ"
        return f"{s}.SH"
    if s.startswith("6"):
        return f"{s}.SH"
    return f"{s}.SZ"


def _load_data(symbol: str, kind: str, start_date: str, end_date: str) -> pd.DataFrame:
    if kind == "index":
        return get_index_daily_with_indicators(symbol, start_date, end_date)
    return get_stock_daily_with_indicators(symbol, start_date, end_date)


def _trend_text(trend_type: Optional[Dict]) -> str:
    if not trend_type:
        return "未知"
    if "summary" in trend_type:
        return str(trend_type["summary"])
    if "type" in trend_type:
        return str(trend_type["type"])
    return "未知"


def _latest_signal(points: List[Dict]) -> Optional[Dict]:
    if not points:
        return None
    return max(points, key=lambda p: pd.to_datetime(p["date"]))


def _build_trade_view(
    analyzer: ChanCoreV5,
    trend_text: str,
    latest_signal: Optional[Dict],
    last_close: float,
) -> Dict[str, str]:
    has_daily_pivot = len(analyzer.pivots) > 0
    last_pivot = analyzer.pivots[-1] if has_daily_pivot else None
    is_down = "下跌" in trend_text
    is_up = "上涨" in trend_text
    recent_buy_signal = bool(latest_signal and latest_signal.get("point_type") == "buy")

    if not has_daily_pivot or is_down:
        tradable = "否"
    elif recent_buy_signal and is_up:
        tradable = "是"
    else:
        tradable = "观察"

    if is_down:
        risk = "高"
    elif recent_buy_signal and is_up:
        risk = "中"
    elif has_daily_pivot:
        risk = "中高"
    else:
        risk = "高"

    if last_pivot:
        zd = float(last_pivot["low"])
        zg = float(last_pivot["high"])
        if last_close > zg:
            view = f"回踩不破 ZG={zg:.2f} 可继续观察；强支撑 ZD={zd:.2f}"
        elif last_close < zd:
            view = f"先观察重回 ZD={zd:.2f}；上沿压力 ZG={zg:.2f}"
        else:
            view = f"区间震荡，关注 ZD={zd:.2f} / ZG={zg:.2f}"
    else:
        view = "无日线中枢，先观察次级别结构与量能"

    if latest_signal:
        signal_name = str(latest_signal.get("type", ""))
        signal_price = float(latest_signal.get("price", last_close))
        signal_text = f"{signal_name}@{signal_price:.2f}"
    else:
        signal_text = "无"

    return {
        "latest_signal": signal_text,
        "tradable": tradable,
        "risk": risk,
        "observation": view,
    }


def analyze_symbol(
    symbol: str,
    kind: str,
    name: str,
    start_date: str,
    end_date: str,
    count: int,
) -> Dict:
    code = _normalize_symbol(symbol, kind)
    df = _load_data(code, kind, start_date, end_date)
    if df is None or len(df) == 0:
        return {
            "name": name,
            "symbol": code,
            "kind": kind,
            "error": "未获取到数据",
        }

    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values("date").tail(count).reset_index(drop=True)

    analyzer = ChanCoreV5(
        df=df,
        min_amplitude=0.005,
        dynamic_min_amplitude=True,
        dynamic_pivot_rules=True,
        symbol=code,
        instrument_type=("index" if kind == "index" else "auto"),
    )
    analyzer.analyze()

    trend = _trend_text(analyzer.trend_type)
    last_close = float(df["close"].iloc[-1])
    latest_signal = _latest_signal(analyzer.buy_sell_points)
    trade_view = _build_trade_view(analyzer, trend, latest_signal, last_close)

    return {
        "name": name,
        "symbol": code,
        "kind": kind,
        "kline_count": len(df),
        "date_range": f"{df['date'].iloc[0].strftime('%Y-%m-%d')}~{df['date'].iloc[-1].strftime('%Y-%m-%d')}",
        "trend": trend,
        "strokes": len(analyzer.strokes),
        "daily_pivots": len(analyzer.pivots),
        "sub_pivots": len(analyzer.sub_level_pivots),
        "min_amp": analyzer.effective_min_amplitude,
        "pivot_daily_amp": analyzer.effective_pivot_rules.get("daily_min_amplitude"),
        "pivot_daily_strokes": analyzer.effective_pivot_rules.get("daily_min_strokes"),
        "last_close": last_close,
        "latest_signal": trade_view["latest_signal"],
        "tradable": trade_view["tradable"],
        "risk": trade_view["risk"],
        "observation": trade_view["observation"],
    }


def print_report(rows: List[Dict]) -> None:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("=" * 120)
    print(f"缠论固定报告 v5 | 生成时间: {now}")
    print("=" * 120)

    ok_rows = [r for r in rows if "error" not in r]
    err_rows = [r for r in rows if "error" in r]

    if ok_rows:
        df = pd.DataFrame(ok_rows)
        out = df[
            [
                "name",
                "symbol",
                "trend",
                "daily_pivots",
                "sub_pivots",
                "latest_signal",
                "tradable",
                "risk",
                "observation",
            ]
        ].rename(
            columns={
                "name": "标的",
                "symbol": "代码",
                "trend": "走势",
                "daily_pivots": "日线中枢",
                "sub_pivots": "次级别中枢",
                "latest_signal": "最新信号",
                "tradable": "是否可交易",
                "risk": "风险等级",
                "observation": "建议观察位",
            }
        )
        print(out.to_string(index=False))
        print()
        for row in ok_rows:
            print(
                f"- {row['name']}({row['symbol']}): K线{row['kline_count']} "
                f"笔{row['strokes']} 日线中枢{row['daily_pivots']} 次级别{row['sub_pivots']} "
                f"动态笔阈值{row['min_amp']*100:.3f}% 日线中枢阈值{row['pivot_daily_amp']*100:.3f}%/{row['pivot_daily_strokes']}笔"
            )

    if err_rows:
        print("\n数据异常:")
        for row in err_rows:
            print(f"- {row['name']}({row['symbol']}): {row['error']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="缠论固定报告 v5")
    parser.add_argument("--start-date", default="20240101", help="开始日期 YYYYMMDD")
    parser.add_argument("--end-date", default=datetime.now().strftime("%Y%m%d"), help="结束日期 YYYYMMDD")
    parser.add_argument("--count", type=int, default=520, help="分析K线条数")
    args = parser.parse_args()

    targets = [
        {"name": "灿芯股份", "symbol": "688691.SH", "kind": "stock"},
        {"name": "上证指数", "symbol": "000001.SH", "kind": "index"},
    ]

    rows = [
        analyze_symbol(
            symbol=t["symbol"],
            kind=t["kind"],
            name=t["name"],
            start_date=args.start_date,
            end_date=args.end_date,
            count=args.count,
        )
        for t in targets
    ]
    print_report(rows)


if __name__ == "__main__":
    main()
