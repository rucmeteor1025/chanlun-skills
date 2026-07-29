#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""macd_divergence 单元测试：构造已知序列断言。"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from macd_divergence import MACDDivergence  # noqa: E402


def make_df(closes):
    n = len(closes)
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n, freq="30min"),
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1000] * n,
    })


def test_macd_against_reference():
    """与 pandas ewm 参考实现对比（adjust=False 等价于递推 EMA）。"""
    rng = np.random.default_rng(42)
    closes = 100 + np.cumsum(rng.normal(0, 1, 500))
    df = make_df(closes)
    m = MACDDivergence()
    out = m.compute(df)
    ema_f = pd.Series(closes).ewm(span=12, adjust=False).mean()
    ema_s = pd.Series(closes).ewm(span=26, adjust=False).mean()
    dif_ref = ema_f - ema_s
    dea_ref = dif_ref.ewm(span=9, adjust=False).mean()
    bar_ref = 2 * (dif_ref - dea_ref)
    assert np.allclose(out["macd_dif"].to_numpy(), dif_ref.to_numpy(), atol=1e-10), "DIF 与参考不一致"
    assert np.allclose(out["macd_dea"].to_numpy(), dea_ref.to_numpy(), atol=1e-10), "DEA 与参考不一致"
    assert np.allclose(out["macd_bar"].to_numpy(), bar_ref.to_numpy(), atol=1e-10), "MACD柱 与参考不一致"
    print("✅ MACD 计算与参考实现一致")


def test_area_only_same_color():
    """面积只累计同色柱：下跌笔区间内的红柱不计入。"""
    # 先下跌（绿柱区），中间小反弹（可能出现红柱），再下跌
    closes = list(np.linspace(100, 80, 60)) + list(np.linspace(80, 84, 15)) + list(np.linspace(84, 70, 60))
    df = make_df(closes)
    m = MACDDivergence()
    out = m.compute(df)
    bars = out["macd_bar"]
    area_down = m.stroke_macd_area(out, 0, len(df) - 1, "down")
    expect = bars[bars < 0].abs().sum()
    assert abs(area_down - expect) < 1e-9, f"面积应为 {expect}，得到 {area_down}"
    area_up = m.stroke_macd_area(out, 0, len(df) - 1, "up")
    expect_up = bars[bars > 0].abs().sum()
    assert abs(area_up - expect_up) < 1e-9
    print("✅ 面积只累计同色柱")


def test_divergence_detection():
    """构造趋势背驰：两段下跌，第二段更缓（面积更小）→ 应判背驰。"""
    seg1 = np.linspace(100, 70, 80)            # 急跌
    seg2 = np.linspace(70, 64, 80)             # 缓跌（力度衰减）
    closes = np.concatenate([seg1, seg2])
    df = make_df(closes)
    m = MACDDivergence()
    out = m.compute(df)
    is_div, ratio = m.is_divergence(out, (0, 79, "down"), (80, 159, "down"), rate=0.9)
    assert is_div, f"应判背驰，ratio={ratio}"
    assert ratio < 0.9
    # 反过来比较（后段力度大）应不背驰
    is_div2, ratio2 = m.is_divergence(out, (80, 159, "down"), (0, 79, "down"), rate=0.9)
    assert not is_div2, f"不应判背驰，ratio={ratio2}"
    print(f"✅ 背驰判定正确（衰减比 {ratio:.2f} / 反向 {ratio2:.2f}）")


def test_dif_below_zero():
    """长期下跌末端 DIF/DEA 应在 0 轴下；长期上涨末端在 0 轴上。"""
    df_down = make_df(list(np.linspace(100, 60, 300)))
    m = MACDDivergence()
    assert m.dif_below_zero(df_down, 299), "下跌末端应在0轴下"
    df_up = make_df(list(np.linspace(60, 100, 300)))
    assert not m.dif_below_zero(df_up, 299), "上涨末端不应在0轴下"
    print("✅ 0轴判定正确")


def test_index_bounds():
    """乱序/越界索引不应报错。"""
    df = make_df(list(np.linspace(100, 90, 50)))
    m = MACDDivergence()
    out = m.compute(df)
    a1 = m.stroke_macd_area(out, 40, 10, "down")   # 乱序
    a2 = m.stroke_macd_area(out, -100, 9999, "down")  # 越界
    assert a1 >= 0 and a2 >= 0
    print("✅ 索引容错正确")


if __name__ == "__main__":
    test_macd_against_reference()
    test_area_only_same_color()
    test_divergence_detection()
    test_dif_below_zero()
    test_index_bounds()
    print("\n全部单测通过 ✅")
