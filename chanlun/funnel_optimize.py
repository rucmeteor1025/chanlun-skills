# -*- coding: utf-8 -*-
"""
三层漏斗参数敏感性分析（防过拟合）

流程：
1. 加载 funnel_backtest.py 采集的原始事件（.h5，宽松门限，指标已记录）
2. 对参数网格逐组合过滤事件 → 组合模拟 → 汇总指标
3. 训练/验证切分：按信号日切两段，分别报告，检验参数稳健性

用法：
    python3 funnel_optimize.py --events 输出/funnel_events_20250801_20260718.h5 \
        --start 2025-08-01 --end 2026-07-18 --split 2026-03-01
"""
import argparse
import itertools
import os
import sys
from typing import Dict, List

import pandas as pd

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, CURRENT_DIR)

from funnel_backtest import FunnelBacktest, DEFAULT_CONFIG  # noqa: E402


def run_grid(bt: FunnelBacktest, index_events, sector_events, stock_signals,
             grid: Dict[str, List], split: str = None) -> pd.DataFrame:
    keys = sorted(grid.keys())
    rows = []
    for combo in itertools.product(*(grid[k] for k in keys)):
        filters = dict(zip(keys, combo))
        tag = ",".join(f"{k}={v}" for k, v in filters.items())
        for seg_name, seg_signals in _segments(stock_signals, split):
            res = bt.simulate(seg_signals, sector_events, index_events, filters=filters)
            row = {"segment": seg_name, "params": tag, **filters}
            for m in ["total_return", "max_drawdown", "n_trades", "win_rate",
                      "avg_return", "avg_hold_days"]:
                row[m] = res.get(m)
            rows.append(row)
            print(f"[{seg_name}] {tag} -> 交易{row.get('n_trades')} "
                  f"收益{row.get('total_return')} 胜率{row.get('win_rate')} 回撤{row.get('max_drawdown')}",
                  flush=True)
    return pd.DataFrame(rows)


def _segments(signals: List[Dict], split: str):
    if not split:
        yield "全区间", signals
        return
    sp = pd.Timestamp(split)
    train = [s for s in signals if pd.Timestamp(s["date"]) < sp]
    valid = [s for s in signals if pd.Timestamp(s["date"]) >= sp]
    yield "训练段", train
    yield "验证段", valid


def main():
    parser = argparse.ArgumentParser(description="三层漏斗参数敏感性")
    parser.add_argument("--events", required=True)
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--start", default="2025-08-01")
    parser.add_argument("--end", default="2026-07-18")
    parser.add_argument("--split", default=None, help="训练/验证切分日 YYYY-MM-DD")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    index_events, sector_events, stock_signals = FunnelBacktest.load_events(args.events)
    print(f"事件加载: 指数一买 {len(index_events)}，板块二买 {len(sector_events)}，个股三买 {len(stock_signals)}")

    bt = FunnelBacktest(args.config, args.start, args.end)
    bt.prepare(download=False)

    grid = {
        "min_pivots": [1, 2],
        "divergence_rate": [0.8, 0.9, 1.0],
        "max_retrace_ratio": [0.5, 0.618, 0.786, 1.0],
        "macd_confirm": [True, False],
    }
    df = run_grid(bt, index_events, sector_events, stock_signals, grid, split=args.split)
    out = args.out or os.path.join(CURRENT_DIR, "输出", "funnel_param_sensitivity.csv")
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\n结果已保存: {out}")

    # 汇总：按参数组合排前列（全区间或有交易的段）
    show = df.dropna(subset=["n_trades"])
    show = show[show["n_trades"] > 0]
    if len(show):
        print("\n=== 有交易的组合（按收益排序）===")
        print(show.sort_values("total_return", ascending=False).head(15).to_string(index=False))
    else:
        print("\n所有组合均无交易。")


if __name__ == "__main__":
    main()
