# -*- coding: utf-8 -*-
"""
Chan engine tests.

Strategy: deterministic synthetic OHLC series (seeded random walk with
embedded trend segments) so the suite runs anywhere without market data.
Assertions target Chan-theory structural invariants, not exact prices:
  - merged K-lines have no inclusion relations
  - fractals alternate top/bottom
  - strokes alternate direction and respect the minimum-amplitude filter
  - pivots carry valid ZG/ZD and enough strokes
  - buy/sell point labels come from the canonical set
  - the full analyze() pipeline returns a complete result dict
"""
import numpy as np
import pandas as pd
import pytest

from chan_core_v5 import ChanCoreV5


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _make_ohlc(seed: int = 42, n: int = 320, start: float = 50.0) -> pd.DataFrame:
    """Seeded random walk with a strong up-drift and cyclical swings."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0008, 0.012, n) + 0.0012  # slight upward drift
    closes = start * np.exp(np.cumsum(rets))
    highs = closes * (1 + np.abs(rng.normal(0, 0.006, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.006, n)))
    opens = np.concatenate([[closes[0]], closes[:-1]]) * (1 + rng.normal(0, 0.004, n))
    dates = pd.bdate_range("2024-01-01", periods=n)
    return pd.DataFrame({
        "date": dates,
        "open": np.round(opens, 2),
        "high": np.round(np.maximum(highs, np.maximum(opens, closes)), 2),
        "low": np.round(np.minimum(lows, np.minimum(opens, closes)), 2),
        "close": np.round(closes, 2),
        "volume": rng.integers(100_000, 5_000_000, n),
    })


@pytest.fixture(scope="module")
def engine() -> ChanCoreV5:
    eng = ChanCoreV5(_make_ohlc(), symbol="TEST.SH")
    eng.analyze()  # run full pipeline once; tests then inspect the state
    return eng


@pytest.fixture(scope="module")
def result(engine: ChanCoreV5) -> dict:
    return engine.analyze()


# ---------------------------------------------------------------------------
# input validation
# ---------------------------------------------------------------------------

def test_missing_columns_raise():
    df = pd.DataFrame({"date": pd.date_range("2024-01-01", periods=5), "close": range(5)})
    with pytest.raises(ValueError):
        ChanCoreV5(df)


def test_short_series_does_not_crash():
    df = _make_ohlc(seed=7, n=5)
    out = ChanCoreV5(df, symbol="T.SH").analyze()
    assert out["merged_klines"] is not None


# ---------------------------------------------------------------------------
# K-line merging (inclusion handling)
# ---------------------------------------------------------------------------

def test_merge_klines_removes_inclusion():
    # K3 is fully contained in K2; in an up move, merge keeps the higher range.
    df = pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=5),
        "open": [10.0, 10.5, 10.6, 11.0, 11.5],
        "high": [10.5, 11.0, 10.8, 11.4, 12.0],
        "low": [9.8, 9.6, 9.8, 10.6, 11.2],
        "close": [10.2, 10.7, 10.6, 11.2, 11.8],
        "volume": [1000] * 5,
    })
    merged = ChanCoreV5(df, symbol="T.SH").merge_klines()
    assert len(merged) < len(df)  # inclusion collapsed


def test_merged_series_free_of_inclusions(engine: ChanCoreV5):
    merged = engine.df_merged
    for i in range(1, len(merged)):
        prev, cur = merged.iloc[i - 1], merged.iloc[i]
        included = (cur["high"] <= prev["high"] and cur["low"] >= prev["low"]) or \
                   (prev["high"] <= cur["high"] and prev["low"] >= cur["low"])
        assert not included, f"inclusion remains at row {i}"


# ---------------------------------------------------------------------------
# fractals
# ---------------------------------------------------------------------------

def test_fractals_alternate(engine: ChanCoreV5):
    types = [f["type"] for f in engine.fractals]
    assert len(types) >= 4
    for a, b in zip(types, types[1:]):
        assert a != b, f"fractals do not alternate: {types}"
    assert set(types) <= {"top", "bottom"}


def test_fractal_fields(result: dict):
    for f in result["fractals"]:
        assert f["index"] >= 0
        assert f["price"] > 0
        assert f["type"] in ("top", "bottom")


# ---------------------------------------------------------------------------
# strokes (笔)
# ---------------------------------------------------------------------------

def test_strokes_alternate_direction(engine: ChanCoreV5):
    dirs = [s["direction"] for s in engine.strokes]
    assert len(dirs) >= 3
    for a, b in zip(dirs, dirs[1:]):
        assert a != b, f"strokes do not alternate: {dirs}"


def test_strokes_respect_min_amplitude(engine: ChanCoreV5):
    min_amp = engine.effective_min_amplitude
    for s in engine.strokes:
        assert s["amplitude"] >= min_amp * 0.999, (
            f"stroke {s['start_idx']}-{s['end_idx']} amp {s['amplitude']:.4f} "
            f"below effective min {min_amp:.4f}"
        )


def test_stroke_geometry(engine: ChanCoreV5):
    for s in engine.strokes:
        assert s["end_idx"] > s["start_idx"]
        assert s["kline_count"] >= 4  # Chan rule: >= 4 bars between fractal endpoints
        assert s["start_type"] != s["end_type"]


# ---------------------------------------------------------------------------
# pivots (中枢)
# ---------------------------------------------------------------------------

def test_pivot_structure(engine: ChanCoreV5):
    for p in engine.pivots:
        assert p["high"] > p["low"], f"invalid pivot range: {p}"  # ZG > ZD
        assert len(p["stroke_indices"]) >= engine.pivot_min_strokes
        assert p["start_idx"] < p["end_idx"]


# ---------------------------------------------------------------------------
# trend classification
# ---------------------------------------------------------------------------

def test_trend_classification_shape(result: dict):
    t = result["trend_type"]
    assert t is not None
    if "segments" in t:
        assert len(t["segments"]) >= 1
        for seg in t["segments"]:
            assert seg["type"] in ("上涨趋势", "下跌趋势", "盘整走势")
        assert "summary" in t


# ---------------------------------------------------------------------------
# buy/sell points
# ---------------------------------------------------------------------------

def test_buy_sell_point_labels(result: dict):
    allowed = {"B1", "B2", "B2*", "B3", "S1", "S2", "S2*", "S3"}
    for p in result["buy_sell_points"]:
        assert p["type"] in allowed, f"unknown label {p['type']}"
        assert p["price"] > 0


def test_analyze_pipeline_keys(result: dict):
    assert set(result.keys()) == {
        "merged_klines", "fractals", "strokes", "pivots",
        "trend_type", "buy_sell_points",
    }


def test_get_summary(engine: ChanCoreV5):
    s = engine.get_summary()
    assert s["total_strokes"] == len(engine.strokes)
    assert s["total_fractals"] == len(engine.fractals)
    assert s["total_pivots"] == len(engine.pivots)
    assert s["effective_min_amplitude"] > 0
