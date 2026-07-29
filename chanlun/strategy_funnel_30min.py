# -*- coding: utf-8 -*-
"""
30分钟级别三层漏斗选股策略（严格缠论定义）

    第一层：指数一买（方向确认）—— ≥2个同向向下中枢构成下跌趋势，
            c段创新低 + MACD面积背驰(c<b×rate) + DIF/DEA在0轴下
    第二层：板块二买（赛道确认）—— 指数一买±N日内板块一买 +
            一买后第一次回调不破一买低点 + 回调幅度<61.8%
    第三层：个股三买（择时入场）—— 已完成中枢 + 向上离开 + 第一次回试不破ZG
            （可选MACD面积确认）

设计纪律：
- 不改 chan_core_v5t.py 主体，v5t 只用于分型/笔/中枢原语；
  一买/二买/三买的严格判定全部在本模块实现（v5t 自带的 B1/B2/B3 标注不使用，
  对拍已证明 v5t 标注多为盘整背驰口径，不满足严格定义）
- 背驰判定一律用 MACD 面积（macd_divergence.py），不用价格幅度

用法：
    python3 strategy_funnel_30min.py --date 2026-07-18
    python3 strategy_funnel_30min.py --date 2026-07-18 --sectors new_dlhy,new_dqhy  # 调试
"""
import argparse
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import yaml

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
TECH_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
sys.path.insert(0, TECH_DIR)
sys.path.insert(0, CURRENT_DIR)

from chan_core_v5t import ChanCoreV5T  # noqa: E402
from macd_divergence import MACDDivergence  # noqa: E402
from funnel_data import FunnelData  # noqa: E402
from sector_mapper import SectorMapper  # noqa: E402

DEFAULT_CONFIG = os.path.join(CURRENT_DIR, "funnel_config.yaml")


# ---------------------------------------------------------------------------
# 单个标的的缠论结构（v5t 原语 + MACD + 原始K线索引映射）
# ---------------------------------------------------------------------------
class ChanStructure30Min:
    """对单标的 30min K线跑 v5t，提供笔/中枢 + MACD 一体化访问"""

    def __init__(self, df: pd.DataFrame, engine_cfg: Dict, macd: MACDDivergence,
                 symbol: str = "", instrument_type: str = "stock"):
        self.symbol = symbol
        self.macd = macd
        self.df_raw = df.rename(columns={"datetime": "date"}).copy()
        self.df_raw["date"] = pd.to_datetime(self.df_raw["date"])
        self.df_raw = self.df_raw.sort_values("date").reset_index(drop=True)
        self.df_raw = macd.compute(self.df_raw)

        self.engine = ChanCoreV5T(
            self.df_raw[["date", "open", "high", "low", "close", "volume"]],
            min_amplitude=engine_cfg.get("min_amplitude", 0.003),
            pivot_min_amplitude=engine_cfg.get("pivot_min_amplitude", 0.008),
            pivot_min_strokes=engine_cfg.get("pivot_min_strokes", 3),
            pivot_max_strokes=engine_cfg.get("pivot_max_strokes", 12),
            pivot_init_filter_ratio=engine_cfg.get("pivot_init_filter_ratio", 2.0),
            pivot_extend_filter_ratio=engine_cfg.get("pivot_extend_filter_ratio", 1.5),
            pivot_final_filter_ratio=engine_cfg.get("pivot_final_filter_ratio", 2.0),
            dynamic_min_amplitude=False,
            dynamic_pivot_rules=False,
            symbol=symbol,
            instrument_type=instrument_type,
        )
        self.engine.analyze()

        # 合并K线 idx -> 原始K线 idx（merged.date == 该合并bar最后一根原始bar的date）
        raw_pos = {d: i for i, d in enumerate(self.df_raw["date"])}
        self._merged_to_raw = []
        if self.engine.df_merged is not None:
            for _, row in self.engine.df_merged.iterrows():
                self._merged_to_raw.append(raw_pos.get(row["date"], -1))

    # ------------------------------------------------------------------
    @property
    def strokes(self) -> List[Dict]:
        return self.engine.strokes

    @property
    def pivots(self) -> List[Dict]:
        """全部中枢（日线级+次级别合并，按起始笔排序，按笔重叠去重）。

        v5t 滚动识别会对同一个中枢产出多个版本（笔区间大部分重叠），
        趋势判定前必须去重：笔区间重叠>50% 的只保留笔数最多者。
        """
        all_p = list(self.engine.pivots) + list(self.engine.sub_level_pivots)
        all_p = sorted(all_p, key=lambda p: p["stroke_indices"][0])
        kept: List[Dict] = []
        for p in all_p:
            span = set(p["stroke_indices"])
            dup_of = None
            for k, q in enumerate(kept):
                qspan = set(q["stroke_indices"])
                inter = len(span & qspan)
                if inter and inter / min(len(span), len(qspan)) > 0.5:
                    dup_of = k
                    break
            if dup_of is None:
                kept.append(p)
            elif p["stroke_count"] > kept[dup_of]["stroke_count"]:
                kept[dup_of] = p
        return kept

    def raw_idx(self, merged_idx: int) -> int:
        if 0 <= merged_idx < len(self._merged_to_raw):
            return self._merged_to_raw[merged_idx]
        return -1

    def stroke_macd_area(self, stroke: Dict) -> float:
        s = self.raw_idx(stroke["start_idx"])
        e = self.raw_idx(stroke["end_idx"])
        return self.macd.stroke_macd_area(self.df_raw, s, e, stroke["direction"])

    def dif_below_zero_at_stroke_end(self, stroke: Dict) -> bool:
        return self.macd.dif_below_zero(self.df_raw, self.raw_idx(stroke["end_idx"]))

    def bar_date(self, raw_idx_: int) -> pd.Timestamp:
        raw_idx_ = min(max(0, raw_idx_), len(self.df_raw) - 1)
        return pd.Timestamp(self.df_raw["date"].iloc[raw_idx_])


# ---------------------------------------------------------------------------
# 严格买卖点判定
# ---------------------------------------------------------------------------
def scan_buy1(st: ChanStructure30Min, cfg: Dict) -> List[Dict]:
    """严格一买：同向向下中枢 + c段创新低 + MACD背驰 + 0轴下。

    cfg['min_pivots']>=2：趋势背驰（a+A+b+B+c，两个无重叠下降中枢）
    cfg['min_pivots']==1：盘整背驰（a+A+b，单中枢，进入段 vs 离开段）

    返回候选列表（按时间升序），每个含:
    {'date', 'price', 'divergence_rate', 'c_stroke_idx', 'pivot_zd', 'pivot_zg',
     'n_pivots', 'below_zero'}
    """
    pivots = st.pivots
    strokes = st.strokes
    min_pivots = cfg.get("min_pivots", 2)
    rate = cfg.get("divergence_rate", 0.9)
    use_peak_fb = cfg.get("use_peak_fallback", True)
    need_below_zero = cfg.get("macd_below_zero", True)

    results = []
    # 从最新往回遍历候选中枢B（趋势模式需要A、B无重叠下降对；盘整模式只看B）
    start_i = len(pivots) - 1
    end_i = 0 if min_pivots >= 2 else -1
    for i in range(start_i, end_i, -1):
        pivot_b = pivots[i]
        n_pivots = 1
        if min_pivots >= 2:
            pivot_a = pivots[i - 1]
            if not (pivot_b["high"] < pivot_a["low"]):
                continue  # 有重叠 = 中枢延伸/盘整，不是趋势
            n_pivots = 2
            if min_pivots >= 3 and i >= 2:
                if not (pivots[i - 1]["high"] < pivots[i - 2]["low"]):
                    continue
                n_pivots = 3
            # b段：中枢A结束到中枢B开始之间的最后一根下跌笔
            gap = [j for j in range(pivot_a["stroke_indices"][-1] + 1,
                                    pivot_b["stroke_indices"][0])
                   if strokes[j]["direction"] == "down"]
        else:
            # 盘整背驰：b段 = 进入中枢B的最后一根下跌笔（中枢之前的下跌笔）
            gap = [j for j in range(0, pivot_b["stroke_indices"][0])
                   if strokes[j]["direction"] == "down"]
            # 实测下降链长度（宽松采集时记录，供事后按 min_pivots 过滤）
            if i >= 1 and pivot_b["high"] < pivots[i - 1]["low"]:
                n_pivots = 2
                if i >= 2 and pivots[i - 1]["high"] < pivots[i - 2]["low"]:
                    n_pivots = 3
        if not gap:
            continue
        b_stroke = strokes[gap[-1]]

        # c段：中枢B之后，价格未先向上突破ZG 的前提下，
        # 第一根跌破ZD（创新低）的下跌笔；先破ZG则该中枢对作废
        c_stroke = None
        c_idx = None
        for j in range(pivot_b["stroke_indices"][-1] + 1, len(strokes)):
            s = strokes[j]
            if s["direction"] == "up" and s["end_price"] > pivot_b["high"]:
                break  # 先向上突破 = 下跌结构被破坏
            if s["direction"] == "down" and s["end_price"] < pivot_b["low"]:
                c_stroke = s
                c_idx = j
                break
        if c_stroke is None:
            continue

        # MACD 背驰：c段面积 < b段面积 × rate
        b_area = st.stroke_macd_area(b_stroke)
        c_area = st.stroke_macd_area(c_stroke)
        div_ok, div_rate = (False, float("inf"))
        if b_area > 0:
            div_rate = c_area / b_area
            div_ok = div_rate < rate
        if not div_ok and use_peak_fb:
            b_peak = st.macd.stroke_dif_peak(
                st.df_raw, st.raw_idx(b_stroke["start_idx"]), st.raw_idx(b_stroke["end_idx"]), "down")
            c_peak = st.macd.stroke_dif_peak(
                st.df_raw, st.raw_idx(c_stroke["start_idx"]), st.raw_idx(c_stroke["end_idx"]), "down")
            if b_peak > 0 and c_peak / b_peak < rate:
                div_ok, div_rate = True, c_peak / b_peak

        below_zero = st.dif_below_zero_at_stroke_end(c_stroke)
        if not div_ok:
            continue
        if need_below_zero and not below_zero:
            continue

        results.append({
            "date": pd.Timestamp(c_stroke["end_date"]),
            "price": float(c_stroke["end_price"]),
            "divergence_rate": round(float(div_rate), 3),
            "c_stroke_idx": c_idx,
            "pivot_zd": float(pivot_b["low"]),
            "pivot_zg": float(pivot_b["high"]),
            "n_pivots": n_pivots,
            "below_zero": bool(below_zero),
        })
        # 不 break：一买在c段创新低时即锁存，之后的走势（包括向上突破ZG）不影响其历史有效性；
        # 扫描所有中枢对，返回全部历史一买（回测需要）
    return sorted(results, key=lambda r: r["date"])


def scan_buy2(st: ChanStructure30Min, index_b1_date: pd.Timestamp, cfg: Dict,
              buy1_cfg: Dict) -> List[Dict]:
    """板块二买：指数一买±window日内有板块一买 + 首次回调不破 + 回调<61.8%"""
    window = pd.Timedelta(days=cfg.get("window_days", 5))
    max_retrace = cfg.get("max_retrace_ratio", 0.618)

    results = []
    for b1 in scan_buy1(st, buy1_cfg):
        if abs(b1["date"] - index_b1_date) > window:
            continue
        c_idx = b1["c_stroke_idx"]
        strokes = st.strokes
        # 一买后：第一根向上笔（反弹），再第一根向下笔（回调）
        if c_idx + 2 >= len(strokes):
            continue
        up_stroke = strokes[c_idx + 1]
        retrace_stroke = strokes[c_idx + 2]
        if up_stroke["direction"] != "up" or retrace_stroke["direction"] != "down":
            continue
        rebound = up_stroke["end_price"] - b1["price"]
        if rebound <= 0:
            continue
        if retrace_stroke["end_price"] <= b1["price"]:
            continue  # 跌破一买低点 = 二买不成立
        retrace_ratio = (up_stroke["end_price"] - retrace_stroke["end_price"]) / rebound
        if retrace_ratio >= max_retrace:
            continue
        results.append({
            "date": pd.Timestamp(retrace_stroke["end_date"]),
            "price": float(retrace_stroke["end_price"]),
            "buy1_date": b1["date"],
            "buy1_price": b1["price"],
            "retrace_ratio": round(float(retrace_ratio), 3),
            "divergence_rate": b1["divergence_rate"],
        })
    return results


def scan_buy3(st: ChanStructure30Min, cfg: Dict) -> List[Dict]:
    """个股三买：已完成中枢(≥N笔) + 向上离开 + 第一次回试低点>ZG (+MACD确认)。

    返回 [{'date','price','stop_loss','zs_zd','zs_zg','macd_ratio'}]
    """
    min_strokes = cfg.get("min_strokes_in_zs", 3)
    first_only = cfg.get("first_pullback_only", True)
    macd_confirm = cfg.get("macd_confirm", True)
    min_amp = cfg.get("min_zs_amplitude", 0.01)
    stop_buffer = 0.005

    strokes = st.strokes
    results = []
    for pivot in reversed(st.pivots):
        if pivot["stroke_count"] < min_strokes:
            continue
        if pivot["amplitude"] < min_amp:
            continue
        zg, zd = pivot["high"], pivot["low"]
        last_idx = pivot["stroke_indices"][-1]

        # 找离开笔：中枢后第一根有效向上离开（终点>ZG）；若先跌破ZD则放弃该中枢
        breakout_idx = None
        for j in range(last_idx + 1, len(strokes)):
            s = strokes[j]
            if s["direction"] == "up" and s["end_price"] > zg:
                breakout_idx = j
                break
            if s["direction"] == "down" and s["end_price"] < zd:
                break
        if breakout_idx is None:
            continue

        # 第一次回试：离开笔后第一根向下笔
        pullback_idx = None
        for j in range(breakout_idx + 1, len(strokes)):
            if strokes[j]["direction"] == "down":
                pullback_idx = j
                break
        if pullback_idx is None:
            continue
        pullback = strokes[pullback_idx]
        if not (pullback["end_price"] > zg):
            continue  # 回进中枢 = 非三买

        macd_ratio = None
        b_area = st.stroke_macd_area(strokes[breakout_idx])
        p_area = st.stroke_macd_area(pullback)
        if b_area > 0:
            macd_ratio = round(p_area / b_area, 3)
        if macd_confirm and (macd_ratio is None or macd_ratio >= 1.0):
            continue

        results.append({
            "date": pd.Timestamp(pullback["end_date"]),
            "price": float(pullback["end_price"]),
            "stop_loss": round(float(zg) * (1 - stop_buffer), 4),
            "zs_zd": float(zd),
            "zs_zg": float(zg),
            "macd_ratio": macd_ratio,
        })
        if first_only:
            break
    return results


# ---------------------------------------------------------------------------
# 三层漏斗
# ---------------------------------------------------------------------------
class FunnelStrategy30Min:
    """30分钟三层漏斗选股"""

    def __init__(self, config_path: str = DEFAULT_CONFIG):
        with open(config_path, encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        macd_cfg = self.cfg.get("macd", {})
        self.macd = MACDDivergence(macd_cfg.get("fast", 12), macd_cfg.get("slow", 26),
                                   macd_cfg.get("signal", 9))
        self.data = FunnelData(sleep_sec=self.cfg.get("data", {}).get("sleep_sec", 0.35))
        self.sector_mapper = SectorMapper()
        self._sectors_cache: Optional[List[Dict]] = None
        self._members_cache: Dict[str, List[Dict]] = {}

    # ------------------------------------------------------------------
    def _structure(self, df: pd.DataFrame, symbol: str, instrument_type: str) -> ChanStructure30Min:
        return ChanStructure30Min(df, self.cfg.get("engine", {}), self.macd,
                                  symbol=symbol, instrument_type=instrument_type)

    def get_sectors(self) -> List[Dict]:
        if self._sectors_cache is None:
            source = self.cfg.get("sector", {}).get("source", "sw")
            if source == "sw":
                sectors = self.sector_mapper.get_industries_sw()
            else:
                sectors = self.sector_mapper.get_industries()
            wl = set(self.cfg.get("sector", {}).get("whitelist") or [])
            bl = set(self.cfg.get("sector", {}).get("blacklist") or [])
            if wl:
                sectors = [s for s in sectors if s["code"] in wl]
            if bl:
                sectors = [s for s in sectors if s["code"] not in bl]
            self._sectors_cache = sectors
        return self._sectors_cache

    def get_members(self, sector_code: str, date: Optional[str] = None) -> List[Dict]:
        """板块成分。sw 源按 date 取时点成分（默认今天）；sina 源为当前快照。"""
        source = self.cfg.get("sector", {}).get("source", "sw")
        key = (sector_code, date or "latest")
        if key not in self._members_cache:
            if source == "sw":
                d = date or datetime.now().strftime("%Y%m%d")
                self._members_cache[key] = self.sector_mapper.get_constituents_pit(sector_code, d)
            else:
                self._members_cache[key] = self.sector_mapper.get_constituents(sector_code)
        return self._members_cache[key]

    # ------------------------------------------------------------------
    def scan_index_buy1(self, end_date: Optional[str] = None) -> List[Dict]:
        """第一层：扫描指数池一买"""
        buy1_cfg = self.cfg.get("buy1", {})
        lookback = buy1_cfg.get("lookback_bars", 800)
        results = []
        for code in self.cfg.get("index_codes", []):
            try:
                # 历史日期扫描直接用缓存（不限流）；扫描"最新"才联网更新
                df = self.data.get(code, end_date=end_date, lookback_bars=lookback,
                                   refresh=(end_date is None))
                if len(df) < 100:
                    continue
                st = self._structure(df, code, "index")
                for b1 in scan_buy1(st, buy1_cfg):
                    results.append({"code": code, **b1})
            except Exception as e:  # noqa: BLE001
                print(f"⚠️ 指数 {code} 扫描失败: {e}")
        return results

    # ------------------------------------------------------------------
    def scan_sector_buy2(self, index_b1: Dict, end_date: Optional[str] = None,
                         refresh_members: bool = False) -> List[Dict]:
        """第二层：在指数一买窗口内扫描板块二买（板块指数由成分等权合成）"""
        buy1_cfg = self.cfg.get("buy1", {})
        buy2_cfg = self.cfg.get("buy2", {})
        lookback = buy1_cfg.get("lookback_bars", 800)
        min_members = self.cfg.get("sector", {}).get("min_members_with_data", 3)
        results = []
        for sector in self.get_sectors():
            try:
                members = self.get_members(sector["code"])
                codes = [m["code"] for m in members]
                df = self.data.build_sector_index(codes, end_date=end_date, refresh=refresh_members)
                if len(df) < 100:
                    continue
                df = df.tail(lookback).reset_index(drop=True)
                st = self._structure(df, sector["code"], "index")
                for b2 in scan_buy2(st, index_b1["date"], buy2_cfg, buy1_cfg):
                    results.append({"code": sector["code"], "name": sector["name"], **b2})
            except Exception as e:  # noqa: BLE001
                print(f"⚠️ 板块 {sector['name']} 扫描失败: {e}")
        return results

    # ------------------------------------------------------------------
    def scan_stock_buy3(self, sectors: List[Dict], end_date: Optional[str] = None,
                        max_stocks: Optional[int] = None) -> pd.DataFrame:
        """第三层：二买板块成分股中扫描三买"""
        buy3_cfg = self.cfg.get("buy3", {})
        lookback = buy3_cfg.get("lookback_bars", 400)
        rows = []
        scanned = 0
        for sector in sectors:
            members = self.get_members(sector["code"], date=end_date)
            for m in members:
                if max_stocks and scanned >= max_stocks:
                    break
                scanned += 1
                code = m["code"]
                try:
                    df = self.data.get(code, end_date=end_date,
                                       lookback_bars=lookback, refresh=False)
                    if len(df) < 60:
                        continue
                    st = self._structure(df, code, "stock")
                    for b3 in scan_buy3(st, buy3_cfg):
                        actionable = b3["date"]  # 信号确认于回试笔收盘
                        rows.append({
                            "code": code,
                            "name": m.get("name", ""),
                            "sector": sector["name"],
                            "buy3_price": round(b3["price"], 3),
                            "stop_loss": b3["stop_loss"],
                            "zs_range": f"{b3['zs_zd']:.2f}~{b3['zs_zg']:.2f}",
                            "macd_ratio": b3["macd_ratio"],
                            "signal_date": b3["date"],
                            "actionable_date": actionable,
                        })
                except Exception as e:  # noqa: BLE001
                    print(f"⚠️ 个股 {code} 扫描失败: {e}")
        return pd.DataFrame(rows)

    # ------------------------------------------------------------------
    def run(self, date: Optional[str] = None, max_stocks: Optional[int] = None) -> Dict:
        """执行完整漏斗"""
        print(f"[漏斗] 截止日期: {date or '最新'}")
        index_b1s = self.scan_index_buy1(end_date=date)
        if not index_b1s:
            print("[第一层] 指数无一买信号 → 漏斗终止")
            return {"date": date, "index_buy1": [], "sector_buy2": [], "stock_buy3": pd.DataFrame(),
                    "message": "指数无一买信号"}
        print(f"[第一层] 指数一买 {len(index_b1s)} 个: "
              + ", ".join(f"{r['code']}@{r['date']:%m-%d %H:%M}(衰减{r['divergence_rate']})" for r in index_b1s))

        latest_b1 = max(index_b1s, key=lambda r: r["date"])
        # 锚点新鲜度：指数一买过旧则漏斗不启动（板块二买/个股三买的操作窗口在一买后数周内）
        anchor_recency = self.cfg.get("buy1", {}).get("anchor_recency_days", 30)
        if date:
            anchor = pd.Timestamp(date)
            if latest_b1["date"] < anchor - pd.Timedelta(days=anchor_recency):
                print(f"[第一层] 最近的指数一买({latest_b1['date']:%Y-%m-%d})距今超过{anchor_recency}天 → 漏斗终止")
                return {"date": date, "index_buy1": index_b1s, "sector_buy2": [],
                        "stock_buy3": pd.DataFrame(), "message": "指数一买信号过旧"}
        sector_b2s = self.scan_sector_buy2(latest_b1, end_date=date)
        if not sector_b2s:
            print("[第二层] 无二买板块 → 漏斗终止")
            return {"date": date, "index_buy1": index_b1s, "sector_buy2": [],
                    "stock_buy3": pd.DataFrame(), "message": "无板块二买信号"}
        print(f"[第二层] 板块二买 {len(sector_b2s)} 个: "
              + ", ".join(f"{r['name']}@{r['date']:%m-%d}" for r in sector_b2s))

        df3 = self.scan_stock_buy3(sector_b2s, end_date=date, max_stocks=max_stocks)
        # 新鲜度过滤：只保留信号日在截止日期前 recency_days 内的三买（防止陈旧信号驱动实盘）
        recency_days = self.cfg.get("buy3", {}).get("recency_days", 10)
        if len(df3):
            anchor = pd.Timestamp(date) if date else df3["signal_date"].max()
            df3 = df3[df3["signal_date"] >= anchor - pd.Timedelta(days=recency_days)].reset_index(drop=True)
        print(f"[第三层] 个股三买 {len(df3)} 个")
        return {"date": date, "index_buy1": index_b1s, "sector_buy2": sector_b2s,
                "stock_buy3": df3, "message": "ok" if len(df3) else "当日无个股三买信号"}


def main():
    parser = argparse.ArgumentParser(description="30min 三层漏斗选股")
    parser.add_argument("--date", default=None, help="截止日期 YYYY-MM-DD（默认最新）")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--sectors", default=None, help="只扫这些板块（逗号分隔，调试用）")
    parser.add_argument("--max-stocks", type=int, default=None, help="第三层最多扫描个股数（调试用）")
    parser.add_argument("--out", default=None, help="结果输出 csv 路径")
    args = parser.parse_args()

    strategy = FunnelStrategy30Min(args.config)
    if args.sectors:
        strategy.cfg["sector"]["whitelist"] = args.sectors.split(",")

    result = strategy.run(date=args.date, max_stocks=args.max_stocks)
    df = result["stock_buy3"]
    if len(df):
        cols = ["code", "name", "sector", "buy3_price", "stop_loss", "zs_range",
                "macd_ratio", "signal_date", "actionable_date"]
        print("\n" + df[cols].to_string(index=False))
        if args.out:
            df.to_csv(args.out, index=False, encoding="utf-8-sig")
            print(f"\n已保存: {args.out}")
    else:
        print(f"\n{result['message']}")


if __name__ == "__main__":
    main()
