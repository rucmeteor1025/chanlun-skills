#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
缠论引擎对拍验证：v5t vs chan.py

目的（用户目标3的前提）：在选股策略上线前，先确认本地缠论引擎（chan_core_v5t.py）
的分型/笔/中枢/买卖点没有理论硬错误。

方法：
1. 同一份 30min K线数据（sina 源，约最近1年），分别跑 v5t 引擎和 chan.py（严格配置）；
2. 对 v5t 输出做理论不变量检查（硬错误，必须100%通过）：
   - 合并K线后不存在包含关系
   - 笔的方向与起止分型一致、相邻笔方向交替
   - 中枢 ZD < ZG、GG >= ZG、DD <= ZD、中枢由>=3笔构成
   - B3 回抽低点 > ZG、S3 反抽高点 < ZD
   - B2 低点 > 对应 B1 低点
3. 与 chan.py 输出做结构性对比（算法差异，作为诊断参考，不算硬错误）：
   - 笔数量、笔端点价格对齐率
   - 中枢数量、中枢区间(ZD/ZG)对齐率
   - 买卖点类型与日期对齐

判定纪律：两实现笔的算法不同（v5t 有幅度过滤，chan.py 无），数量不一致不算 bug；
只有理论不变量破坏才定性为硬错误。

用法：
    python3 chanpy_crosscheck.py [--symbols sh600519,...] [--out 报告路径]
"""
import argparse
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ---- 路径装配 ----
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
CHAN_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))          # 缠论/
TECH_DIR = os.path.abspath(os.path.join(CHAN_DIR, ".."))             # 技术/
sys.path.insert(0, TECH_DIR)
sys.path.insert(0, "PATH_TO_CHAN_PY_REPO")

from chan_core_v5t import ChanCoreV5T  # noqa: E402

# chan.py 侧
from Chan import CChan  # noqa: E402
from ChanConfig import CChanConfig  # noqa: E402
from Common.CEnum import AUTYPE, KL_TYPE  # noqa: E402
from DataAPI.LocalDFAPI import register_df  # noqa: E402


DEFAULT_SYMBOLS = [
    ("sh000300", "沪深300", "index"),
    ("sh000905", "中证500", "index"),
    ("sz399006", "创业板指", "index"),
    ("sh600519", "贵州茅台", "stock"),
    ("sz300750", "宁德时代", "stock"),
    ("sz002594", "比亚迪", "stock"),
]

# 理论不变量容差
EPS = 1e-6
# 对拍对齐容差
PRICE_TOL = 0.005   # 笔端点/中枢区间价格容差 0.5%
ZS_TOL = 0.003      # 中枢区间容差 0.3%


# ---------------------------------------------------------------------------
# 数据
# ---------------------------------------------------------------------------
def fetch_30min(symbol: str, is_index: bool) -> pd.DataFrame:
    """sina 源 30min（约最近1年，1970根）。返回 datetime/open/high/low/close/volume。"""
    import akshare as ak
    adjust = "" if is_index else "qfq"
    df = ak.stock_zh_a_minute(symbol=symbol, period="30", adjust=adjust)
    if df is None or df.empty:
        raise RuntimeError(f"sina 30min 拉取失败: {symbol}")
    out = df.rename(columns={"day": "datetime"})[["datetime", "open", "high", "low", "close", "volume"]].copy()
    out["datetime"] = pd.to_datetime(out["datetime"])
    for c in ["open", "high", "low", "close", "volume"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna().sort_values("datetime").reset_index(drop=True)
    return out


def to_v5t_df(df: pd.DataFrame) -> pd.DataFrame:
    v = df.rename(columns={"datetime": "date"}).copy()
    return v


# ---------------------------------------------------------------------------
# v5t 理论不变量检查
# ---------------------------------------------------------------------------
def check_invariants(an: ChanCoreV5T) -> List[str]:
    """返回硬错误列表（空=通过）。"""
    errors: List[str] = []

    # 1. 合并K线后不存在包含关系
    m = an.df_merged
    if m is not None and len(m) >= 2:
        for i in range(1, len(m)):
            a, b = m.iloc[i - 1], m.iloc[i]
            if (b["high"] <= a["high"] and b["low"] >= a["low"]) or \
               (a["high"] <= b["high"] and a["low"] >= b["low"]):
                errors.append(f"合并K线残留包含关系: idx {i-1}~{i} ({a['date']} / {b['date']})")
                if len(errors) > 20:
                    return errors

    # 2. 笔：方向与起止分型一致 + 相邻笔方向交替
    prev_dir = None
    for k, s in enumerate(an.strokes):
        if s["direction"] == "up":
            if not (s["start_type"] == "bottom" and s["end_type"] == "top"):
                errors.append(f"笔{k} 上涨但分型类型错: {s['start_type']}->{s['end_type']}")
            if not (s["end_price"] > s["start_price"]):
                errors.append(f"笔{k} 上涨但终点<=起点")
        else:
            if not (s["start_type"] == "top" and s["end_type"] == "bottom"):
                errors.append(f"笔{k} 下跌但分型类型错: {s['start_type']}->{s['end_type']}")
            if not (s["end_price"] < s["start_price"]):
                errors.append(f"笔{k} 下跌但终点>=起点")
        if prev_dir is not None and s["direction"] == prev_dir:
            errors.append(f"笔{k} 与前笔同向({prev_dir})，未交替")
        prev_dir = s["direction"]

    # 3. 中枢：ZD < ZG、GG/DD 关系、>=3笔（同时检查日线级与次级别）
    for lvl_name, plist in [("日线级", an.pivots), ("次级别", an.sub_level_pivots)]:
        for k, p in enumerate(plist):
            if not (p["low"] < p["high"]):
                errors.append(f"{lvl_name}中枢{k} ZD>=ZG: {p['low']:.3f} >= {p['high']:.3f}")
            if p["gg"] < p["high"] - EPS:
                errors.append(f"{lvl_name}中枢{k} GG < ZG")
            if p["dd"] > p["low"] + EPS:
                errors.append(f"{lvl_name}中枢{k} DD > ZD")
            if p["stroke_count"] < 3:
                errors.append(f"{lvl_name}中枢{k} 笔数<3: {p['stroke_count']}")

    # 4. 买卖点结构约束
    b1_by_pivot: Dict[int, dict] = {}
    for pt in an.buy_sell_points:
        if pt["type"] == "B1":
            b1_by_pivot[pt["pivot_idx"]] = pt
    for pt in an.buy_sell_points:
        pivot = None
        pi = pt.get("pivot_idx")
        if isinstance(pi, int) and 0 <= pi < len(an.pivots):
            pivot = an.pivots[pi]
        if pt["type"] == "B3" and pivot is not None:
            if not (pt["price"] > pivot["high"]):
                errors.append(f"B3 回抽进中枢: price={pt['price']:.3f} <= ZG={pivot['high']:.3f} ({pt['date']})")
        if pt["type"] == "S3" and pivot is not None:
            if not (pt["price"] < pivot["low"]):
                errors.append(f"S3 反抽进中枢: price={pt['price']:.3f} >= ZD={pivot['low']:.3f} ({pt['date']})")
        if pt["type"] == "B2" and pi in b1_by_pivot:
            if not (pt["price"] > b1_by_pivot[pi]["price"]):
                errors.append(f"B2 跌破一买: {pt['price']:.3f} <= {b1_by_pivot[pi]['price']:.3f} ({pt['date']})")

    return errors


# ---------------------------------------------------------------------------
# chan.py 运行与结果抽取
# ---------------------------------------------------------------------------
def run_chanpy(df: pd.DataFrame, code: str):
    register_df(code, df)
    conf = CChanConfig({
        "divergence_rate": 1.0,
        "min_zs_cnt": 2,
        "bsp1_only_multibi_zs": True,
        "macd_algo": "full_area",
        "strict_bsp3": True,
        "kl_data_check": False,
        "print_warning": False,
    })
    chan = CChan(
        code=code,
        begin_time=str(df["datetime"].iloc[0].date()),
        end_time=str(df["datetime"].iloc[-1].date()),
        data_src="custom:LocalDFAPI.CLocalDF",
        lv_list=[KL_TYPE.K_30M],
        config=conf,
        autype=AUTYPE.NONE,
    )
    kll = chan[KL_TYPE.K_30M]
    strokes = []
    for bi in kll.bi_list:
        strokes.append({
            "direction": "up" if bi.is_up() else "down",
            "start_price": float(bi.get_begin_val()),
            "end_price": float(bi.get_end_val()),
            "end_time": str(bi.get_end_klu().time),
        })
    pivots = []
    for zs in kll.zs_list:
        if not zs.is_sure:
            continue
        pivots.append({
            "low": float(zs.low),
            "high": float(zs.high),
            "stroke_count": len(zs.bi_lst) if hasattr(zs, "bi_lst") else None,
        })
    bsps = []
    for bsp in kll.bs_point_lst.getSortedBspList():
        bsps.append({
            "is_buy": bool(bsp.is_buy),
            "types": bsp.type2str(),
            "time": str(bsp.bi.get_end_klu().time),
            "price": float(bsp.bi.get_end_val()),
        })
    return {"strokes": strokes, "pivots": pivots, "bsps": bsps}


# ---------------------------------------------------------------------------
# 结构对比
# ---------------------------------------------------------------------------
def compare_strokes(v5t_strokes: List[dict], py_strokes: List[dict]) -> Dict:
    n_v, n_p = len(v5t_strokes), len(py_strokes)
    # 用端点价格对齐：对 v5t 每笔，在 chan.py 笔中找终点价格最接近且同向的
    matched = 0
    used = set()
    for s in v5t_strokes:
        best, best_d = None, PRICE_TOL
        for j, t in enumerate(py_strokes):
            if j in used or t["direction"] != s["direction"]:
                continue
            d = abs(t["end_price"] - s["end_price"]) / s["end_price"]
            if d <= best_d:
                best, best_d = j, d
        if best is not None:
            matched += 1
            used.add(best)
    return {
        "v5t_n": n_v,
        "chanpy_n": n_p,
        "matched": matched,
        "match_rate_v5t": round(matched / n_v, 3) if n_v else None,
    }


def compare_pivots(v5t_pivots: List[dict], py_pivots: List[dict]) -> Dict:
    n_v, n_p = len(v5t_pivots), len(py_pivots)
    matched = 0
    used = set()
    for p in v5t_pivots:
        for j, q in enumerate(py_pivots):
            if j in used:
                continue
            mid = (p["low"] + p["high"]) / 2
            if abs(q["low"] - p["low"]) / mid <= ZS_TOL and abs(q["high"] - p["high"]) / mid <= ZS_TOL:
                matched += 1
                used.add(j)
                break
    return {
        "v5t_n": n_v,
        "chanpy_n": n_p,
        "matched": matched,
        "match_rate_v5t": round(matched / n_v, 3) if n_v else None,
    }


def compare_bsp(v5t_points: List[dict], py_bsps: List[dict]) -> Dict:
    def side(t: str) -> str:
        return "buy" if t.startswith("B") else "sell"
    v_types = sorted({p["type"] for p in v5t_points})
    py_has_b1 = any("T1" in b["types"] for b in py_bsps if b["is_buy"])
    py_has_b2 = any("T2" in b["types"] for b in py_bsps if b["is_buy"])
    py_has_b3 = any("T3" in b["types"] for b in py_bsps if b["is_buy"])
    return {
        "v5t_n": len(v5t_points),
        "v5t_types": v_types,
        "chanpy_n": len(py_bsps),
        "chanpy_buy_n": sum(1 for b in py_bsps if b["is_buy"]),
        "chanpy_has": {"B1": py_has_b1, "B2": py_has_b2, "B3": py_has_b3},
        "v5t_buy_n": sum(1 for p in v5t_points if side(p["type"]) == "buy"),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def analyze_one(symbol: str, name: str, inst: str, df: pd.DataFrame) -> Dict:
    # v5t：30min 级别调小过滤参数，尽量减少与 chan.py 的算法口径差异
    v5t_df = to_v5t_df(df)
    an = ChanCoreV5T(
        v5t_df,
        min_amplitude=0.001,
        pivot_min_amplitude=0.005,
        pivot_min_strokes=3,
        pivot_max_strokes=20,
        dynamic_min_amplitude=False,
        dynamic_pivot_rules=False,
        symbol=symbol,
        instrument_type=inst,
    )
    an.analyze()
    errors = check_invariants(an)

    py_res = run_chanpy(df, symbol)

    all_v5t_pivots = list(an.pivots) + list(an.sub_level_pivots)
    return {
        "symbol": symbol,
        "name": name,
        "bars": len(df),
        "range": f"{df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}",
        "hard_errors": errors,
        "stroke_cmp": compare_strokes(an.strokes, py_res["strokes"]),
        "pivot_cmp": compare_pivots(all_v5t_pivots, py_res["pivots"]),
        "bsp_cmp": compare_bsp(an.buy_sell_points, py_res["bsps"]),
        "v5t_counts": {
            "fractals": len(an.fractals),
            "strokes": len(an.strokes),
            "pivots_daily": len(an.pivots),
            "pivots_sub": len(an.sub_level_pivots),
            "bsp": len(an.buy_sell_points),
        },
        "chanpy_counts": {
            "strokes": len(py_res["strokes"]),
            "pivots": len(py_res["pivots"]),
            "bsp": len(py_res["bsps"]),
        },
    }


def render_report(results: List[Dict]) -> str:
    lines = []
    lines.append("# 缠论引擎对拍报告（v5t vs chan.py）")
    lines.append(f"\n生成时间：{datetime.now():%Y-%m-%d %H:%M}")
    lines.append("\n## 判定纪律")
    lines.append("- 理论不变量破坏 = 硬错误（必须修复）")
    lines.append("- 与 chan.py 的数量/结构差异 = 算法口径差异（诊断参考，不算 bug）")
    lines.append("- v5t 本次运行参数：min_amplitude=0.001, pivot_min_amplitude=0.005, 无动态过滤（对齐 chan.py 口径）")
    lines.append("- chan.py 配置：min_zs_cnt=2, bsp1_only_multibi_zs, macd_algo=full_area, strict_bsp3, divergence_rate=1.0")

    total_err = 0
    lines.append("\n## 总览")
    lines.append("| 标的 | K线数 | 硬错误 | 笔 v5t/chan.py | 笔对齐率 | 中枢 v5t/chan.py | 中枢对齐率 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        ne = len(r["hard_errors"])
        total_err += ne
        sc, pc = r["stroke_cmp"], r["pivot_cmp"]
        lines.append(
            f"| {r['name']} {r['symbol']} | {r['bars']} | {ne} | "
            f"{sc['v5t_n']}/{sc['chanpy_n']} | {sc['match_rate_v5t']} | "
            f"{pc['v5t_n']}/{pc['chanpy_n']} | {pc['match_rate_v5t']} |"
        )
    lines.append(f"\n**硬错误总数：{total_err}**")

    for r in results:
        lines.append(f"\n## {r['name']}（{r['symbol']}）")
        lines.append(f"- 区间：{r['range']}")
        lines.append(f"- v5t：分型{r['v5t_counts']['fractals']} 笔{r['v5t_counts']['strokes']} "
                     f"日线中枢{r['v5t_counts']['pivots_daily']} 次级别中枢{r['v5t_counts']['pivots_sub']} 买卖点{r['v5t_counts']['bsp']}")
        lines.append(f"- chan.py：笔{r['chanpy_counts']['strokes']} 中枢{r['chanpy_counts']['pivots']} 买卖点{r['chanpy_counts']['bsp']}")
        bc = r["bsp_cmp"]
        lines.append(f"- 买卖点：v5t 买{bc['v5t_buy_n']}个 {bc['v5t_types']}；chan.py 买{bc['chanpy_buy_n']}个 含B1/B2/B3={bc['chanpy_has']}")
        if r["hard_errors"]:
            lines.append("- ❌ 硬错误：")
            for e in r["hard_errors"][:15]:
                lines.append(f"  - {e}")
            if len(r["hard_errors"]) > 15:
                lines.append(f"  - ... 共 {len(r['hard_errors'])} 条")
        else:
            lines.append("- ✅ 理论不变量全部通过")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="v5t vs chan.py 对拍验证")
    parser.add_argument("--symbols", default=None, help="逗号分隔，如 sh600519,sz300750")
    parser.add_argument("--out", default=os.path.join(CURRENT_DIR, f"对拍报告_{datetime.now():%Y%m%d}.md"))
    args = parser.parse_args()

    symbol_rows = DEFAULT_SYMBOLS
    if args.symbols:
        wanted = set(args.symbols.split(","))
        symbol_rows = [s for s in DEFAULT_SYMBOLS if s[0] in wanted]

    results = []
    for symbol, name, inst in symbol_rows:
        print(f"[对拍] {name} {symbol} ...", flush=True)
        df = fetch_30min(symbol, inst == "index")
        res = analyze_one(symbol, name, inst, df)
        results.append(res)
        ne = len(res["hard_errors"])
        print(f"  -> 硬错误 {ne}；笔 {res['stroke_cmp']['v5t_n']}/{res['stroke_cmp']['chanpy_n']} "
              f"对齐 {res['stroke_cmp']['match_rate_v5t']}；中枢 {res['pivot_cmp']['v5t_n']}/{res['pivot_cmp']['chanpy_n']} "
              f"对齐 {res['pivot_cmp']['match_rate_v5t']}", flush=True)

    report = render_report(results)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\n报告已写入: {args.out}")

    total_err = sum(len(r["hard_errors"]) for r in results)
    print(f"硬错误总数: {total_err}")
    sys.exit(1 if total_err else 0)


if __name__ == "__main__":
    main()
