# Chanlun Engine — Chan Theory Technical Analysis Toolkit for A-Share Markets

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Status](https://img.shields.io/badge/Status-Research%20Snapshot-blue)

An independent, engineering-grade implementation of **缠论 (Chan Theory)** — the Chinese technical-analysis methodology built on fractal market structure — designed for A-share (CN) markets and integrated with AI-agent tooling for automated research workflows.

This repository is a curated research snapshot of a personal quantitative research system: a pure-Python Chan Theory engine, strategy scaffolds, walk-forward backtesting, and reusable agent skills that let an LLM agent (Hermes) run technical analysis autonomously.

---

## Why Chan Theory?

Chan Theory decomposes price action into a strict hierarchy of structure — **merging K-lines → fractals → strokes (笔) → pivots/centers (中枢) → trend segments → buy/sell signals** — without relying on arbitrary indicator thresholds. It is one of the most widely used frameworks in Chinese retail/institutional trading, but most public implementations are ad-hoc and untested. This project treats it as *software engineering*: explicit invariants, adaptive parameter inference, and honest out-of-sample validation.

## Key Features

- **Complete Chan pipeline** (`core/chan_core_v5.py`, ~1,300 LOC, zero hard-coded magic):
  - K-line merging with gap rules (merge_klines)
  - Fractal identification (identify_fractals)
  - Stroke (笔) construction (identify_strokes)
  - Pivot/center (中枢) recognition with amplitude validation (identify_pivots, _validate_pivot_amplitudes)
  - Trend segmentation & classification (classify_trend)
  - **Type-1/2/3 buy & sell points** (identify_buy_sell_points)
- **Adaptive parameters**: effective minimum amplitude and pivot rules are inferred per-instrument from realized volatility (`_compute_effective_min_amplitude`, `_compute_effective_pivot_rules`) — no one-size-fits-all thresholds.
- **30-min three-layer funnel strategy** (`chanlun/strategy_funnel_30min.py`): scans candidates through staged buy-point filters (B1/B2/B3) for stock screening.
- **Strict walk-forward backtesting** (`chanlun/walkforward_backtest_v5t.py`): rolling train/validation windows to mitigate overfitting, plus a lightweight backtest (`backtest_v5t_light.py`).
- **MACD divergence detection** (`chanlun/macd_divergence.py`).
- **Sector mapping** (`chanlun/sector_mapper.py`) for rotation context.
- **Cross-implementation validation** (`chanlun/validation/chanpy_crosscheck.py`): structure-level 对拍 (diff) against an independent third-party implementation to catch logic drift.
- **AI-agent integration**: ready-to-use Hermes agent skills for automated trend-structure tracking and technical analysis (`skills/`).

## Repository Layout

```text
core/
  chan_core_v5.py            # Chan engine, stable release
  chan_core_v5t.py           # Chan engine, development line (v5t)
chanlun/
  缠论分析_v5.py             # single-instrument full analysis (Plotly HTML output)
  缠论报告_v5.py             # fixed-list summary reports (terminal tables)
  export_v5t_*.py            # HTML report exporters (single / watchlist / index)
  backtest_v5t_light.py      # lightweight backtest
  walkforward_backtest_v5t.py# strict walk-forward backtest
  strategy_funnel_30min.py   # 30-min three-layer funnel screening strategy
  funnel_*.py, funnel_config.yaml
  macd_divergence.py         # MACD divergence detector
  sector_mapper.py           # sector mapping helper
  专项脚本/                   # special-purpose minute-level scripts
  validation/                # cross-check against third-party chan.py
skills/
  a-share-technical-analysis/  # Hermes skill: EMA/SMA systems, Chan entry, backtest index
  trend-structure-tracker/     # Hermes skill: trend structure + Chan-enhanced framework
```

## Quick Start

```bash
# engine on path
export PYTHONPATH="$PWD/core:${PYTHONPATH}"

# single-instrument full Chan analysis (requires a market-data backend; see below)
cd chanlun
python3 缠论分析_v5.py 688256.SH --count 260

# walk-forward backtest CLI
python3 walkforward_backtest_v5t.py --help
```

**Market data:** the pipeline is data-backend agnostic — scripts read a `TUSHARE_TOKEN` (Tushare Pro) from `~/.config/ai-keys.env` or `~/.hermes/ai-keys.env`; iFinD / Wind connectors are optional. No data or credentials are bundled in this repo.

## Example Output

- `chanlun/缠论分析_v5.py 688256.SH --count 260` → interactive Plotly HTML report: merged K-lines, fractal/stroke/pivot overlays, trend segments, and labeled buy/sell points, saved to `输出/`.
- Strategy funnel scans emit ranked candidate lists with staged signal types and config-driven thresholds (`funnel_config.yaml`).

## Using with Hermes (LLM agent)

Copy the skills into your Hermes skills tree:

```bash
cp -R skills/a-share-technical-analysis ~/.hermes/skills/data-science/
cp -R skills/trend-structure-tracker ~/.hermes/skills/trading/
```

The skills encode the analysis workflow (parameter conventions, backtest index, entry points) so an agent can run the whole pipeline from a natural-language request.

## Validation & Honesty Notes

- Walk-forward design is used rather than naive single-window backtests; still, treat results as research, not production signals.
- `validation/chanpy_crosscheck.py` diffs structure output against an independent implementation (external clone path is configurable).
- HTML/parquet artifacts and cached market data are intentionally **not** published (machine-local, large, and licensed).

## License & Disclaimer

MIT — this is an independent implementation for personal research; Chan Theory itself is public methodology. **Not investment advice.** You are responsible for data-vendor license terms and all trading risk.

---

*Chinese-language docs: `chanlun/README.md` (engine entry) and `chanlun/CODEX_*.md` task notes.*
