---
name: a-share-technical-analysis
description: >
  A股技术分析工具箱：EMA/SMA均线系统、金叉死叉信号、趋势强度评分、
  缠论分析、策略回测。覆盖投研系统/工具脚本/技术/下的全部脚本。
  所有工具输出HTML交互图+C SV数据，本地浏览器查看。
version: 1.1.0
references:
  - sector-ma-table.md: 板块均线位置表（WuChuan Style）— 跨板块多周期趋势对比，Wind MCP 自动生成
  - ema-quick-reference.md: EMA vs SMA 对比、参数表、实战解读要点
metadata:
  author: starsmith
  hermes:
    tags: [a-share, technical-analysis, ema, ma, python, plotly]
    category: data-science
    requires_toolsets: [terminal]
allowed-tools: Bash(python3:*) Read Write
---

# A股技术分析工具箱

## 目录结构

所有工具脚本位于 `~/AI_invest/投研系统/工具脚本/技术/`。

| 脚本 | 功能 | 输出目录 |
|---|---|---|
| `ma_analysis.py` | SMA 均线系统（MA5/10/20/60/120）+ 金叉死叉 | `技术分析工具/均线系统/` |
| `ema_analysis.py` | **EMA 指数均线**（EMA5-120）+ EMA vs SMA 对比 + 趋势强度 | `技术分析工具/EMA系统/` |
| `strategy_backtest.py` | SMA 金叉死叉回测（含手续费/滑点） | `技术分析工具/交易回测/` |

## 板块均线位置表（跨板块对比）（WuChuan Style）

**脚本：** `~/.hermes/scripts/ma_table.py`

使用 Wind MCP 自动拉取 30+ 宽基指数/行业/题材指数的 K 线数据，计算每个板块相对 5/10/20/30/60/120/250 日均线的站上率，按趋势强度排序输出。源自吴川的板块强弱跟踪方法。

**用法：**
```bash
python3 ~/.hermes/scripts/ma_table.py         # 全部板块
python3 ~/.hermes/scripts/ma_table.py tech     # 科技线
python3 ~/.hermes/scripts/ma_table.py theme    # 题材线
python3 ~/.hermes/scripts/ma_table.py broad    # 宽基指数
python3 ~/.hermes/scripts/ma_table.py trad     # 传统行业
python3 ~/.hermes/scripts/ma_table.py health   # 医药线
```

**输出：** 板块名称、最新价、涨跌幅、7 档均线站上状态（✅/❌）、站上数/总数。按强势→弱势排序。

**配合板块轮动分析：** 输出作为 `sector_rotation_radar_skill` 的量化输入层，直回答其 Step 2 "板块强弱分布如何"。

详情与完整解读见 `references/sector-ma-table.md`。

辅助工具（`投研系统/工具脚本/研究/`）：
- `test_growth_valuation_phase2.py` — 估值分析
- `test_growth_forecast_clarity_phase3.py` — 预测清晰度

## EMA 分析工具（主推）

**路径：** `~/AI_invest/投研系统/工具脚本/技术/ema_analysis.py`

### 核心功能
- 计算 EMA5/8/10/12/20/26/60/120（A股常用全周期）
- 同步计算 SMA 做基准对比（MA5/10/20/60/120）
- EMA vs SMA 信号速度对比（领先/滞后天数）
- EMA5/20 金叉死叉检测
- EMA12/26 交叉检测（MACD 源参数）
- 趋势强度评分（多头排列/空头排列/粘合）
- EMA 支撑/阻力聚合区自动识别
- K线图 + EMA 多周期线 + 成交量 + 信号标注

### 用法

```bash
# 默认：茅台分析
python3 ema_analysis.py

# 指定个股（A股）
python3 ema_analysis.py --code 002415.SZ

# 双股对比
python3 ema_analysis.py --code 600519.SH --compare

# 港股/美股
python3 ema_analysis.py --code 0700.HK --market HK
python3 ema_analysis.py --code AAPL --market US

# 只看理论对比（不拉数据）
python3 ema_analysis.py --theory

# 不生成图表（仅文本摘要）
python3 ema_analysis.py --code 300750.SZ --no-chart
```

### A 股 EMA 实战参数体系

| 周期 | EMA参数 | SMA对应 | 用途 |
|---|---|---|---|
| 短线 | EMA5/8/10 | MA5 | 日内/短波进出参考 |
| 趋势核 | EMA12/26 | MA10/20 | MACD 源参数，方向判断 |
| 波段 | EMA20 | MA20 | 中线多空分水岭 |
| 中期 | EMA60 | MA60 | 富途称"牛熊分界" |
| 长线 | EMA120/250 | MA120/250 | 季线/年线，大方向 |

### 推荐用法（双线验证）

1. **EMA12 金叉 EMA26** → 趋势转多信号（MACD 源）
2. **EMA20 斜率** → 趋势强度（向上=多头，向下=空头）
3. **EMA60 作多空分界** → EMA20 在 EMA60 上方只看多信号，下方只看空
4. **SMA 做确认** → EMA 出信号后等 SMA 确认再动手（过滤震荡假信号）

### 输出文件

每个标的生成 3 个文件到 `技术分析工具/EMA系统/`：
- `{code}_ema_signals.html` — 多周期 EMA 信号图（K线+EMA+成交量）
- `{code}_ema_vs_sma.html` — EMA vs SMA 对比图（同周期+价差+趋势强度）
- `{code}_ema_data.csv` — 完整计算数据

## SMA 均线分析（原有）

**路径：** `~/AI_invest/投研系统/工具脚本/技术/ma_analysis.py`

计算 MA5/10/20/60/120 + 金叉死叉信号。

```bash
python3 ma_analysis.py                           # 默认茅台
python3 ma_analysis.py                           # 改代码最后一行指定个股
```

## 策略回测

**路径：** `~/AI_invest/投研系统/工具脚本/技术/strategy_backtest.py`

SMA 金叉死叉回测（MA5 上穿 MA20 买入，下穿卖出）。
含手续费（A股0.03%/美股0.005%）、滑点（A股0.02%/美股0.01%）。

```bash
python3 strategy_backtest.py                     # 默认茅台
```

## 缠论引擎（v5/v5t）

**核心引擎：** `技术/chan_core_v5t.py`（1521行，开发主力）/ `chan_core_v5.py`（1313行，稳定版）
**分析入口：** `技术/缠论/缠论分析_v5.py`（多周期 Plotly HTML）
**回测：** `技术/缠论/backtest_v5t_light.py`（轻量）/ `walkforward_backtest_v5t.py`（严格）

功能：K线合并 → 分型 → 笔 → 中枢（三级过滤）→ 走势类型 → 买卖点（6+2类）
独到设计：中枢三级过滤、trade timing 标注（actionable_date）、波动率自适应参数、中枢降级机制

**已知短板与改进路线：** 详见 `references/chanlun-engine-comparison.md`
- P0：补 MACD 面积背驰（参考 chan.py ZS.is_divergence）
- P1：补线段划分（参考 chan.py SegListChan + EigenFX 特征序列法）
- P2：借鉴 czsc Signal→Event→Position 决策链

开源对比参考仓库：chan.py (~/Downloads/_research/chan.py/)、czsc (~/Downloads/_research/czsc/)

## 依赖安装

```bash
export PATH="$HOME/.local/bin:$PATH"
uv pip install akshare plotly pandas yfinance
```

注意：系统的 `python3` 是 uv 管理，`pip3` 是系统 Python 3.9 — **必须用 `uv pip install`** 装包。

## 大波段回撤分析（Drawdown & Golden Ratio）

对 ETF/个股做「每轮大涨后高点→低点的回撤」量化分析，从时间+空间双维度评估当前调整状态。

**触发词：** 回撤分析、回撤测算、黄金分割回撤、波段分析、drawdown analysis

### 核心方法

1. **绝对回撤幅度**：从高点到回撤低点的百分比跌幅
2. **黄金分割回撤位**：23.6% / 38.2% / 50% / 61.8% / 78.6% / 88.6%，两种算法：
   - 基于本轮上涨段（低→高）做黄金分割回撤
   - 从高点做绝对百分比回撤位
3. **时间分析**：回撤交易日/自然日，和历史分位数对比（25%/50%/75%）
4. **回撤/涨幅比**：回撤幅度占本轮涨幅的比例（<50% 强势，>80% 偏弱）
5. **Dixon-Coles 零修正**：对低比分（0:0, 1:0, 0:1, 1:1）做微调（可选）

### 分析脚本

**脚本：** `scripts/drawdown_analysis.py`

通用脚本，支持单股/ETF + 多股批量。用 akshare 拉日K（前复权），自动识别大波段（涨幅>20% + 回撤>8%），输出完整回撤统计。

```bash
# 单股
python3 scripts/drawdown_analysis.py --csv /tmp/data.csv --name "北方华创" --code 002371

# 多股批量（脚本内置文件列表）
python3 scripts/drawdown_analysis.py --batch
```

### 关键输出维度

| 维度 | 说明 |
|---|---|
| 当前回撤幅度 vs 历史均值/中位/最大 | 判断空间是否到位 |
| 已过交易日 vs 历史均值/中位 | 判断时间是否够 |
| 回撤/涨幅比 | 判断调整深度（<50%=强势回撤，>80%=深度调整） |
| 黄金分割支撑位 | 未来可能的价格支撑 |
| 时间分位数预测 | 25%/50%/75% 概率完成调整的日期 |

### 半导体板块回撤规律参考

详见 `references/semiconductor-drawdown-profile.md` — 2026-07 半导体板块（北方华创/中芯国际/京仪装备/中科飞测/拓荆科技/159558 ETF）的回撤特征：
- 历史回撤均值集中在 **-17%~-20%**
- 回撤时间均值 **15-40 交易日**
- 板块联动明显（同涨同跌，高点常集中在同一周）

### 注意事项

- **akshare 限频**：连续拉 5+ 只股票会被东财断连（`RemoteDisconnected('Remote end closed connection without response')`）。三步策略：①每只间隔 `time.sleep(8)`；②失败的缩小 `start_date` 范围重试（如从 20230101 改 20250101）；③换新浪源 `ak.stock_zh_a_daily(symbol='sh688041', ...)` — 注意新浪源列名为英文（date/open/high/low/close），需在分析脚本里做列名映射
- **execute_code 无 pandas/numpy**：hermes sandbox 环境缺这些库，改用 terminal 跑 python3（系统 Python 3.9 有 akshare/pandas/numpy）。脚本写文件后 `terminal(command='python3 /tmp/script.py')` 执行
- **execute_code subprocess 陷阱**：sandbox 的 python3 是 3.13（uv 管理），可能缺 akshare；系统 python3 是 3.9（有 akshare）。subprocess 里要用 `/usr/bin/python3` 或直接 `python3`（终端默认走系统 Python）
- **脚本存到 skill 目录**：`skill_manage(action='write_file', file_path='scripts/drawdown_analysis.py')` 可让 skill 自带可复用脚本
- **ETF 用 `fund_etf_hist_em`，个股用 `stock_zh_a_hist`**，两个接口参数不同
- **优先 Tushare 日线（已部署）**：`python3 ~/.openclaw/workspace/invest/data/tushare_client.py daily --ts-code XXX.SH --start YYYYMMDD --end YYYYMMDD`；失败再 akshare/新浪。硬数字标 `Tushare · 日期`。双人 2000 积分：定点取数、禁全市场狂刷（`tushare-finance`）

## Pitfalls

- **Chart 文件用浏览器打开**（不支持 Telegram 内嵌渲染），发文件给用户时提供 MEDIA 路径
- Vision 分析大 chart 图可能失败（模型 provider 限制），改用本地打开
- AKShare 有时会改接口字段名，`ema_analysis.py` 用了 `stock_zh_a_hist` 标准列映射
- `--no-chart` 模式只输出文本摘要，适合快速检查
