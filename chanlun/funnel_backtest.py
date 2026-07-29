# -*- coding: utf-8 -*-
"""
30min 三层漏斗策略回测

方法（无未来函数的逐日扫描仿真）：
1. 预载全部数据到内存（指数 + 板块成分股 30min，来自 funnel_data 缓存）
2. 逐交易日仿真盘后扫描：
   - 第一层：4个指数 → 严格一买（信号日≤T 且距今≤3天 → 新事件）
   - 第二层：指数一买激活期内（≤30天），49个板块合成指数 → 二买事件
   - 第三层：板块二买激活期内，板块成分股 → 三买信号（信号日≤T 且距今≤3天）
   每个日期 T 的结构分析只用 ≤T 的数据（截断后重跑 v5t），杜绝未来函数
3. 组合模拟：信号次日（下一根30min K线）开盘价买入；止损=ZG×(1-buffer)；
   超时=10个交易日收盘强制卖出；单票10%仓位，最多5仓；佣金万2.5双边+印花税千1卖出

输出：总收益率、胜率、最大回撤、平均持仓天数、逐笔明细 parquet、分年统计

幸存者偏差声明：板块成分为当前快照（新浪无历史成分），已剔除回测起点后上市个股，
但无法剔除回测期内退市/调出个股，绝对收益偏乐观，参数间相对比较仍有意义。

用法：
    python3 funnel_backtest.py --start 2025-08-01 --end 2026-07-18
    python3 funnel_backtest.py --start 2025-08-01 --end 2026-07-18 --download  # 先补数据
"""
import argparse
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import yaml

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
TECH_DIR = os.path.abspath(os.path.join(CURRENT_DIR, ".."))
sys.path.insert(0, TECH_DIR)
sys.path.insert(0, CURRENT_DIR)

from macd_divergence import MACDDivergence  # noqa: E402
from funnel_data import FunnelData  # noqa: E402
from sector_mapper import SectorMapper, get_list_dates  # noqa: E402
from strategy_funnel_30min import (  # noqa: E402
    ChanStructure30Min, scan_buy1, scan_buy2, scan_buy3,
    DEFAULT_CONFIG,
)

NEW_SIGNAL_DAYS = 3          # 信号日距今≤N天算"新信号"
LAYER2_ACTIVE_DAYS = 30      # 指数一买激活期
LAYER3_ACTIVE_DAYS = 30      # 板块二买激活期


class FunnelBacktest:
    def __init__(self, config_path: str = DEFAULT_CONFIG,
                 start: str = "2025-08-01", end: str = "2026-07-18"):
        with open(config_path, encoding="utf-8") as f:
            self.cfg = yaml.safe_load(f)
        macd_cfg = self.cfg.get("macd", {})
        self.macd = MACDDivergence(macd_cfg.get("fast", 12), macd_cfg.get("slow", 26),
                                   macd_cfg.get("signal", 9))
        self.data = FunnelData(sleep_sec=self.cfg.get("data", {}).get("sleep_sec", 0.35))
        self.mapper = SectorMapper()
        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        self.stock_data: Dict[str, pd.DataFrame] = {}   # code -> 30min df
        self.index_data: Dict[str, pd.DataFrame] = {}
        self.sector_members: Dict[str, List[Dict]] = {}
        self.sector_names: Dict[str, str] = {}
        self.trade_cal: List[pd.Timestamp] = []

    # ------------------------------------------------------------------
    def prepare(self, download: bool = False):
        """准备数据：板块成分（sw 源用时点成分）+ 全部30min缓存"""
        source = self.cfg.get("sector", {}).get("source", "sw")
        self.sw_mode = (source == "sw")
        if self.sw_mode:
            sectors = self.mapper.get_industries_sw()
            wl = set(self.cfg.get("sector", {}).get("whitelist") or [])
            bl = set(self.cfg.get("sector", {}).get("blacklist") or [])
            if wl:
                sectors = [s for s in sectors if s["code"] in wl]
            if bl:
                sectors = [s for s in sectors if s["code"] not in bl]
            # K线缓存需要窗口内所有时点成分的并集（按月采样）
            sample_dates = [self.start.strftime("%Y%m%d"), self.end.strftime("%Y%m%d")]
            cur = self.start.normalize() + pd.offsets.MonthBegin()
            while cur <= self.end:
                sample_dates.append(cur.strftime("%Y%m%d"))
                cur = cur + pd.offsets.MonthBegin()
            union = self.mapper.sw_union_members(sample_dates)
            all_codes = set()
            for s in sectors:
                members = union.get(s["code"], [])
                self.sector_members[s["code"]] = members  # 并集（用于K线预载）
                self.sector_names[s["code"]] = s["name"]
                all_codes.update(m["code"] for m in members)
            print(f"[板块] 申万一级 {len(sectors)} 个行业，窗口内成分并集 {len(all_codes)} 只")
        else:
            # 成分优先用本地快照（今天已采集过就不碰网络，防限流）
            snap = None
            latest_snap = self.mapper.latest_snapshot_date()
            if latest_snap:
                snap = self.mapper.load_snapshot(latest_snap)
            if snap is not None and len(snap):
                sector_codes = list(dict.fromkeys(snap["sector_code"]))
                sectors = [{"code": c, "name": snap[snap["sector_code"] == c]["sector_name"].iloc[0]}
                           for c in sector_codes]
            else:
                sectors = self.mapper.get_industries()
            wl = set(self.cfg.get("sector", {}).get("whitelist") or [])
            bl = set(self.cfg.get("sector", {}).get("blacklist") or [])
            if wl:
                sectors = [s for s in sectors if s["code"] in wl]
            if bl:
                sectors = [s for s in sectors if s["code"] not in bl]
            list_dates = get_list_dates()
            min_list_date = self.start.strftime("%Y%m%d")
            all_codes = set()
            for s in sectors:
                if snap is not None and len(snap):
                    sub = snap[snap["sector_code"] == s["code"]]
                    members = [{"code": r["stock_code"], "name": r["stock_name"]}
                               for _, r in sub.iterrows()]
                else:
                    members = self.mapper.get_constituents(s["code"])
                members = self.mapper.filter_by_list_date(members, min_list_date, list_dates)
                self.sector_members[s["code"]] = members
                self.sector_names[s["code"]] = s["name"]
                all_codes.update(m["code"] for m in members)
            if snap is None or not len(snap):
                self.mapper.save_snapshot(
                    {s["code"]: self.sector_members[s["code"]] for s in sectors},
                    sector_names=self.sector_names)

        index_codes = self.cfg.get("index_codes", [])
        if download:
            print(f"[数据] 批量更新 {len(index_codes)} 指数 + {len(all_codes)} 成分股 30min ...")
            t0 = time.time()
            for code in index_codes:
                self.data.update(code)
            stats = self.data.batch_update(sorted(all_codes), workers=8)
            ok = sum(1 for v in stats.values() if v > 0)
            print(f"[数据] 完成 {ok}/{len(all_codes)}，耗时 {time.time()-t0:.0f}s")

        # 预载入内存
        for code in index_codes:
            df = self.data.get(code, refresh=False)
            if not df.empty:
                self.index_data[code] = df
        for code in sorted(all_codes):
            try:
                df = self.data.get(code, refresh=False)
                if len(df) >= 60:
                    self.stock_data[code] = df
            except Exception:  # noqa: BLE001
                continue
        print(f"[数据] 内存预载：指数 {len(self.index_data)}，个股 {len(self.stock_data)}")

        # 交易日历：用沪深300的30min日期
        base = self.index_data.get("000300.SH")
        if base is None or base.empty:
            base = next(iter(self.index_data.values()))
        days = sorted(set(pd.Timestamp(d).normalize() for d in base["datetime"]))
        self.trade_cal = [d for d in days if self.start <= d <= self.end]
        print(f"[回测] 交易日 {len(self.trade_cal)} 天: {self.trade_cal[0]:%Y-%m-%d} ~ {self.trade_cal[-1]:%Y-%m-%d}")

    # ------------------------------------------------------------------
    def _structure_at(self, df: pd.DataFrame, day: pd.Timestamp, lookback: int,
                      symbol: str, inst: str) -> Optional[ChanStructure30Min]:
        cutoff = day + pd.Timedelta(hours=15)
        sub = df[df["datetime"] <= cutoff].tail(lookback)
        if len(sub) < 60:
            return None
        try:
            return ChanStructure30Min(sub, self.cfg.get("engine", {}), self.macd,
                                      symbol=symbol, instrument_type=inst)
        except Exception:  # noqa: BLE001
            return None

    def _sector_index_at(self, sector_code: str, day: pd.Timestamp,
                         lookback: int, members: Optional[List[Dict]] = None) -> Optional[pd.DataFrame]:
        """内存内等权合成板块指数（截断到 day）。members 缺省时用 self.sector_members。"""
        if members is None:
            members = self.sector_members.get(sector_code, [])
        series = []
        cutoff = day + pd.Timedelta(hours=15)
        for m in members:
            df = self.stock_data.get(m["code"])
            if df is None:
                continue
            sub = df[df["datetime"] <= cutoff].tail(lookback)
            if len(sub) < 60:
                continue
            f = 1000.0 / sub["close"].iloc[0]
            tmp = sub[["datetime"]].copy()
            for c in ["open", "high", "low", "close"]:
                tmp[c] = sub[c] * f
            series.append(tmp)
        if len(series) < self.cfg.get("sector", {}).get("min_members_with_data", 3):
            return None
        merged = series[0]
        for tmp in series[1:]:
            merged = merged.merge(tmp, on="datetime", how="outer", suffixes=("", "_r"))
            for c in ["open", "high", "low", "close"]:
                merged[c] = merged[[c, c + "_r"]].mean(axis=1)
                merged = merged.drop(columns=[c + "_r"])
        merged["volume"] = 0.0
        return merged.sort_values("datetime").reset_index(drop=True)

    # ------------------------------------------------------------------
    @staticmethod
    def _permissive_cfg(cfg: Dict) -> Dict:
        """原始事件收集用的宽松门限：结构条件保留，数值门限放宽并记录指标，
        供事后按参数组合过滤（参数敏感性分析）。"""
        import copy
        c = copy.deepcopy(cfg)
        c.setdefault("buy1", {})
        c["buy1"]["min_pivots"] = 1            # 超集：n_pivots 记入事件
        c["buy1"]["divergence_rate"] = 1.0
        c["buy1"]["macd_below_zero"] = False   # below_zero 记入事件
        c.setdefault("buy2", {})
        c["buy2"]["max_retrace_ratio"] = 1.0
        c.setdefault("buy3", {})
        c["buy3"]["macd_confirm"] = False      # macd_ratio 记入事件
        return c

    def collect_signals(self) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """逐日扫描，返回 (指数一买事件, 板块二买事件, 个股三买信号)。

        用宽松门限收集原始事件（含 n_pivots/below_zero/divergence_rate/
        retrace_ratio/macd_ratio 指标），过滤在 simulate 阶段做。
        """
        raw_cfg = self._permissive_cfg(self.cfg)
        buy1_cfg = raw_cfg["buy1"]
        buy2_cfg = raw_cfg["buy2"]
        buy3_cfg = raw_cfg["buy3"]
        lb1 = buy1_cfg.get("lookback_bars", 800)
        lb3 = buy3_cfg.get("lookback_bars", 400)

        index_events: List[Dict] = []
        sector_events: List[Dict] = []
        stock_signals: List[Dict] = []
        seen_b1, seen_b2, seen_b3 = set(), set(), set()
        pit_cache: Dict[Tuple, List[Dict]] = {}

        def pit_members(sc: str, day_str: str) -> List[Dict]:
            """sw 模式用时点成分（按天缓存）；sina 模式用并集成员"""
            if self.sw_mode:
                key = (sc, day_str)
                if key not in pit_cache:
                    pit_cache[key] = self.mapper.get_constituents_pit(sc, day_str)
                return pit_cache[key]
            return self.sector_members.get(sc, [])

        for day in self.trade_cal:
            day_str = day.strftime("%Y%m%d")
            # ---- 第一层 ----
            for code, df in self.index_data.items():
                st = self._structure_at(df, day, lb1, code, "index")
                if st is None:
                    continue
                for b1 in scan_buy1(st, buy1_cfg):
                    key = (code, b1["date"])
                    if key in seen_b1:
                        continue
                    if 0 <= (day - b1["date"].normalize()).days <= NEW_SIGNAL_DAYS:
                        seen_b1.add(key)
                        index_events.append({"code": code, "detect_day": day, **b1})
                        print(f"[一买] {code} 信号日 {b1['date']:%Y-%m-%d %H:%M} 衰减 {b1['divergence_rate']}")

            # ---- 第二层 ----
            active_b1 = [e for e in index_events
                         if 0 <= (day - e["date"].normalize()).days <= LAYER2_ACTIVE_DAYS]
            if not active_b1:
                continue
            anchor_b1 = max(active_b1, key=lambda e: e["date"])
            for sector_code in self.sector_members:
                sdf = self._sector_index_at(sector_code, day, lb1,
                                            members=pit_members(sector_code, day_str))
                if sdf is None:
                    continue
                st = self._structure_at(sdf, day, lb1, sector_code, "index")
                if st is None:
                    continue
                for b2 in scan_buy2(st, anchor_b1["date"], buy2_cfg, buy1_cfg):
                    key = (sector_code, b2["date"])
                    if key in seen_b2:
                        continue
                    if 0 <= (day - b2["date"].normalize()).days <= NEW_SIGNAL_DAYS:
                        seen_b2.add(key)
                        sector_events.append({"code": sector_code,
                                              "name": self.sector_names.get(sector_code, ""),
                                              "detect_day": day,
                                              "anchor_index_code": anchor_b1["code"],
                                              "anchor_index_date": anchor_b1["date"],
                                              **b2})
                        print(f"[二买] {self.sector_names.get(sector_code)} 信号日 {b2['date']:%Y-%m-%d}")

            # ---- 第三层 ----
            active_b2 = [e for e in sector_events
                         if 0 <= (day - e["date"].normalize()).days <= LAYER3_ACTIVE_DAYS]
            if not active_b2:
                continue
            for e in active_b2:
                for m in pit_members(e["code"], day_str):
                    df = self.stock_data.get(m["code"])
                    if df is None:
                        continue
                    st = self._structure_at(df, day, lb3, m["code"], "stock")
                    if st is None:
                        continue
                    for b3 in scan_buy3(st, buy3_cfg):
                        key = (m["code"], b3["date"])
                        if key in seen_b3:
                            continue
                        if 0 <= (day - b3["date"].normalize()).days <= NEW_SIGNAL_DAYS:
                            seen_b3.add(key)
                            stock_signals.append({
                                "code": m["code"], "name": m.get("name", ""),
                                "sector": e["name"],
                                "sector_code": e["code"],
                                "sector_b2_date": e["date"],
                                "anchor_index_code": e.get("anchor_index_code"),
                                "anchor_index_date": e.get("anchor_index_date"),
                                "detect_day": day, **b3})
                            print(f"[三买] {m.get('name', m['code'])} 信号日 {b3['date']:%Y-%m-%d %H:%M} "
                                  f"价 {b3['price']:.2f} 止损 {b3['stop_loss']:.2f}")

        return index_events, sector_events, stock_signals

    # ------------------------------------------------------------------
    def simulate(self, signals: List[Dict], sector_events: Optional[List[Dict]] = None,
                 index_events: Optional[List[Dict]] = None,
                 filters: Optional[Dict] = None) -> Dict:
        """组合模拟：下一根30min开盘价入场，止损/超时出场。

        filters（参数敏感性用）:
            min_pivots: 指数一买最少中枢数（默认2）
            divergence_rate: 一买MACD衰减阈值（默认0.9）
            macd_below_zero: 是否要求一买在0轴下（默认True）
            max_retrace_ratio: 板块二买回调阈值（默认0.618）
            macd_confirm: 三买MACD确认（默认True）
        """
        f = {
            "min_pivots": self.cfg.get("buy1", {}).get("min_pivots", 2),
            "divergence_rate": self.cfg.get("buy1", {}).get("divergence_rate", 0.9),
            "macd_below_zero": self.cfg.get("buy1", {}).get("macd_below_zero", True),
            "max_retrace_ratio": self.cfg.get("buy2", {}).get("max_retrace_ratio", 0.618),
            "macd_confirm": self.cfg.get("buy3", {}).get("macd_confirm", True),
        }
        if filters:
            f.update(filters)

        # 事件过滤（沿血缘链逐层传导：个股三买 → 其板块二买 → 其锚定指数一买）
        if index_events is not None and sector_events is not None:
            ok_index_keys = {
                (e["code"], pd.Timestamp(e["date"]))
                for e in index_events
                if e.get("n_pivots", 2) >= f["min_pivots"]
                and e.get("divergence_rate", 0) < f["divergence_rate"]
                and (not f["macd_below_zero"] or e.get("below_zero", True))
            }
            ok_sector_keys = {
                (e["code"], pd.Timestamp(e["date"]))
                for e in sector_events
                if e.get("retrace_ratio", 0) < f["max_retrace_ratio"]
                and (e.get("anchor_index_code"), pd.Timestamp(e.get("anchor_index_date"))) in ok_index_keys
            }
            signals = [s for s in signals
                       if (s.get("sector_code"), pd.Timestamp(s.get("sector_b2_date"))) in ok_sector_keys]
        if f["macd_confirm"]:
            signals = [s for s in signals
                       if s.get("macd_ratio") is not None and s["macd_ratio"] < 1.0]
        tr = self.cfg.get("trade", {})
        hold_bars = tr.get("hold_days_max", 10) * tr.get("bars_per_day", 8)
        pos_pct = tr.get("position_per_stock", 0.1)
        max_pos = tr.get("max_positions", 5)
        comm = tr.get("commission", 0.00025)
        tax = tr.get("stamp_tax", 0.001)
        capital0 = 1_000_000.0

        # 每个信号找入场点（信号日之后第一根K线开盘价）
        entries = []
        for sig in signals:
            df = self.stock_data.get(sig["code"])
            if df is None:
                continue
            pos = df.index[df["datetime"] > sig["date"]]
            if len(pos) < 2:
                continue
            entry_i = pos[0]
            if df["datetime"].iloc[entry_i].normalize() > self.end:
                continue
            entries.append({**sig, "entry_i": entry_i,
                            "entry_date": df["datetime"].iloc[entry_i],
                            "entry_price": float(df["open"].iloc[entry_i])})
        entries.sort(key=lambda e: e["entry_date"])

        cash = capital0
        positions: List[Dict] = []   # {code, shares, entry_price, stop, entry_i, df, name, sector}
        trades: List[Dict] = []
        equity_curve: List[Dict] = []

        def close_position(p, exit_i, exit_price, reason):
            nonlocal cash
            gross = p["shares"] * exit_price
            cost = gross * (comm + tax)
            cash += gross - cost
            ret = (exit_price / p["entry_price"] - 1) - (2 * comm + tax)
            trades.append({
                "code": p["code"], "name": p["name"], "sector": p["sector"],
                "signal_date": p["signal_date"], "entry_date": p["entry_date"],
                "entry_price": p["entry_price"],
                "exit_date": p["df"]["datetime"].iloc[exit_i],
                "exit_price": round(exit_price, 4), "exit_reason": reason,
                "return": round(ret, 4),
                "hold_days": round((p["df"]["datetime"].iloc[exit_i] - p["entry_date"]).days, 1),
            })

        # 事件循环：按时间推进（以沪深300的30min时间轴为时钟）
        clock = sorted(set(d for df in self.index_data.values() for d in df["datetime"]))
        clock = [d for d in clock if self.start <= d <= self.end + pd.Timedelta(days=1)]
        entry_queue = list(entries)
        ei = 0
        for now in clock:
            # 1) 持仓检查（止损/超时）
            for p in list(positions):
                df = p["df"]
                future = df.index[(df["datetime"] > p["entry_date"]) & (df["datetime"] <= now)]
                if len(future) == 0:
                    continue
                cur_i = future[-1]
                bars_held = cur_i - p["entry_i"] + 1
                row = df.loc[cur_i]
                if row["low"] <= p["stop"]:
                    close_position(p, cur_i, p["stop"], "stop_loss")
                    positions.remove(p)
                elif bars_held >= hold_bars:
                    close_position(p, cur_i, float(row["close"]), "timeout")
                    positions.remove(p)
            # 2) 新入场
            while ei < len(entry_queue) and entry_queue[ei]["entry_date"] <= now:
                e = entry_queue[ei]
                ei += 1
                if len(positions) >= max_pos:
                    continue
                if any(p["code"] == e["code"] for p in positions):
                    continue
                alloc = capital0 * pos_pct
                if alloc > cash:
                    continue
                shares = int(alloc / e["entry_price"] / 100) * 100
                if shares <= 0:
                    continue
                cost = shares * e["entry_price"] * (1 + comm)
                if cost > cash:
                    continue
                cash -= cost
                positions.append({
                    "code": e["code"], "name": e["name"], "sector": e["sector"],
                    "shares": shares, "entry_price": e["entry_price"],
                    "stop": e["stop_loss"], "entry_i": e["entry_i"],
                    "entry_date": e["entry_date"], "signal_date": e["date"],
                    "df": self.stock_data[e["code"]],
                })
            # 3) 每日收盘记净值
            if now.hour == 15:
                mv = cash
                for p in positions:
                    df = p["df"]
                    past = df.index[df["datetime"] <= now]
                    if len(past):
                        mv += p["shares"] * float(df["close"].loc[past[-1]])
                equity_curve.append({"date": now.normalize(), "equity": mv})

        # 期末强制平仓
        for p in list(positions):
            df = p["df"]
            past = df.index[df["datetime"] <= self.end + pd.Timedelta(hours=15)]
            if len(past):
                close_position(p, past[-1], float(df["close"].loc[past[-1]]), "end_of_backtest")
            positions.remove(p)

        eq = pd.DataFrame(equity_curve)
        tdf = pd.DataFrame(trades)
        return self._metrics(eq, tdf, capital0)

    # ------------------------------------------------------------------
    @staticmethod
    def _metrics(eq: pd.DataFrame, tdf: pd.DataFrame, capital0: float) -> Dict:
        out: Dict = {"trades": tdf, "equity": eq}
        if eq.empty:
            out.update(message="无净值数据")
            return out
        total_ret = eq["equity"].iloc[-1] / capital0 - 1
        roll_max = eq["equity"].cummax()
        max_dd = float((eq["equity"] / roll_max - 1).min())
        out["total_return"] = round(float(total_ret), 4)
        out["max_drawdown"] = round(max_dd, 4)
        if tdf.empty:
            out.update(n_trades=0, message="无成交")
            return out
        out["n_trades"] = len(tdf)
        out["win_rate"] = round(float((tdf["return"] > 0).mean()), 3)
        out["avg_return"] = round(float(tdf["return"].mean()), 4)
        out["avg_hold_days"] = round(float(tdf["hold_days"].mean()), 1)
        out["exit_reasons"] = tdf["exit_reason"].value_counts().to_dict()
        tdf2 = tdf.copy()
        tdf2["year"] = pd.to_datetime(tdf2["exit_date"]).dt.year
        out["by_year"] = tdf2.groupby("year")["return"].agg(["count", "mean"]).round(4).to_dict()
        return out

    # ------------------------------------------------------------------
    def run(self, download: bool = False, events_out: Optional[str] = None) -> Dict:
        self.prepare(download=download)
        t0 = time.time()
        index_events, sector_events, stock_signals = self.collect_signals()
        print(f"\n[信号] 指数一买 {len(index_events)}，板块二买 {len(sector_events)}，"
              f"个股三买 {len(stock_signals)}（扫描耗时 {time.time()-t0:.0f}s）")
        if events_out:
            self.save_events(events_out, index_events, sector_events, stock_signals)
        result = self.simulate(stock_signals, sector_events, index_events)
        result["index_events"] = index_events
        result["sector_events"] = sector_events
        return result

    @staticmethod
    def save_events(path: str, index_events: List[Dict], sector_events: List[Dict],
                    stock_signals: List[Dict]):
        def _df(rows):
            df = pd.DataFrame(rows)
            for c in ["date", "detect_day", "buy1_date", "signal_date"]:
                if c in df.columns:
                    df[c] = pd.to_datetime(df[c])
            return df
        with pd.HDFStore(path, "w") as store:
            store["index_events"] = _df(index_events)
            store["sector_events"] = _df(sector_events)
            store["stock_signals"] = _df(stock_signals)
        print(f"[事件] 已保存: {path}")

    @staticmethod
    def load_events(path: str) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        with pd.HDFStore(path, "r") as store:
            return (store["index_events"].to_dict("records"),
                    store["sector_events"].to_dict("records"),
                    store["stock_signals"].to_dict("records"))


def main():
    parser = argparse.ArgumentParser(description="30min 三层漏斗回测")
    parser.add_argument("--config", default=DEFAULT_CONFIG)
    parser.add_argument("--start", default="2025-08-01")
    parser.add_argument("--end", default="2026-07-18")
    parser.add_argument("--download", action="store_true", help="先批量更新30min数据")
    parser.add_argument("--events-out", default=None, help="原始事件保存路径(.h5)")
    parser.add_argument("--out-dir", default=os.path.join(CURRENT_DIR, "输出"))
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    bt = FunnelBacktest(args.config, args.start, args.end)
    result = bt.run(download=args.download, events_out=args.events_out)

    tag = f"{args.start}_{args.end}".replace("-", "")
    trades = result.get("trades")
    if trades is not None and not trades.empty:
        path = os.path.join(args.out_dir, f"funnel_trades_{tag}.parquet")
        trades.to_parquet(path, index=False)
        print(f"\n逐笔明细: {path}")
    eq = result.get("equity")
    if eq is not None and not eq.empty:
        path = os.path.join(args.out_dir, f"funnel_equity_{tag}.parquet")
        eq.to_parquet(path, index=False)

    print("\n===== 回测结果 =====")
    for k in ["total_return", "max_drawdown", "n_trades", "win_rate",
              "avg_return", "avg_hold_days", "exit_reasons", "by_year", "message"]:
        if k in result:
            print(f"{k}: {result[k]}")
    print(f"指数一买事件: {len(result.get('index_events', []))}, "
          f"板块二买事件: {len(result.get('sector_events', []))}")


if __name__ == "__main__":
    main()
