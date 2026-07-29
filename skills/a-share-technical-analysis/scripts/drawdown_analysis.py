#!/usr/bin/env python3
"""
大波段回撤分析 — 通用脚本
对 ETF/个股做「每轮大涨后高点→低点的回撤」量化分析。

用法:
  python3 drawdown_analysis.py --csv /tmp/data.csv --name "北方华创" --code 002371
  python3 drawdown_analysis.py --batch  # 内置半导体5+1批量

数据格式要求:
  CSV 至少包含列: 日期, 开盘, 收盘, 最高, 最低
  列名兼容大小写和中英文 (日期/date, 收盘/close, 最高/high, 最低/low)
"""
import pandas as pd
import numpy as np
from datetime import timedelta
import argparse, sys

def find_major_swings(high, low, close, dates, n, min_gain_pct=20, min_drawdown_pct=8):
    """自动找大波段：涨幅>min_gain_pct 的上涨 + 随后回撤>min_drawdown_pct"""
    swings = []
    peak_idx = 0
    peak_val = high[0]
    for i in range(1, n):
        if high[i] >= peak_val:
            peak_val = high[i]
            peak_idx = i
        elif close[i] < peak_val * (1 - min_drawdown_pct/100):
            dd_low = low[peak_idx]
            dd_low_idx = peak_idx
            j = peak_idx + 1
            while j < n:
                if low[j] <= dd_low:
                    dd_low = low[j]
                    dd_low_idx = j
                if high[j] > peak_val:
                    break
                j += 1
            start_idx = 0
            if peak_idx > 5:
                start_idx = np.argmin(low[:peak_idx])
            gain = (peak_val / low[start_idx] - 1) * 100 if low[start_idx] > 0 else 0
            dd_pct = (peak_val - dd_low) / peak_val * 100
            if gain >= min_gain_pct:
                swings.append({
                    'gain': gain, 'dd_pct': dd_pct,
                    'start_p': round(low[start_idx], 2), 'peak_p': round(peak_val, 2),
                    'dd_p': round(dd_low, 2),
                    'start_d': pd.Timestamp(dates[start_idx]).strftime('%Y-%m-%d'),
                    'peak_d': pd.Timestamp(dates[peak_idx]).strftime('%Y-%m-%d'),
                    'dd_d': pd.Timestamp(dates[dd_low_idx]).strftime('%Y-%m-%d'),
                    'up_days': peak_idx - start_idx, 'dd_days': dd_low_idx - peak_idx,
                    'up_cal': (pd.Timestamp(dates[peak_idx]) - pd.Timestamp(dates[start_idx])).days,
                    'dd_cal': (pd.Timestamp(dates[dd_low_idx]) - pd.Timestamp(dates[peak_idx])).days,
                    'retrace': (peak_val - dd_low) / (peak_val - low[start_idx]) * 100 if (peak_val - low[start_idx]) > 0 else 0,
                })
            peak_idx = dd_low_idx
            peak_val = high[dd_low_idx]
    return swings

def analyze(hist, name, code):
    date_col = '日期' if '日期' in hist.columns else 'date'
    close_col = '收盘' if '收盘' in hist.columns else 'close'
    high_col = '最高' if '最高' in hist.columns else 'high'
    low_col = '最低' if '最低' in hist.columns else 'low'
    hist[date_col] = pd.to_datetime(hist[date_col])
    hist = hist.sort_values(date_col).reset_index(drop=True)
    high = hist[high_col].values; low = hist[low_col].values
    close = hist[close_col].values; dates = hist[date_col].values; n = len(hist)
    golden = [0.236, 0.382, 0.5, 0.618, 0.786, 0.886]

    swings = find_major_swings(high, low, close, dates, n)
    cur_peak_idx = np.argmax(high); cur_peak = high[cur_peak_idx]
    cur_peak_date = pd.Timestamp(dates[cur_peak_idx])
    post_peak = hist.iloc[cur_peak_idx:]
    cur_low_idx_in = post_peak[low_col].idxmin()
    cur_low = post_peak.loc[cur_low_idx_in, low_col]
    cur_low_date = pd.Timestamp(hist.loc[cur_low_idx_in, date_col])
    cur_close = close[-1]
    cur_dd = (cur_peak - cur_low) / cur_peak * 100
    cur_dd_close = (cur_peak - cur_close) / cur_peak * 100
    cur_dd_days = len(post_peak) - 1
    cur_start = low[np.argmin(low[:cur_peak_idx])] if cur_peak_idx > 5 else low[0]

    print(f"\n{'█'*100}\n█ {code} {name} — 大波段回撤分析\n{'█'*100}")
    print(f"\n数据范围: {hist[date_col].iloc[0].strftime('%Y-%m-%d')} ~ {hist[date_col].iloc[-1].strftime('%Y-%m-%d')}  ({n}个交易日)")
    print(f"历史最高: {cur_peak}  ({cur_peak_date.strftime('%Y-%m-%d')})")
    print(f"最新收盘: {cur_close}  ({hist[date_col].iloc[-1].strftime('%Y-%m-%d')})")

    if not swings:
        print("\n⚠ 未找到满足条件的大波段（涨幅>20%+回撤>8%），降低门槛重试...")
        swings = find_major_swings(high, low, close, dates, n, min_gain_pct=10, min_drawdown_pct=5)

    if swings:
        dds = [s['dd_pct'] for s in swings]; dd_days_list = [s['dd_days'] for s in swings]
        dd_cals = [s['dd_cal'] for s in swings]; gains = [s['gain'] for s in swings]
        retraces = [s['retrace'] for s in swings]
        print(f"\n找到 {len(swings)} 个大波段\n")
        print(f"{'#':<3} {'上涨段':<28} {'涨幅%':>7} {'回撤段':<28} {'回撤%':>7} {'交易日':>6} {'自然日':>6} {'回撤/涨幅':>8}")
        print("-" * 105)
        for i, s in enumerate(swings):
            up_str = f"{s['start_d']}({s['start_p']})→{s['peak_d']}({s['peak_p']})"
            dd_str = f"{s['peak_d']}→{s['dd_d']}({s['dd_p']})"
            print(f"{i+1:<3} {up_str:<28} {s['gain']:>6.1f}% {dd_str:<28} {s['dd_pct']:>6.1f}% {s['dd_days']:>5}天 {s['dd_cal']:>5}天 {s['retrace']:>7.1f}%")
        print(f"\n{'='*80}\n统计特征:")
        print(f"{'指标':<20} {'均值':>8} {'中位数':>8} {'最大':>8} {'最小':>8}")
        print("-" * 55)
        for label, vals in [('上涨涨幅%', gains), ('回撤幅度%', dds), ('回撤/涨幅比%', retraces), ('回撤交易日', dd_days_list), ('回撤自然日', dd_cals)]:
            print(f"{label:<20} {np.mean(vals):>7.1f} {np.median(vals):>7.1f} {max(vals):>7.1f} {min(vals):>7.1f}")

    print(f"\n{'='*80}\n当前回撤分析:")
    print(f"  高点: {cur_peak:.2f}  ({cur_peak_date.strftime('%Y-%m-%d')})")
    print(f"  回撤低点: {cur_low:.2f}  ({cur_low_date.strftime('%Y-%m-%d')})")
    print(f"  最新收盘: {cur_close:.2f}  (距高点-{cur_dd_close:.1f}%)")
    print(f"  当前最大回撤: -{cur_dd:.1f}%")
    print(f"  已过去: {cur_dd_days} 交易日 / {(cur_low_date - cur_peak_date).days} 自然日")
    if swings:
        rank = sum(1 for d in dds if d < cur_dd) + 1
        print(f"\n  与历史对比: 回撤 -{cur_dd:.1f}% 排名 {rank}/{len(dds)} (均值-{np.mean(dds):.1f}%, 中位-{np.median(dds):.1f}%, 最大-{max(dds):.1f}%)")
        if cur_dd_days < np.median(dd_days_list):
            print(f"  时间不足! 还差约 {np.median(dd_days_list)-cur_dd_days:.0f} 交易日 (均值{np.mean(dd_days_list):.0f}天, 中位{np.median(dd_days_list):.0f}天)")
        else:
            print(f"  时间过半/充足 (均值{np.mean(dd_days_list):.0f}天)")
    print(f"\n  关键支撑位（从高点 {cur_peak:.2f} 绝对回撤）:")
    for pct in [0.10, 0.15, 0.20, 0.236, 0.25, 0.30, 0.382, 0.50]:
        level = cur_peak * (1 - pct); mark = ""
        if abs(cur_dd/100 - pct) < 0.02: mark = " ◀ 当前低点"
        elif abs(cur_dd_close/100 - pct) < 0.02: mark = " ◀ 当前收盘"
        print(f"    -{pct*100:5.1f}% → {level:>8.2f}{mark}")
    full_up = cur_peak - cur_start
    print(f"\n  黄金分割支撑（基于本轮上涨 {cur_start:.2f}→{cur_peak:.2f}）:")
    for r in golden:
        level = cur_peak - full_up * r; dd_from_peak = (cur_peak - level) / cur_peak * 100; mark = ""
        if abs(level - cur_low) / max(cur_low,0.01) < 0.03: mark = " ← 当前低点附近"
        print(f"    回撤{r*100:5.1f}%: {level:>8.2f} (距高点-{dd_from_peak:.1f}%){mark}")
    if swings and dd_cals:
        print(f"\n  时间窗口预测:")
        for pct in [25, 50, 75]:
            days_c = np.percentile(dd_cals, pct); est = cur_peak_date + timedelta(days=int(days_c))
            status = "✓ 已过" if est < pd.Timestamp.now() else f"≈ {est.strftime('%Y-%m-%d')}"
            print(f"    {pct}%分位: ~{days_c:.0f}自然日 → {status}")
    if swings:
        space = "偏深" if cur_dd > np.percentile(dds,60) else ("偏浅" if cur_dd < np.percentile(dds,40) else "中性")
        time_s = "不足" if cur_dd_days < np.median(dd_days_list)*0.7 else ("充足" if cur_dd_days > np.median(dd_days_list) else "过半")
        print(f"\n  ★ 空间{space}（-{cur_dd:.1f}% vs 均值-{np.mean(dds):.1f}%），时间{time_s}（{cur_dd_days}天 vs 均值{np.mean(dd_days_list):.0f}天）")

def fetch_and_save(code, name, is_etf=False, start='20230101', out_dir='/tmp/stock_analysis'):
    import os; os.makedirs(out_dir, exist_ok=True)
    import akshare as ak
    csv_path = f'{out_dir}/{code}_{name}_daily.csv'
    if is_etf:
        hist = ak.fund_etf_hist_em(symbol=code, period="daily", start_date=start, end_date="20260715", adjust="qfq")
    else:
        hist = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date="20260715", adjust="qfq")
    hist.to_csv(csv_path, index=False)
    return csv_path

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='大波段回撤分析')
    parser.add_argument('--csv', help='本地CSV路径')
    parser.add_argument('--name', default='标的', help='名称')
    parser.add_argument('--code', default='---', help='代码')
    parser.add_argument('--batch', action='store_true', help='批量半导体5+1')
    args = parser.parse_args()

    if args.batch:
        targets = [
            ('159558', '半导体设备ETF', True, '20240101'),
            ('002371', '北方华创', False, '20230101'),
            ('688981', '中芯国际', False, '20230101'),
            ('688652', '京仪装备', False, '20230101'),
            ('688361', '中科飞测', False, '20230101'),
            ('688072', '拓荆科技', False, '20230101'),
        ]
        import time
        for code, name, is_etf, start in targets:
            try:
                csv = fetch_and_save(code, name, is_etf, start)
                hist = pd.read_csv(csv)
                analyze(hist, name, code)
            except Exception as e:
                print(f"\n❌ {code} {name} 失败: {e}")
            time.sleep(1)
    elif args.csv:
        hist = pd.read_csv(args.csv)
        analyze(hist, args.name, args.code)
    else:
        parser.print_help()
