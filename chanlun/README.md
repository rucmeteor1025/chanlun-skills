# 缠论正式入口

这是当前唯一推荐使用的缠论入口目录。

## 正式入口

- `缠论分析_v5.py`
  - 单标的完整分析
  - 输出 Plotly HTML 到 `输出/`
- `缠论报告_v5.py`
  - 固定标的缠论报告
  - 输出终端表格摘要
- `export_v5t_single_stock_report_html.py`
  - 单标的 `v5t` 结构 + 买卖点 + 轻量回测 HTML
- `export_v5t_selected_report_html.py`
  - 多标的 `v5t` HTML 报告
- `export_v5t_cached_index_report_html.py`
  - 指数 `v5t` HTML 报告
- `backtest_v5t_light.py`
  - 轻量回测
- `walkforward_backtest_v5t.py`
  - 严格 walk-forward 回测
- `专项脚本/`
  - 个别分钟级 / 港股专项脚本

## 核心引擎

- 主核心：`../chan_core_v5.py`
- `v5t` 核心：`../chan_core_v5t.py`

## 输出目录

- 当前输出：`输出/`

## 常用命令

```bash
# 寒武纪完整缠论图
python3 './缠论分析_v5.py' 688256.SH --count 260

# 固定报告
python3 './缠论报告_v5.py' --count 260

# 寒武纪 v5t 单股报告
python3 './export_v5t_single_stock_report_html.py' --code 688256.SH --name '寒武纪-U'
```

## 历史目录

旧脚本、旧核心、办公室版资料已整理到同级目录 `../缠论开发/`，仅作兼容和历史参考。
