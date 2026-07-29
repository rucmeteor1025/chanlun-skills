# -*- coding: utf-8 -*-
"""
缠论分析核心模块 v5t - 面向实战的优化版本

优化方向：
1. 合并K线的方向与时间归属更稳健
2. 中枢有效性和级别过滤真正接入主流程
3. 日线中枢分级对股票更保守，减少3笔日线中枢泛滥
4. 买卖点扫描范围更宽，边界容忍度按中枢幅度自适应
"""
import pandas as pd
import numpy as np
from typing import List, Dict, Tuple, Optional


class ChanCoreV5T:
    """缠论核心分析 v5t - 面向实战的优化版本"""
    
    def __init__(
        self,
        df: pd.DataFrame,
        min_amplitude: float = 0.005,
        pivot_min_amplitude: float = 0.02,
        pivot_min_strokes: int = 3,
        pivot_max_strokes: int = 9,
        pivot_amplitude_tolerance: float = 0.5,
        dynamic_min_amplitude: bool = False,
        symbol: Optional[str] = None,
        instrument_type: str = "auto",
        pivot_daily_min_amplitude: Optional[float] = None,
        pivot_daily_min_strokes: Optional[int] = None,
        pivot_init_filter_ratio: float = 2.0,
        pivot_extend_filter_ratio: float = 1.5,
        pivot_final_filter_ratio: float = 2.0,
        dynamic_pivot_rules: bool = True,
    ):
        """
        初始化
        
        Args:
            df: 原始K线数据
            min_amplitude: 笔的最小幅度（默认0.5%）
            pivot_min_amplitude: 中枢的最小波动幅度（默认2%）
            pivot_min_strokes: 中枢的最小笔数（默认3）
            pivot_max_strokes: 中枢的最大笔数（默认9）
            pivot_amplitude_tolerance: 同级别中枢幅度偏差容忍度（默认50%）
            dynamic_min_amplitude: 是否启用动态笔幅阈值（基于历史波动率）
            symbol: 标的代码（用于自动识别指数/个股）
            instrument_type: 标的类型（auto/index/stock）
            pivot_daily_min_amplitude: 日线级别中枢最小幅度（None时自动计算）
            pivot_daily_min_strokes: 日线级别中枢最小笔数（None时自动计算）
            pivot_init_filter_ratio: 前3笔初筛时，最大笔幅度/中枢宽度容忍倍数
            pivot_extend_filter_ratio: 中枢扩展时，单笔幅度/中枢宽度容忍倍数
            pivot_final_filter_ratio: 中枢成型后复核时，最大笔幅度/中枢宽度容忍倍数
            dynamic_pivot_rules: 是否启用动态中枢分级与过滤（按标的+波动率）
        """
        self.df_raw = df.copy()
        self.min_amplitude = min_amplitude
        self.pivot_min_amplitude = pivot_min_amplitude
        self.pivot_min_strokes = pivot_min_strokes
        self.pivot_max_strokes = pivot_max_strokes
        self.pivot_amplitude_tolerance = pivot_amplitude_tolerance
        self.dynamic_min_amplitude = dynamic_min_amplitude
        self.symbol = symbol
        self.instrument_type = instrument_type
        self.pivot_daily_min_amplitude = pivot_daily_min_amplitude
        self.pivot_daily_min_strokes = pivot_daily_min_strokes
        self.pivot_init_filter_ratio = pivot_init_filter_ratio
        self.pivot_extend_filter_ratio = pivot_extend_filter_ratio
        self.pivot_final_filter_ratio = pivot_final_filter_ratio
        self.dynamic_pivot_rules = dynamic_pivot_rules
        self.effective_min_amplitude = min_amplitude
        self.dynamic_threshold_meta = {}
        self.effective_pivot_rules = {}
        self.trade_dates = pd.Index([])
        
        self.df_merged = None
        self.fractals = []
        self.strokes = []
        self.raw_pivots = []
        self.pivots = []
        self.sub_level_pivots = []  # 次级别中枢列表
        self.buy_sell_points = []  # 买卖点列表
        self.trend_type = None  # 走势类型
        
        self._standardize_columns()
        self.effective_min_amplitude, self.dynamic_threshold_meta = self._compute_effective_min_amplitude()
        self.effective_pivot_rules = self._compute_effective_pivot_rules()
    
    def _standardize_columns(self):
        """标准化列名"""
        column_map = {
            'Date': 'date', 'date': 'date',
            'Open': 'open', 'open': 'open',
            'High': 'high', 'high': 'high',
            'Low': 'low', 'low': 'low',
            'Close': 'close', 'close': 'close',
            'Volume': 'volume', 'volume': 'volume',
        }
        
        for old_col, new_col in column_map.items():
            if old_col in self.df_raw.columns:
                self.df_raw.rename(columns={old_col: new_col}, inplace=True)
        
        required_cols = ['date', 'open', 'high', 'low', 'close', 'volume']
        for col in required_cols:
            if col not in self.df_raw.columns:
                raise ValueError(f"缺少必要列: {col}")

        self.df_raw['date'] = pd.to_datetime(self.df_raw['date'])
        self.df_raw.sort_values('date', inplace=True)
        self.df_raw.reset_index(drop=True, inplace=True)
        self.trade_dates = pd.Index(self.df_raw['date'].drop_duplicates())

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _stroke_strength(stroke: Dict) -> float:
        """用单位K线幅度近似力度，给实战版背驰判断一个基础约束。"""
        return float(stroke['amplitude']) / max(int(stroke.get('kline_count', 1)), 1)

    @staticmethod
    def _detect_inclusion_uptrend(prev_prev: Dict, prev_merged: Dict, curr: pd.Series) -> bool:
        """用高低点关系优先判方向，避免只看 high 导致震荡区误判。"""
        if prev_merged["high"] >= prev_prev["high"] and prev_merged["low"] >= prev_prev["low"]:
            return True
        if prev_merged["high"] <= prev_prev["high"] and prev_merged["low"] <= prev_prev["low"]:
            return False
        if curr["high"] >= prev_merged["high"] and curr["low"] >= prev_merged["low"]:
            return True
        if curr["high"] <= prev_merged["high"] and curr["low"] <= prev_merged["low"]:
            return False
        return bool(curr["close"] >= prev_merged["close"])

    def _infer_instrument_type(self) -> str:
        """自动推断标的类型（index/stock）。"""
        if self.instrument_type in {"index", "stock"}:
            return self.instrument_type

        code = (self.symbol or "").upper()
        if not code:
            return "stock"

        if code.endswith(".CSI") or code.endswith(".SI"):
            return "index"
        if code.endswith(".SZ") and code.startswith("399"):
            return "index"
        if code.endswith(".SH") and code.startswith("000"):
            return "index"
        return "stock"

    def _get_hv_series(self, window: int) -> pd.Series:
        """优先读取已有HV列，否则用对数收益率计算年化波动率。"""
        hv_candidates = (
            [f"hv{window}", f"HV{window}", f"hist_vol_{window}", f"volatility_{window}", f"realized_vol_{window}"]
        )
        for col in hv_candidates:
            if col in self.df_raw.columns:
                s = pd.to_numeric(self.df_raw[col], errors="coerce")
                if s.notna().sum() > 10:
                    return s

        close = pd.to_numeric(self.df_raw["close"], errors="coerce")
        log_ret = np.log(close / close.shift(1))
        return log_ret.rolling(window, min_periods=max(5, window // 2)).std() * np.sqrt(252)

    def _compute_effective_min_amplitude(self) -> Tuple[float, Dict]:
        """
        动态笔幅阈值：
        min_amp = clip(base_by_asset * sqrt(HV60/median(HV60)) * clamp(HV20/HV60, 0.8, 1.4), floor, cap)
        """
        meta = {"dynamic_enabled": bool(self.dynamic_min_amplitude)}
        if not self.dynamic_min_amplitude:
            meta["reason"] = "dynamic_disabled"
            return self.min_amplitude, meta

        try:
            inst = self._infer_instrument_type()
            if inst == "index":
                base, floor, cap = 0.004, 0.0025, 0.012
            else:
                base, floor, cap = 0.008, 0.004, 0.03

            hv20_s = self._get_hv_series(20)
            hv60_s = self._get_hv_series(60)
            hv20 = float(hv20_s.dropna().iloc[-1]) if not hv20_s.dropna().empty else np.nan
            hv60_valid = hv60_s.dropna()
            hv60 = float(hv60_valid.iloc[-1]) if not hv60_valid.empty else np.nan
            hv60_med = float(hv60_valid.median()) if not hv60_valid.empty else np.nan

            if np.isnan(hv20) or np.isnan(hv60) or np.isnan(hv60_med) or hv60 <= 0 or hv60_med <= 0:
                meta.update(
                    {
                        "reason": "hv_unavailable",
                        "instrument_type": inst,
                        "base": base,
                        "floor": floor,
                        "cap": cap,
                    }
                )
                return self.min_amplitude, meta

            regime_factor = np.sqrt(hv60 / hv60_med)
            regime_factor = self._clip(float(regime_factor), 0.7, 1.6)
            recent_factor = self._clip(float(hv20 / hv60), 0.8, 1.4)
            effective = self._clip(base * regime_factor * recent_factor, floor, cap)

            meta.update(
                {
                    "reason": "ok",
                    "instrument_type": inst,
                    "base": base,
                    "floor": floor,
                    "cap": cap,
                    "hv20": hv20,
                    "hv60": hv60,
                    "hv60_median": hv60_med,
                    "regime_factor": regime_factor,
                    "recent_factor": recent_factor,
                    "effective_min_amplitude": effective,
                }
            )
            return effective, meta
        except Exception as e:
            meta.update({"reason": f"dynamic_error:{e}"})
            return self.min_amplitude, meta

    def _compute_effective_pivot_rules(self) -> Dict:
        """
        计算生效的中枢分级/过滤规则。

        目标：
        1. 去除硬编码，全部参数化；
        2. 指数/个股采用不同默认强度；
        3. 可选按波动率动态放宽或收紧过滤。
        """
        inst = self._infer_instrument_type()
        base_daily_amp = (
            float(self.pivot_daily_min_amplitude)
            if self.pivot_daily_min_amplitude is not None
            else float(self.pivot_min_amplitude)
        )
        base_daily_strokes = (
            int(self.pivot_daily_min_strokes)
            if self.pivot_daily_min_strokes is not None
            else int(self.pivot_min_strokes)
        )
        base_daily_strokes = max(3, base_daily_strokes)
        if self.pivot_daily_min_strokes is None:
            if inst == "index":
                base_daily_strokes = max(base_daily_strokes, 4)
            else:
                base_daily_strokes = max(base_daily_strokes, 4)

        init_ratio = float(self.pivot_init_filter_ratio)
        extend_ratio = float(self.pivot_extend_filter_ratio)
        final_ratio = float(self.pivot_final_filter_ratio)
        meta = {"dynamic_enabled": bool(self.dynamic_pivot_rules), "instrument_type": inst}

        if self.dynamic_pivot_rules:
            if inst == "stock":
                # 实战中个股波动虽大，但日线级别不宜过度放宽。
                base_daily_amp *= 0.85
                init_ratio *= 1.20
                extend_ratio *= 1.25
                final_ratio *= 1.20
            else:
                # 指数波动相对稳定，保持接近默认规则
                base_daily_amp *= 0.95
                init_ratio *= 1.02
                extend_ratio *= 1.05
                final_ratio *= 1.02

            hv20_s = self._get_hv_series(20)
            hv60_s = self._get_hv_series(60)
            hv20 = float(hv20_s.dropna().iloc[-1]) if not hv20_s.dropna().empty else np.nan
            hv60 = float(hv60_s.dropna().iloc[-1]) if not hv60_s.dropna().empty else np.nan
            if not np.isnan(hv20) and not np.isnan(hv60) and hv60 > 0:
                vol_ratio = self._clip(float(hv20 / hv60), 0.75, 1.35)
                base_daily_amp *= np.sqrt(vol_ratio)
                ratio_factor = self._clip(1.0 + (vol_ratio - 1.0) * 0.6, 0.85, 1.25)
                init_ratio *= ratio_factor
                extend_ratio *= ratio_factor
                final_ratio *= ratio_factor
                meta.update(
                    {
                        "vol_ratio_hv20_hv60": vol_ratio,
                        "ratio_factor": ratio_factor,
                        "hv20": hv20,
                        "hv60": hv60,
                    }
                )
            else:
                meta["volatility_adjustment"] = "hv_unavailable"
        else:
            meta["reason"] = "dynamic_disabled"

        # 日线中枢阈值需要高于笔阈值，避免中枢级别“降维”
        stroke_to_pivot_ratio = 2.6 if inst == "index" else 2.2
        daily_min_amp = max(base_daily_amp, self.effective_min_amplitude * stroke_to_pivot_ratio)
        amp_floor = 0.010 if inst == "index" else 0.012
        amp_cap = 0.050 if inst == "index" else 0.080
        daily_min_amp = self._clip(float(daily_min_amp), amp_floor, amp_cap)

        init_ratio = self._clip(float(init_ratio), 1.20, 4.50)
        extend_ratio = self._clip(float(extend_ratio), 1.00, 4.00)
        final_ratio = self._clip(float(final_ratio), 1.20, 4.50)

        return {
            "instrument_type": inst,
            "daily_min_amplitude": daily_min_amp,
            "daily_min_strokes": int(base_daily_strokes),
            "init_filter_ratio": init_ratio,
            "extend_filter_ratio": extend_ratio,
            "final_filter_ratio": final_ratio,
            "stroke_to_pivot_ratio": stroke_to_pivot_ratio,
            "base_daily_min_amplitude": float(
                self.pivot_daily_min_amplitude
                if self.pivot_daily_min_amplitude is not None
                else self.pivot_min_amplitude
            ),
            "dynamic_meta": meta,
        }
    
    def merge_klines(self) -> pd.DataFrame:
        """第一步：合并K线 - 处理包含关系"""
        if len(self.df_raw) < 2:
            self.df_merged = self.df_raw.copy()
            return self.df_merged
        
        merged = []
        merged.append(self.df_raw.iloc[0].to_dict())
        
        for i in range(1, len(self.df_raw)):
            curr = self.df_raw.iloc[i]
            prev_merged = merged[-1]
            
            # 判断包含关系
            curr_in_prev = (curr['high'] <= prev_merged['high'] and 
                           curr['low'] >= prev_merged['low'])
            prev_in_curr = (prev_merged['high'] <= curr['high'] and 
                           prev_merged['low'] >= curr['low'])
            
            has_inclusion = curr_in_prev or prev_in_curr
            
            if has_inclusion:
                # 判断趋势方向
                if len(merged) >= 2:
                    prev_prev = merged[-2]
                    is_uptrend = self._detect_inclusion_uptrend(prev_prev, prev_merged, curr)
                else:
                    is_uptrend = bool(curr['close'] >= prev_merged['close'])
                
                if is_uptrend:
                    merged[-1]['high'] = max(prev_merged['high'], curr['high'])
                    merged[-1]['low'] = max(prev_merged['low'], curr['low'])
                else:
                    merged[-1]['high'] = min(prev_merged['high'], curr['high'])
                    merged[-1]['low'] = min(prev_merged['low'], curr['low'])
                
                merged[-1]['close'] = curr['close']
                merged[-1]['volume'] += curr['volume']
                merged[-1]['date'] = curr['date']
            else:
                merged.append(curr.to_dict())
        
        self.df_merged = pd.DataFrame(merged)
        return self.df_merged
    
    def identify_fractals(self) -> List[Dict]:
        """第二步：标出顶底分型"""
        if self.df_merged is None:
            self.merge_klines()
        
        df = self.df_merged
        fractals = []
        
        if len(df) < 3:
            self.fractals = fractals
            return fractals
        
        for i in range(1, len(df) - 1):
            left = df.iloc[i - 1]
            mid = df.iloc[i]
            right = df.iloc[i + 1]
            
            # 顶分型
            if (mid['high'] > left['high'] and mid['high'] > right['high'] and
                mid['low'] > left['low'] and mid['low'] > right['low']):
                fractals.append({
                    'index': i,
                    'type': 'top',
                    'price': mid['high'],
                    'date': mid['date'],
                    'high': mid['high'],
                    'low': mid['low']
                })
            # 底分型
            elif (mid['low'] < left['low'] and mid['low'] < right['low'] and
                  mid['high'] < left['high'] and mid['high'] < right['high']):
                fractals.append({
                    'index': i,
                    'type': 'bottom',
                    'price': mid['low'],
                    'date': mid['date'],
                    'high': mid['high'],
                    'low': mid['low']
                })
        
        self.fractals = self._normalize_fractals(fractals)
        return self.fractals
    
    def identify_strokes(self) -> List[Dict]:
        """第三步：画笔（幅度过滤）"""
        if not self.fractals:
            self.identify_fractals()
        
        if len(self.fractals) < 2:
            self.strokes = []
            return []
        
        initial_strokes = []
        i = 0
        min_amp = self.effective_min_amplitude
        
        while i < len(self.fractals) - 1:
            start_fractal = self.fractals[i]
            j = i + 1
            candidates = []
            
            while j < len(self.fractals):
                end_fractal = self.fractals[j]
                kline_count = end_fractal['index'] - start_fractal['index']
                
                if end_fractal['type'] != start_fractal['type']:
                    if kline_count >= 4:
                        amplitude = abs(end_fractal['price'] - start_fractal['price']) / start_fractal['price']
                        
                        if amplitude >= min_amp:
                            candidates.append(j)
                else:
                    if candidates:
                        last_candidate_idx = candidates[-1]
                        last_candidate = self.fractals[last_candidate_idx]
                        gap_to_last = end_fractal['index'] - last_candidate['index']
                        
                        if gap_to_last >= 4:
                            break
                
                j += 1
            
            if candidates:
                if start_fractal['type'] == 'top':
                    best_j = min(candidates, key=lambda idx: self.fractals[idx]['price'])
                else:
                    best_j = max(candidates, key=lambda idx: self.fractals[idx]['price'])
                
                end_fractal = self.fractals[best_j]
                direction = 'up' if end_fractal['price'] > start_fractal['price'] else 'down'
                amplitude = abs(end_fractal['price'] - start_fractal['price']) / start_fractal['price']
                
                initial_strokes.append({
                    'start_idx': start_fractal['index'],
                    'end_idx': end_fractal['index'],
                    'start_price': start_fractal['price'],
                    'end_price': end_fractal['price'],
                    'start_date': start_fractal['date'],
                    'end_date': end_fractal['date'],
                    'direction': direction,
                    'start_type': start_fractal['type'],
                    'end_type': end_fractal['type'],
                    'kline_count': end_fractal['index'] - start_fractal['index'],
                    'amplitude': amplitude
                })
                
                i = best_j
            else:
                i += 1
        
        self.strokes = initial_strokes
        return initial_strokes
    
    def identify_pivots(self) -> List[Dict]:
        """
        第四步：识别中枢。

        规则：
        1. 中枢区间由前3笔固定（避免未来函数）；
        2. 使用笔端点计算重叠区间；
        3. 过滤阈值和分级阈值全部参数化，可按标的和波动率动态调整。
        """
        if not self.strokes:
            self.identify_strokes()

        if len(self.strokes) < 3:
            self.pivots = []
            self.sub_level_pivots = []
            return []

        rules = self.effective_pivot_rules or self._compute_effective_pivot_rules()
        daily_min_amp = float(rules["daily_min_amplitude"])
        daily_min_strokes = int(rules["daily_min_strokes"])
        init_filter_ratio = float(rules["init_filter_ratio"])
        extend_filter_ratio = float(rules["extend_filter_ratio"])
        final_filter_ratio = float(rules["final_filter_ratio"])

        all_pivots = []
        i = 0

        while i < len(self.strokes) - 2:
            stroke1 = self.strokes[i]
            stroke2 = self.strokes[i + 1]
            stroke3 = self.strokes[i + 2]

            ranges = []
            for stroke in [stroke1, stroke2, stroke3]:
                if stroke['direction'] == 'up':
                    low = stroke['start_price']
                    high = stroke['end_price']
                else:
                    low = stroke['end_price']
                    high = stroke['start_price']
                ranges.append({'high': high, 'low': low})

            zd = max(r['low'] for r in ranges)
            zg = min(r['high'] for r in ranges)

            if zd >= zg:
                i += 1
                continue

            pivot_width = zg - zd
            if pivot_width <= 0:
                i += 1
                continue

            max_stroke_amplitude = max(r['high'] - r['low'] for r in ranges)
            if max_stroke_amplitude > pivot_width * init_filter_ratio:
                i += 1
                continue

            pivot_zd = zd
            pivot_zg = zg
            pivot_amplitude = pivot_width / max(abs(zd), 1e-12)

            j = i + 3
            pivot_stroke_indices = [i, i + 1, i + 2]
            termination_reason = None

            while j < len(self.strokes):
                next_stroke = self.strokes[j]
                if next_stroke['direction'] == 'up':
                    stroke_low = next_stroke['start_price']
                    stroke_high = next_stroke['end_price']
                else:
                    stroke_low = next_stroke['end_price']
                    stroke_high = next_stroke['start_price']

                has_overlap = not (stroke_low > pivot_zg or stroke_high < pivot_zd)
                if not has_overlap:
                    termination_reason = f"笔{j+1}完全脱离中枢"
                    break

                stroke_amplitude = stroke_high - stroke_low
                if stroke_amplitude > pivot_width * extend_filter_ratio:
                    limit = pivot_width * extend_filter_ratio
                    termination_reason = f"笔{j+1}幅度过大（{stroke_amplitude:.2f} > {limit:.2f}）"
                    break

                pivot_stroke_indices.append(j)
                if self.pivot_max_strokes and len(pivot_stroke_indices) >= self.pivot_max_strokes:
                    termination_reason = f"达到笔数上限（{self.pivot_max_strokes}）"
                    break
                j += 1

            if len(pivot_stroke_indices) >= 3:
                all_ranges = []
                for idx in pivot_stroke_indices:
                    stroke = self.strokes[idx]
                    if stroke['direction'] == 'up':
                        low = stroke['start_price']
                        high = stroke['end_price']
                    else:
                        low = stroke['end_price']
                        high = stroke['start_price']
                    all_ranges.append({'high': high, 'low': low})

                gg = max(r['high'] for r in all_ranges)
                dd = min(r['low'] for r in all_ranges)

                max_amplitude = max(r['high'] - r['low'] for r in all_ranges)
                if max_amplitude > pivot_width * final_filter_ratio:
                    i += 1
                    continue

                first_stroke = self.strokes[pivot_stroke_indices[0]]
                last_stroke = self.strokes[pivot_stroke_indices[-1]]
                stroke_count = len(pivot_stroke_indices)
                is_daily_level = pivot_amplitude >= daily_min_amp and stroke_count >= daily_min_strokes

                pivot_info = {
                    'start_idx': first_stroke['start_idx'],
                    'end_idx': last_stroke['end_idx'],
                    'high': pivot_zg,
                    'low': pivot_zd,
                    'gg': gg,
                    'dd': dd,
                    'start_date': first_stroke['start_date'],
                    'end_date': last_stroke['end_date'],
                    'stroke_count': stroke_count,
                    'stroke_indices': pivot_stroke_indices,
                    'amplitude': pivot_amplitude,
                    'daily_min_amplitude': daily_min_amp,
                    'daily_min_strokes': daily_min_strokes,
                    'is_daily_level': is_daily_level,
                    'level': '日线级别' if is_daily_level else '次级别',
                    'termination_reason': termination_reason
                }
                all_pivots.append(pivot_info)

            if len(pivot_stroke_indices) >= 3:
                i = i + max(1, len(pivot_stroke_indices) - 2)
            else:
                i += 1

        all_pivots = self._dedupe_pivots(all_pivots)
        self.raw_pivots = [dict(p) for p in all_pivots]
        validated_pivots = self._validate_pivot_amplitudes([dict(p) for p in all_pivots])
        validated_pivots = self._validate_pivots(validated_pivots)

        self.pivots = [p for p in validated_pivots if p.get('is_daily_level')]
        self.sub_level_pivots = [p for p in validated_pivots if not p.get('is_daily_level')]
        return self.pivots


    @staticmethod
    def _demote_pivot(pivot: Dict, reason: str) -> Dict:
        """将不稳定的日线候选中枢下调为次级别，避免直接丢失结构信息。"""
        pivot['is_daily_level'] = False
        pivot['level'] = '次级别'
        pivot['demote_reason'] = reason
        return pivot

    @staticmethod
    def _dedupe_pivots(pivots: List[Dict]) -> List[Dict]:
        """滚动识别后按笔序列去重，避免重叠识别造成重复中枢。"""
        deduped = []
        seen = set()
        for pivot in pivots:
            key = tuple(pivot['stroke_indices'])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(pivot)
        return deduped

    def _scan_post_pivot_structure(self, pivot: Dict) -> Tuple[Optional[str], Optional[int], Optional[int]]:
        """搜索中枢后的首个离开笔和首个回抽/反抽笔。"""
        last_stroke_idx = pivot['stroke_indices'][-1]
        for idx in range(last_stroke_idx + 1, len(self.strokes)):
            stroke = self.strokes[idx]
            if stroke['direction'] == 'up' and stroke['end_price'] > pivot['high']:
                breakout_direction = 'up'
                breakout_idx = idx
                break
            if stroke['direction'] == 'down' and stroke['end_price'] < pivot['low']:
                breakout_direction = 'down'
                breakout_idx = idx
                break
        else:
            return None, None, None

        for idx in range(breakout_idx + 1, len(self.strokes)):
            stroke = self.strokes[idx]
            if breakout_direction == 'up' and stroke['direction'] == 'down':
                return breakout_direction, breakout_idx, idx
            if breakout_direction == 'down' and stroke['direction'] == 'up':
                return breakout_direction, breakout_idx, idx
        return breakout_direction, breakout_idx, None

    def _first_breakout_after_pivot(self, pivot: Dict) -> Optional[Tuple[str, int]]:
        """寻找中枢结束后的首个有效离开方向。"""
        breakout_direction, breakout_idx, _ = self._scan_post_pivot_structure(pivot)
        if breakout_direction is None or breakout_idx is None:
            return None
        return breakout_direction, breakout_idx

    def _get_pre_pivot_indices(self, pivot_idx: int, first_stroke_idx: int) -> range:
        """优先在前一中枢结束到当前中枢开始之间找一买/一卖。"""
        if pivot_idx > 0:
            prev_pivot_end = self.pivots[pivot_idx - 1]['stroke_indices'][-1]
            start_idx = prev_pivot_end + 1
        else:
            start_idx = max(0, first_stroke_idx - 6)
        if first_stroke_idx - start_idx < 2:
            start_idx = max(0, first_stroke_idx - 6)
        return range(start_idx, first_stroke_idx)

    def _get_pivot_edge_tolerance(self, pivot: Dict) -> float:
        """中枢边界容忍度按中枢宽度自适应，避免固定2%对大小票失真。"""
        return float(self._clip(pivot['amplitude'] * 0.35, 0.008, 0.02))

    def _next_trade_date(self, date_value: pd.Timestamp) -> Optional[pd.Timestamp]:
        """返回信号确认后的下一个可交易日。"""
        ts = pd.Timestamp(date_value)
        idx = self.trade_dates.searchsorted(ts, side='right')
        if idx >= len(self.trade_dates):
            return None
        return pd.Timestamp(self.trade_dates[idx])

    def _trade_bar_gap(self, start_date: pd.Timestamp, end_date: pd.Timestamp) -> int:
        start_ts = pd.Timestamp(start_date)
        end_ts = pd.Timestamp(end_date)
        start_idx = int(self.trade_dates.searchsorted(start_ts, side='left'))
        end_idx = int(self.trade_dates.searchsorted(end_ts, side='left'))
        return max(0, end_idx - start_idx)

    def _annotate_trade_timing(self, point: Dict) -> Dict:
        """给买卖点补充真实可执行时间。"""
        signal_date = pd.Timestamp(point['date'])
        pivot_idx = point.get('pivot_idx')
        pivot_confirm_date = signal_date
        if isinstance(pivot_idx, int) and 0 <= pivot_idx < len(self.pivots):
            pivot_confirm_date = pd.Timestamp(self.pivots[pivot_idx]['end_date'])

        actionable_date = max(signal_date, pivot_confirm_date)
        trade_date = self._next_trade_date(actionable_date)

        point['signal_date'] = signal_date
        point['actionable_date'] = actionable_date
        point['trade_date'] = trade_date
        point['signal_lag_days'] = int((actionable_date - signal_date).days)
        point['signal_lag_bars'] = self._trade_bar_gap(signal_date, actionable_date)
        point['is_realtime_signal'] = bool(actionable_date == signal_date)
        point['is_tradeable'] = trade_date is not None
        return point

    def _normalize_fractals(self, fractals: List[Dict]) -> List[Dict]:
        """连续同类分型只保留更极端者，避免后续画笔被噪声分型干扰。"""
        normalized = []
        for fractal in fractals:
            if not normalized:
                normalized.append(fractal)
                continue
            last = normalized[-1]
            if fractal['type'] != last['type']:
                normalized.append(fractal)
                continue
            if fractal['type'] == 'top' and fractal['price'] >= last['price']:
                normalized[-1] = fractal
            elif fractal['type'] == 'bottom' and fractal['price'] <= last['price']:
                normalized[-1] = fractal
        return normalized

    def _find_b1_candidate(self, stroke_indices: range, pivot_zd: float) -> Optional[int]:
        """在中枢前的下跌笔中寻找创新低但力度衰减的一买候选。"""
        down_indices = [idx for idx in stroke_indices if self.strokes[idx]['direction'] == 'down']
        if not down_indices:
            return None

        candidate_idx = None
        prev_down_idx = None
        for idx in down_indices:
            stroke = self.strokes[idx]
            if stroke['end_price'] >= pivot_zd:
                prev_down_idx = idx
                continue
            if prev_down_idx is not None:
                prev_down = self.strokes[prev_down_idx]
                makes_new_low = stroke['end_price'] < prev_down['end_price']
                weaker = (
                    self._stroke_strength(stroke) <= self._stroke_strength(prev_down) * 0.95
                    or stroke['amplitude'] <= prev_down['amplitude'] * 0.95
                )
                if makes_new_low and weaker:
                    candidate_idx = idx if candidate_idx is None else min(
                        candidate_idx,
                        idx,
                        key=lambda x: self.strokes[x]['end_price'],
                    )
            prev_down_idx = idx

        if candidate_idx is not None:
            return candidate_idx
        fallback_indices = [idx for idx in down_indices if self.strokes[idx]['end_price'] < pivot_zd]
        if not fallback_indices:
            return None
        return min(
            fallback_indices,
            key=lambda idx: (self.strokes[idx]['end_price'], -self.strokes[idx]['amplitude']),
        )

    def _find_s1_candidate(self, stroke_indices: range, pivot_zg: float) -> Optional[int]:
        """在中枢前的上涨笔中寻找创新高但力度衰减的一卖候选。"""
        up_indices = [idx for idx in stroke_indices if self.strokes[idx]['direction'] == 'up']
        if not up_indices:
            return None

        candidate_idx = None
        prev_up_idx = None
        for idx in up_indices:
            stroke = self.strokes[idx]
            if stroke['end_price'] <= pivot_zg:
                prev_up_idx = idx
                continue
            if prev_up_idx is not None:
                prev_up = self.strokes[prev_up_idx]
                makes_new_high = stroke['end_price'] > prev_up['end_price']
                weaker = (
                    self._stroke_strength(stroke) <= self._stroke_strength(prev_up) * 0.95
                    or stroke['amplitude'] <= prev_up['amplitude'] * 0.95
                )
                if makes_new_high and weaker:
                    candidate_idx = idx if candidate_idx is None else max(
                        candidate_idx,
                        idx,
                        key=lambda x: self.strokes[x]['end_price'],
                    )
            prev_up_idx = idx

        if candidate_idx is not None:
            return candidate_idx
        fallback_indices = [idx for idx in up_indices if self.strokes[idx]['end_price'] > pivot_zg]
        if not fallback_indices:
            return None
        return max(
            fallback_indices,
            key=lambda idx: (self.strokes[idx]['end_price'], self.strokes[idx]['amplitude']),
        )


    
    def _split_trend_segments(self) -> List[Dict]:
        """
        BUG1修复：拆分走势段
        
        规则：
        - 上涨走势段：连续的笔满足「低点不断抬升、高点不断抬升」
        - 下跌走势段：连续的笔满足「高点不断降低、低点不断降低」
        - 盘整走势段：无明确高低点抬升/降低
        """
        if len(self.strokes) < 3:
            return []
        
        segments = []
        i = 0
        
        while i < len(self.strokes):
            # 尝试识别一个走势段
            if i + 2 >= len(self.strokes):
                # 剩余笔数不足3笔，作为一个独立段
                if i < len(self.strokes):
                    segments.append({
                        'direction': 'mixed',
                        'stroke_indices': list(range(i, len(self.strokes))),
                        'start_idx': i,
                        'end_idx': len(self.strokes) - 1
                    })
                break
            
            # 从第i笔开始，判断走势方向
            segment_start = i
            segment_direction = None
            
            # 检查前3笔的高低点关系
            stroke1 = self.strokes[i]
            stroke2 = self.strokes[i + 1]
            stroke3 = self.strokes[i + 2]
            
            # 提取高低点
            high1 = max(stroke1['start_price'], stroke1['end_price'])
            low1 = min(stroke1['start_price'], stroke1['end_price'])
            high2 = max(stroke2['start_price'], stroke2['end_price'])
            low2 = min(stroke2['start_price'], stroke2['end_price'])
            high3 = max(stroke3['start_price'], stroke3['end_price'])
            low3 = min(stroke3['start_price'], stroke3['end_price'])
            
            # 判断走势方向
            if low2 > low1 and low3 > low2 and high2 > high1 and high3 > high2:
                # 上涨走势段：低点抬升、高点抬升
                segment_direction = 'up'
            elif high2 < high1 and high3 < high2 and low2 < low1 and low3 < low2:
                # 下跌走势段：高点降低、低点降低
                segment_direction = 'down'
            else:
                # 盘整走势段
                segment_direction = 'mixed'
            
            # 扩展走势段
            j = i + 3
            segment_strokes = [i, i + 1, i + 2]
            
            if segment_direction == 'up':
                # 上涨走势段：继续扩展，直到出现一笔下跌跌破前一个向上笔的低点
                prev_low = low3
                while j < len(self.strokes):
                    curr_stroke = self.strokes[j]
                    curr_high = max(curr_stroke['start_price'], curr_stroke['end_price'])
                    curr_low = min(curr_stroke['start_price'], curr_stroke['end_price'])
                    
                    # 检查是否跌破前一个低点
                    if curr_low < prev_low:
                        # 上涨段终结
                        break
                    
                    segment_strokes.append(j)
                    prev_low = min(prev_low, curr_low)
                    j += 1
            
            elif segment_direction == 'down':
                # 下跌走势段：继续扩展，直到出现一笔上涨突破前一个向下笔的高点
                prev_high = high3
                while j < len(self.strokes):
                    curr_stroke = self.strokes[j]
                    curr_high = max(curr_stroke['start_price'], curr_stroke['end_price'])
                    curr_low = min(curr_stroke['start_price'], curr_stroke['end_price'])
                    
                    # 检查是否突破前一个高点
                    if curr_high > prev_high:
                        # 下跌段终结
                        break
                    
                    segment_strokes.append(j)
                    prev_high = max(prev_high, curr_high)
                    j += 1
            
            else:
                # 盘整走势段：扩展到下一个明确的上涨或下跌段开始
                while j < len(self.strokes) - 2:
                    # 检查接下来的3笔是否形成明确的上涨或下跌
                    s1 = self.strokes[j]
                    s2 = self.strokes[j + 1]
                    s3 = self.strokes[j + 2]
                    
                    h1 = max(s1['start_price'], s1['end_price'])
                    l1 = min(s1['start_price'], s1['end_price'])
                    h2 = max(s2['start_price'], s2['end_price'])
                    l2 = min(s2['start_price'], s2['end_price'])
                    h3 = max(s3['start_price'], s3['end_price'])
                    l3 = min(s3['start_price'], s3['end_price'])
                    
                    if (l2 > l1 and l3 > l2 and h2 > h1 and h3 > h2) or \
                       (h2 < h1 and h3 < h2 and l2 < l1 and l3 < l2):
                        # 发现明确的上涨或下跌段，盘整段终结
                        break
                    
                    segment_strokes.append(j)
                    j += 1
                
                # 如果到达末尾，把剩余的笔都加入
                if j == len(self.strokes) - 2:
                    segment_strokes.extend([j, j + 1])
                    j = len(self.strokes)
            
            # 形成一个走势段
            segments.append({
                'direction': segment_direction,
                'stroke_indices': segment_strokes,
                'start_idx': segment_start,
                'end_idx': segment_strokes[-1]
            })
            
            i = j if j > segment_start else segment_start + 1
        
        return segments
    


    
    def _validate_pivot_amplitudes(self, pivots: List[Dict]) -> List[Dict]:
        """
        同级别中枢幅度校验
        
        同一个走势段内的同级别中枢，波动幅度偏差不超过50%
        """
        if len(pivots) <= 1:
            return pivots

        daily_pivots = [p for p in pivots if p.get('is_daily_level')]
        if len(daily_pivots) <= 1:
            return pivots

        baseline = float(np.median([p['amplitude'] for p in daily_pivots]))
        if baseline <= 0:
            return pivots

        validated_pivots = []
        for pivot in pivots:
            if not pivot.get('is_daily_level'):
                validated_pivots.append(pivot)
                continue

            deviation = abs(pivot['amplitude'] - baseline) / baseline
            # 笔数很充足的中枢给予更高容忍度，避免趋势中后段被误降级。
            tolerance = self.pivot_amplitude_tolerance
            if pivot['stroke_count'] >= max(5, pivot.get('daily_min_strokes', 4) + 1):
                tolerance += 0.20

            if deviation > tolerance:
                validated_pivots.append(self._demote_pivot(pivot, 'amplitude_deviation'))
            else:
                validated_pivots.append(pivot)

        return validated_pivots
    
    def _validate_pivots(self, pivots: List[Dict]) -> List[Dict]:
        """
        BUG4修复：中枢有效性校验
        
        检查每个中枢是否有明确的离开段和回踩段
        """
        valid_pivots = []
        
        for pivot in pivots:
            if not pivot.get('is_daily_level'):
                pivot.setdefault('validity', 'sub_level')
                valid_pivots.append(pivot)
                continue

            breakout_direction, breakout_idx, pullback_idx = self._scan_post_pivot_structure(pivot)
            pivot['has_breakout'] = breakout_direction is not None
            pivot['breakout_direction'] = breakout_direction
            pivot['breakout_stroke_idx'] = breakout_idx
            pivot['has_pullback'] = pullback_idx is not None
            pivot['pullback_stroke_idx'] = pullback_idx
            
            # 标记中枢类型
            if pivot.get('has_breakout') and pivot.get('has_pullback'):
                pivot['validity'] = 'valid'  # 有效中枢
            elif pivot.get('has_breakout'):
                pivot['validity'] = 'partial'  # 部分有效（有离开段，无回踩段）
            else:
                pivot['validity'] = 'invalid'  # 无效震荡
            
            if pivot['validity'] == 'invalid':
                valid_pivots.append(self._demote_pivot(pivot, 'no_breakout'))
            else:
                valid_pivots.append(pivot)
        
        return valid_pivots
    
    def classify_trend(self) -> Dict:
        """
        任务2：走势类型自动判断
        
        Returns:
            走势类型信息
        """
        if not self.pivots:
            self.identify_pivots()
        
        if len(self.pivots) == 0:
            self.trend_type = {
                'type': '无中枢',
                'description': '未识别到有效中枢'
            }
            return self.trend_type
        
        if len(self.pivots) == 1:
            pivot = self.pivots[0]
            breakout = self._first_breakout_after_pivot(pivot)
            if breakout and breakout[0] == 'up':
                self.trend_type = {
                    'type': '上涨趋势',
                    'description': '单中枢后向上离开',
                    'pivots': [0]
                }
            elif breakout and breakout[0] == 'down':
                self.trend_type = {
                    'type': '下跌趋势',
                    'description': '单中枢后向下离开',
                    'pivots': [0]
                }
            else:
                self.trend_type = {
                    'type': '盘整走势',
                    'description': '仅识别到1个有效中枢，且未出现明确离开',
                    'pivots': [0]
                }
            return self.trend_type
        
        # 检查多个中枢的关系
        trend_segments = []
        i = 0
        
        while i < len(self.pivots):
            pivot = self.pivots[i]
            
            # 检查是否有下一个中枢
            if i + 1 < len(self.pivots):
                next_pivot = self.pivots[i + 1]
                
                # 检查价格区间是否重叠
                has_overlap = (pivot['low'] < next_pivot['high'] and 
                              next_pivot['low'] < pivot['high'])
                
                if has_overlap:
                    # 中枢延伸，属于盘整
                    trend_segments.append({
                        'type': '盘整走势',
                        'pivots': [i, i + 1],
                        'description': f"中枢{i+1}和中枢{i+2}价格区间重叠"
                    })
                    i += 2
                else:
                    # 无重叠，判断趋势方向
                    if next_pivot['low'] > pivot['high']:
                        # 上涨趋势
                        trend_segments.append({
                            'type': '上涨趋势',
                            'pivots': [i, i + 1],
                            'description': f"中枢{i+2}的ZD({next_pivot['low']:.2f}) > 中枢{i+1}的ZG({pivot['high']:.2f})"
                        })
                    elif next_pivot['high'] < pivot['low']:
                        # 下跌趋势
                        trend_segments.append({
                            'type': '下跌趋势',
                            'pivots': [i, i + 1],
                            'description': f"中枢{i+2}的ZG({next_pivot['high']:.2f}) < 中枢{i+1}的ZD({pivot['low']:.2f})"
                        })
                    else:
                        # 边界情况
                        trend_segments.append({
                            'type': '盘整走势',
                            'pivots': [i, i + 1],
                            'description': f"中枢{i+1}和中枢{i+2}价格区间部分重叠"
                        })
                    i += 2
            else:
                # 最后一个中枢
                i += 1
        
        self.trend_type = {
            'segments': trend_segments,
            'summary': self._summarize_trend(trend_segments)
        }
        
        return self.trend_type
    def identify_buy_sell_points(self) -> List[Dict]:
        """
        识别三类买卖点（简化标注版本）

        缠论买卖点定义：
        - B1（一买）：下跌趋势背驰后的最低点（趋势结束）
        - B2（二买）：一买后第一次回调不破一买低点（确认趋势反转）
        - B2*（类二买）：中枢内部接近ZD的次级别买点
        - B3（三买）：离开中枢后回抽不跌破ZG（趋势延续）
        - S1（一卖）：上涨趋势背驰后的最高点
        - S2（二卖）：一卖后第一次反弹不破一卖高点
        - S3（三卖）：离开中枢后反抽不突破ZD

        Returns:
            买卖点列表
        """
        if not self.pivots or not self.strokes:
            self.buy_sell_points = []
            return []

        buy_sell_points = []

        # 遍历每个中枢，识别相关的买卖点
        for pivot_idx, pivot in enumerate(self.pivots):
            pivot_zd = pivot['low']
            pivot_zg = pivot['high']

            # 找到中枢对应的笔索引范围
            pivot_stroke_indices = pivot['stroke_indices']
            first_stroke_idx = pivot_stroke_indices[0]
            last_stroke_idx = pivot_stroke_indices[-1]
            pre_pivot_indices = self._get_pre_pivot_indices(pivot_idx, first_stroke_idx)
            edge_tolerance = self._get_pivot_edge_tolerance(pivot)

            # ========================================
            # B1（一买）：中枢形成前的下跌背驰低点
            # ========================================
            if first_stroke_idx >= 2:
                stroke_idx = self._find_b1_candidate(pre_pivot_indices, pivot_zd)
                if stroke_idx is not None:
                    b1_stroke = self.strokes[stroke_idx]
                    # 结构约束：一买应位于中枢下方
                    b1_valid = b1_stroke['end_price'] < pivot_zd

                    # 检查是否已经标注过
                    if b1_valid and not any(p['stroke_idx'] == stroke_idx for p in buy_sell_points):
                        buy_sell_points.append({
                            'type': 'B1',
                            'full_name': '一买',
                            'point_type': 'buy',
                            'stroke_idx': stroke_idx,
                            'price': b1_stroke['end_price'],
                            'date': b1_stroke['end_date'],
                            'description': f'中枢{pivot_idx+1}形成前的下跌背驰低点',
                            'pivot_idx': pivot_idx,
                            'stop_loss': b1_stroke['end_price'] * 0.95,
                            'take_profit': pivot_zd,
                            'priority': 1
                        })

            # ========================================
            # B2（二买）：一买后第一次回调不破一买低点
            # ========================================
            if len(pivot_stroke_indices) >= 3:
                for stroke_idx in pivot_stroke_indices[1:]:
                    stroke = self.strokes[stroke_idx]

                    if stroke['direction'] == 'down':
                        # 检查是否有对应的一买
                        one_buy = next((p for p in buy_sell_points 
                                       if p['type'] == 'B1' and p['pivot_idx'] == pivot_idx), None)

                        if one_buy and stroke['end_price'] > one_buy['price']:
                            # 回调低点高于一买，形成二买
                            if not any(p['stroke_idx'] == stroke_idx for p in buy_sell_points):
                                buy_sell_points.append({
                                    'type': 'B2',
                                    'full_name': '二买',
                                    'point_type': 'buy',
                                    'stroke_idx': stroke_idx,
                                    'price': stroke['end_price'],
                                    'date': stroke['end_date'],
                                    'description': f'回调低点{stroke["end_price"]:.2f} > 一买{one_buy["price"]:.2f}',
                                    'pivot_idx': pivot_idx,
                                    'stop_loss': one_buy['price'],
                                    'take_profit': pivot_zg,
                                    'priority': 2
                                })
                                break

            # ========================================
            # B2*（类二买）：中枢内部接近ZD的次级别买点
            # ========================================
            # 遍历中枢内所有笔，找到接近ZD的向下笔
            for stroke_idx in pivot_stroke_indices:
                stroke = self.strokes[stroke_idx]

                if stroke['direction'] == 'down':
                    distance_to_zd = abs(stroke['end_price'] - pivot_zd) / pivot_zd

                    if distance_to_zd <= edge_tolerance and stroke['end_price'] >= pivot_zd * (1 - edge_tolerance):
                        # 接近ZD，形成类二买
                        # 如果已经标注为B2，则替换为B2*（更精确）
                        existing = next((p for p in buy_sell_points if p['stroke_idx'] == stroke_idx), None)
                        if existing and existing['type'] == 'B2':
                            buy_sell_points.remove(existing)
                        
                        if not any(p['stroke_idx'] == stroke_idx for p in buy_sell_points):
                            buy_sell_points.append({
                                'type': 'B2*',
                                'full_name': '类二买',
                                'point_type': 'buy',
                                'is_sub_level': True,  # 标记为次级别类买点
                                'stroke_idx': stroke_idx,
                                'price': stroke['end_price'],
                                'date': stroke['end_date'],
                                'description': f'中枢内部接近ZD={pivot_zd:.2f}',
                                'pivot_idx': pivot_idx,
                                'stop_loss': pivot_zd * 0.97,
                                'take_profit': (pivot_zd + pivot_zg) / 2,
                                'priority': 4
                            })

            # ========================================
            # B3（三买）：离开中枢后回抽不跌破ZG
            # ========================================
            if last_stroke_idx + 2 < len(self.strokes):
                next_pivot_start = (
                    self.pivots[pivot_idx + 1]['stroke_indices'][0]
                    if pivot_idx + 1 < len(self.pivots)
                    else len(self.strokes)
                )
                search_end = min(next_pivot_start, len(self.strokes))

                # 1) 在中枢后寻找首个向上有效离开笔
                breakout_idx = None
                for idx in range(last_stroke_idx + 1, search_end):
                    s = self.strokes[idx]
                    if s['direction'] == 'up' and s['end_price'] > pivot_zg:
                        breakout_idx = idx
                        break
                    # 若先出现向下有效离开，当前中枢不再考虑三买
                    if s['direction'] == 'down' and s['end_price'] < pivot_zd:
                        break

                # 2) 找离开后的首个向下回抽笔，且回抽不破ZG
                if breakout_idx is not None:
                    for idx in range(breakout_idx + 1, search_end):
                        pullback_stroke = self.strokes[idx]
                        if pullback_stroke['direction'] != 'down':
                            continue
                        if pullback_stroke['end_price'] > pivot_zg:
                            stroke_idx = idx
                            pullback_price = pullback_stroke['end_price']
                            if not any(p['stroke_idx'] == stroke_idx for p in buy_sell_points):
                                buy_sell_points.append({
                                    'type': 'B3',
                                    'full_name': '三买',
                                    'point_type': 'buy',
                                    'stroke_idx': stroke_idx,
                                    'price': pullback_price,
                                    'date': pullback_stroke['end_date'],
                                    'description': f'离开笔{breakout_idx+1}后回抽低点{pullback_price:.2f} > ZG={pivot_zg:.2f}',
                                    'pivot_idx': pivot_idx,
                                    'stop_loss': pivot_zg,
                                    'take_profit': pullback_price * 1.15,
                                    'priority': 3
                                })
                        break

            # ========================================
            # S1（一卖）：中枢形成前的上涨背驰高点
            # ========================================
            if first_stroke_idx >= 2:
                stroke_idx = self._find_s1_candidate(pre_pivot_indices, pivot_zg)
                if stroke_idx is not None:
                    s1_stroke = self.strokes[stroke_idx]
                    # 结构约束：一卖应位于中枢上方
                    s1_valid = s1_stroke['end_price'] > pivot_zg

                    if s1_valid and not any(p['stroke_idx'] == stroke_idx for p in buy_sell_points):
                        buy_sell_points.append({
                            'type': 'S1',
                            'full_name': '一卖',
                            'point_type': 'sell',
                            'stroke_idx': stroke_idx,
                            'price': s1_stroke['end_price'],
                            'date': s1_stroke['end_date'],
                            'description': f'中枢{pivot_idx+1}形成前的上涨背驰高点',
                            'pivot_idx': pivot_idx,
                            'stop_loss': s1_stroke['end_price'] * 1.05,
                            'take_profit': pivot_zd,
                            'priority': 1
                        })

            # ========================================
            # S2（二卖）：一卖后第一次反弹不破一卖高点
            # ========================================
            if len(pivot_stroke_indices) >= 3:
                for stroke_idx in pivot_stroke_indices[1:]:
                    stroke = self.strokes[stroke_idx]

                    if stroke['direction'] == 'up':
                        one_sell = next((p for p in buy_sell_points 
                                        if p['type'] == 'S1' and p['pivot_idx'] == pivot_idx), None)

                        if one_sell and stroke['end_price'] < one_sell['price']:
                            if not any(p['stroke_idx'] == stroke_idx for p in buy_sell_points):
                                buy_sell_points.append({
                                    'type': 'S2',
                                    'full_name': '二卖',
                                    'point_type': 'sell',
                                    'stroke_idx': stroke_idx,
                                    'price': stroke['end_price'],
                                    'date': stroke['end_date'],
                                    'description': f'反弹高点{stroke["end_price"]:.2f} < 一卖{one_sell["price"]:.2f}',
                                    'pivot_idx': pivot_idx,
                                    'stop_loss': one_sell['price'],
                                    'take_profit': pivot_zd,
                                    'priority': 2
                                })
                                break

            # ========================================
            # S2*（类二卖）：中枢内部接近ZG的次级别卖点
            # ========================================
            # 遍历中枢内所有笔，找到接近ZG的向上笔
            for stroke_idx in pivot_stroke_indices:
                stroke = self.strokes[stroke_idx]

                if stroke['direction'] == 'up':
                    distance_to_zg = abs(stroke['end_price'] - pivot_zg) / pivot_zg

                    if distance_to_zg <= edge_tolerance and stroke['end_price'] <= pivot_zg * (1 + edge_tolerance):
                        # 接近ZG，形成类二卖
                        # 如果已经标注为S2，则替换为S2*（更精确）
                        existing = next((p for p in buy_sell_points if p['stroke_idx'] == stroke_idx), None)
                        if existing and existing['type'] == 'S2':
                            buy_sell_points.remove(existing)
                        
                        if not any(p['stroke_idx'] == stroke_idx for p in buy_sell_points):
                            buy_sell_points.append({
                                'type': 'S2*',
                                'full_name': '类二卖',
                                'point_type': 'sell',
                                'is_sub_level': True,  # 标记为次级别类卖点
                                'stroke_idx': stroke_idx,
                                'price': stroke['end_price'],
                                'date': stroke['end_date'],
                                'description': f'中枢内部接近ZG={pivot_zg:.2f}',
                                'pivot_idx': pivot_idx,
                                'stop_loss': pivot_zg * 1.03,
                                'take_profit': (pivot_zd + pivot_zg) / 2,
                                'priority': 4
                            })

            # ========================================
            # S3（三卖）：离开中枢后反抽不突破ZD
            # ========================================
            if last_stroke_idx + 2 < len(self.strokes):
                next_pivot_start = (
                    self.pivots[pivot_idx + 1]['stroke_indices'][0]
                    if pivot_idx + 1 < len(self.pivots)
                    else len(self.strokes)
                )
                search_end = min(next_pivot_start, len(self.strokes))

                # 1) 在中枢后寻找首个向下有效离开笔
                breakout_idx = None
                for idx in range(last_stroke_idx + 1, search_end):
                    s = self.strokes[idx]
                    if s['direction'] == 'down' and s['end_price'] < pivot_zd:
                        breakout_idx = idx
                        break
                    # 若先出现向上有效离开，当前中枢不再考虑三卖
                    if s['direction'] == 'up' and s['end_price'] > pivot_zg:
                        break

                # 2) 找离开后的首个向上反抽笔，且反抽不破ZD
                if breakout_idx is not None:
                    for idx in range(breakout_idx + 1, search_end):
                        pullback_stroke = self.strokes[idx]
                        if pullback_stroke['direction'] != 'up':
                            continue
                        if pullback_stroke['end_price'] < pivot_zd:
                            stroke_idx = idx
                            pullback_price = pullback_stroke['end_price']
                            if not any(p['stroke_idx'] == stroke_idx for p in buy_sell_points):
                                buy_sell_points.append({
                                    'type': 'S3',
                                    'full_name': '三卖',
                                    'point_type': 'sell',
                                    'stroke_idx': stroke_idx,
                                    'price': pullback_price,
                                    'date': pullback_stroke['end_date'],
                                    'description': f'离开笔{breakout_idx+1}后反抽高点{pullback_price:.2f} < ZD={pivot_zd:.2f}',
                                    'pivot_idx': pivot_idx,
                                    'stop_loss': pivot_zd,
                                    'take_profit': pullback_price * 0.85,
                                    'priority': 3
                                })
                        break

        for point in buy_sell_points:
            self._annotate_trade_timing(point)

        # 按真实可交易时间排序
        buy_sell_points.sort(
            key=lambda p: (
                pd.Timestamp.max if p.get('trade_date') is None else pd.Timestamp(p['trade_date']),
                pd.Timestamp(p['actionable_date']),
                pd.Timestamp(p['signal_date']),
            )
        )

        self.buy_sell_points = buy_sell_points
        return buy_sell_points


    
    def _summarize_trend(self, segments: List[Dict]) -> str:
        """总结走势类型"""
        if not segments:
            return "无明确走势"
        
        # 统计各类型数量
        uptrend_count = sum(1 for s in segments if s['type'] == '上涨趋势')
        downtrend_count = sum(1 for s in segments if s['type'] == '下跌趋势')
        consolidation_count = sum(1 for s in segments if s['type'] == '盘整走势')
        
        if uptrend_count > 0 and downtrend_count == 0:
            return f"上涨趋势（{uptrend_count}个同方向中枢）"
        elif downtrend_count > 0 and uptrend_count == 0:
            return f"下跌趋势（{downtrend_count}个同方向中枢）"
        elif consolidation_count > 0 and uptrend_count == 0 and downtrend_count == 0:
            return "盘整走势（中枢延伸）"
        else:
            return f"复杂走势（上涨{uptrend_count}段 + 下跌{downtrend_count}段 + 盘整{consolidation_count}段）"
    
    def analyze(self) -> Dict:
        """执行完整分析"""
        self.merge_klines()
        self.identify_fractals()
        self.identify_strokes()
        self.identify_pivots()
        self.classify_trend()
        self.identify_buy_sell_points()
        
        return {
            'merged_klines': self.df_merged,
            'fractals': self.fractals,
            'strokes': self.strokes,
            'pivots': self.pivots,
            'trend_type': self.trend_type,
            'buy_sell_points': self.buy_sell_points
        }
    
    def get_summary(self) -> Dict:
        """获取分析摘要"""
        if not self.strokes:
            self.analyze()
        
        return {
            'total_strokes': len(self.strokes),
            'total_fractals': len(self.fractals),
            'total_raw_pivots': len(self.raw_pivots),
            'total_pivots': len(self.pivots),
            'total_sub_level_pivots': len(self.sub_level_pivots),
            'total_buy_sell_points': len(self.buy_sell_points),
            'trend_type': self.trend_type,
            'min_amplitude': self.min_amplitude,
            'effective_min_amplitude': self.effective_min_amplitude,
            'dynamic_threshold_meta': self.dynamic_threshold_meta,
            'pivot_min_amplitude': self.pivot_min_amplitude,
            'pivot_min_strokes': self.pivot_min_strokes,
            'effective_pivot_rules': self.effective_pivot_rules
        }


# 兼容旧调用方式：from chan_core_v5t import ChanCoreV5
ChanCoreV5 = ChanCoreV5T
