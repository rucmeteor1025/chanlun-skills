# Chanlun / A-share technical skills

Open-source snapshot of personal **缠论 (Chan Theory)** tooling and related Hermes agent skills for A-share technical analysis.

> 研究/自用工具开源快照。**不含**行情数据缓存、HTML 报告产物、API token。数据源（Tushare / iFinD / Wind）需自备密钥。

## Contents

```text
skills/
  a-share-technical-analysis/   # Hermes skill: EMA/SMA, 缠论入口说明, 回测索引
  trend-structure-tracker/      # Hermes skill: 趋势结构 + 缠论增强思路
core/
  chan_core_v5.py               # 缠论引擎稳定版
  chan_core_v5t.py              # 缠论引擎开发版 (v5t)
chanlun/
  缠论分析_v5.py / 缠论报告_v5.py
  export_v5t_*.py               # HTML 报告导出
  backtest_v5t_light.py / walkforward_backtest_v5t.py
  strategy_funnel_30min.py      # 30min 三层漏斗策略
  funnel_*.py / funnel_config.yaml
  macd_divergence.py
  sector_mapper.py
  专项脚本/ validation/
```

## Hermes skills

Copy into your Hermes skills tree (or symlink):

```bash
# example
cp -R skills/a-share-technical-analysis ~/.hermes/skills/data-science/
cp -R skills/trend-structure-tracker ~/.hermes/skills/trading/
```

`a-share-technical-analysis` 默认指向本机 `AI_invest` 技术脚本路径；开源包里引擎在 `core/`，入口在 `chanlun/`，使用时请按本机布局改路径或设：

```bash
export AI_INVEST=/path/to/AI_invest   # if you keep original monorepo layout
# or PYTHONPATH=./core
```

## Quick start (engine)

```bash
cd chanlun
# ensure core on path
export PYTHONPATH="../core:${PYTHONPATH}"

# single-name analysis (needs market data backend configured on your machine)
python3 缠论分析_v5.py 688256.SH --count 260

# light backtest helper
python3 backtest_v5t_light.py --help
```

Data: scripts may read `TUSHARE_TOKEN` from `~/.config/ai-keys.env` or `~/.hermes/ai-keys.env` (not included). iFinD/Wind optional.

## Notes

- `输出/` HTML/parquet artifacts are **not** published (large, machine-local).
- Formal entry in original monorepo: `投研系统/工具脚本/技术/缠论/`; engines live one level up as `chan_core_v5{,t}.py`.
- Validation script may compare against external `chan.py` research clone — set path yourself.
- Chinese documentation in `chanlun/README.md` and CODEX task notes.

## License

MIT for this packaging and original scripts authored for personal research, unless a file states otherwise. Chan Theory concepts are public methodology; this is an independent implementation.

## Disclaimer

Not investment advice. For research/education only. You are responsible for data-vendor terms and trading risk.
