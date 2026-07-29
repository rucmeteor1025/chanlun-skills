# -*- coding: utf-8 -*-
"""
MACD 背驰计算模块（P0 前置）

供三层漏斗策略的一买/三买判定调用。
对齐 chan.py 的实现口径（Bi/Bi.py L225-289）：
- MACD 参数 12/26/9，柱 = 2*(DIF-DEA)（国内软件惯例，与同花顺/通达信一致）
- 面积：笔区间内只累计与笔同色的柱的 |值|（FULL_AREA）
- 峰值：笔区间内同色柱 |值| 的最大值
- 背驰：离开段力度 < 进入段力度 × rate

索引约定：stroke_start_idx / stroke_end_idx 为**原始K线DataFrame的行号**（含两端）。
v5t 引擎的 stroke 字典里的 start_idx/end_idx 是合并后K线行号，调用方需自行映射
（漏斗策略直接用原始K线行号传参即可）。
"""
from typing import Tuple

import numpy as np
import pandas as pd


class MACDDivergence:
    """MACD 背驰计算"""

    def __init__(self, fast: int = 12, slow: int = 26, signal: int = 9):
        self.fast = fast
        self.slow = slow
        self.signal = signal

    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算 DIF/DEA/MACD柱，返回带 macd_dif/macd_dea/macd_bar 列的 df 副本。

        与 chan.py Math/MACD.py 一致：标准 EMA 递推（首根 EMA=收盘价），
        macd_bar = 2 * (DIF - DEA)。
        """
        out = df.copy()
        close = pd.to_numeric(out["close"], errors="coerce").to_numpy(dtype=float)
        n = len(close)
        dif = np.full(n, np.nan)
        dea = np.full(n, np.nan)
        if n > 0:
            alpha_f = 2.0 / (self.fast + 1)
            alpha_s = 2.0 / (self.slow + 1)
            alpha_g = 2.0 / (self.signal + 1)
            ema_f = close[0]
            ema_s = close[0]
            dea_prev = 0.0
            for i in range(n):
                v = close[i]
                if np.isnan(v):
                    dif[i] = np.nan
                    dea[i] = np.nan
                    continue
                if i > 0:
                    ema_f = alpha_f * v + (1 - alpha_f) * ema_f
                    ema_s = alpha_s * v + (1 - alpha_s) * ema_s
                d = ema_f - ema_s
                dea_prev = alpha_g * d + (1 - alpha_g) * dea_prev if i > 0 else 0.0
                dif[i] = d
                dea[i] = dea_prev
        out["macd_dif"] = dif
        out["macd_dea"] = dea
        out["macd_bar"] = 2.0 * (out["macd_dif"] - out["macd_dea"])
        return out

    @staticmethod
    def _slice(df: pd.DataFrame, start_idx: int, end_idx: int) -> pd.Series:
        lo, hi = sorted((int(start_idx), int(end_idx)))
        lo = max(0, lo)
        hi = min(len(df) - 1, hi)
        return df["macd_bar"].iloc[lo:hi + 1]

    def stroke_macd_area(self, df: pd.DataFrame, stroke_start_idx: int, stroke_end_idx: int,
                         direction: str) -> float:
        """一笔内同色 MACD 柱的累计面积（|值|求和）。

        direction='down' → 只累计绿柱（macd_bar<0）
        direction='up'   → 只累计红柱（macd_bar>0）
        """
        if "macd_bar" not in df.columns:
            df = self.compute(df)
        bars = self._slice(df, stroke_start_idx, stroke_end_idx).dropna()
        if direction == "down":
            sel = bars[bars < 0]
        elif direction == "up":
            sel = bars[bars > 0]
        else:
            raise ValueError(f"direction 必须是 up/down: {direction}")
        return float(sel.abs().sum())

    def stroke_macd_peak(self, df: pd.DataFrame, stroke_start_idx: int, stroke_end_idx: int,
                         direction: str) -> float:
        """一笔内同色 MACD 柱 |值| 的最大值。"""
        if "macd_bar" not in df.columns:
            df = self.compute(df)
        bars = self._slice(df, stroke_start_idx, stroke_end_idx).dropna()
        sel = bars[bars < 0] if direction == "down" else bars[bars > 0]
        return float(sel.abs().max()) if len(sel) else 0.0

    def stroke_dif_peak(self, df: pd.DataFrame, stroke_start_idx: int, stroke_end_idx: int,
                        direction: str) -> float:
        """一笔内 DIF 的极值 |值|（向下笔取最深负值，向上笔取最高正值）。"""
        if "macd_dif" not in df.columns:
            df = self.compute(df)
        lo, hi = sorted((int(stroke_start_idx), int(stroke_end_idx)))
        s = df["macd_dif"].iloc[max(0, lo):hi + 1].dropna()
        if s.empty:
            return 0.0
        return float(abs(s.min()) if direction == "down" else abs(s.max()))

    def is_divergence(self, df: pd.DataFrame, in_stroke: Tuple[int, int, str],
                      out_stroke: Tuple[int, int, str], rate: float = 0.9,
                      use_peak: bool = False) -> Tuple[bool, float]:
        """判断背驰：out_stroke 力度 < in_stroke 力度 × rate。

        Args:
            in_stroke: (start_idx, end_idx, direction) 进入段
            out_stroke: (start_idx, end_idx, direction) 离开段
            rate: 衰减阈值（0.9 = 离开段面积须小于进入段的90%）
            use_peak: True 用峰值比较，False 用面积比较

        Returns:
            (是否背驰, 衰减比例 out/in)；in 力度为0时比例返回 inf 且判 False
        """
        metric = self.stroke_macd_peak if use_peak else self.stroke_macd_area
        in_force = metric(df, in_stroke[0], in_stroke[1], in_stroke[2])
        out_force = metric(df, out_stroke[0], out_stroke[1], out_stroke[2])
        if in_force <= 0:
            return False, float("inf")
        ratio = out_force / in_force
        return bool(ratio < rate), float(ratio)

    def dif_below_zero(self, df: pd.DataFrame, idx: int) -> bool:
        """指定K线处 DIF 与 DEA 均在 0 轴之下（一买的 MACD 定律）。"""
        if "macd_dif" not in df.columns:
            df = self.compute(df)
        i = min(max(0, int(idx)), len(df) - 1)
        return bool(df["macd_dif"].iloc[i] < 0 and df["macd_dea"].iloc[i] < 0)
