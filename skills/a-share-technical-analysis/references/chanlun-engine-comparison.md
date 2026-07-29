# 缠论引擎三方对比与本地改进路线

> 来源：2026-07-20 深读源码（chan.py / czsc / 本地 v5t）+ 缠论108课理论文章

## 一、三方定位

| 项目 | 代码量 | 语言 | 定位 |
|---|---|---|---|
| chan.py (Vespa314) | 7,223行（公开版，完整22000行未放） | Python 3.11+ | 开放式框架，算法可配 |
| czsc (waditu) | Rust 9 crate + Python 薄壳 | Rust + PyO3 | 工程化量化交易平台 |
| 本地 v5/v5t | ~6,177行 | Python（零外部依赖） | 实战导向简化引擎 |

## 二、本地核心文件

| 文件 | 行数 | 说明 |
|---|---|---|
| `技术/chan_core_v5.py` | 1313 | 稳定版核心引擎 |
| `技术/chan_core_v5t.py` | 1521 | **开发主力**（v5 + trade timing + 力度背驰 + 自适应参数） |
| `技术/缠论/缠论分析_v5.py` | 932 | 多周期分析入口（Plotly HTML） |
| `技术/缠论/backtest_v5t_light.py` | 256 | 轻量回测（有前视偏差） |
| `技术/缠论/walkforward_backtest_v5t.py` | 298 | 严格 walk-forward 回测 |

"t" = trade/实战版：增加 actionable_date/trade_date 标注、力度背驰、自适应参数、更保守中枢过滤。

## 三、算法完整度对比

| 算法环节 | chan.py | czsc | 本地 v5t |
|---|---|---|---|
| K线合并 | ✅ 标准 | ✅ 标准 | ✅ 三级方向判定（更鲁棒） |
| 分型识别 | ✅ | ✅ | ✅ + 连续同类取极值 |
| 笔构建 | ✅ 新/旧笔可选 | ✅ | ✅ 新笔 + 幅度过滤 |
| **线段划分** | ✅ 特征序列（SegListChan） | ✅ 含特征序列分型 | ❌ **无**（用"走势段"替代） |
| 中枢构建 | ✅ | ✅ | ✅ **三级过滤最严格** |
| **背驰判定** | ✅ 12种度量（MACD面积为主） | ✅ MACD面积 | ⚠️ **仅力度比值**（最弱） |
| 买卖点 | ✅ 6类(1/1p/2/2s/3a/3b) | ✅ | ✅ 6+2类 |
| 多级别联立 | ✅ 数据对齐+独立计算 | ✅ BarGenerator原生 | ❌ 无 |

## 四、本地独到优势（两个开源项目都没有）

1. **中枢三级过滤**（初筛 init_filter_ratio / 扩展 extend_filter_ratio / 复核 final_filter_ratio）— 比 chan.py 严格
2. **trade timing 标注**（actionable_date / trade_date）— 信号何时可执行
3. **波动率自适应参数**（_compute_effective_min_amplitude 用 HV20/HV60）
4. **中枢降级而非丢弃**（_demote_pivot → validity='sub_level'）
5. **个股/指数自动参数切换**（_infer_instrument_type）

## 五、本地两个明确短板

### 短板1：无线段划分

`_split_trend_segments()` 仅用"低点抬升+高点抬升"判上涨段，不是缠论线段。

**chan.py 的正确实现（SegListChan + EigenFX）：**
- 向上线段 → 用下降笔构成特征序列
- 特征序列元素做包含处理（CEigen 继承 CKLine_Combiner）
- 找特征序列的顶/底分型 → 线段结束
- `actual_break()` 处理假突破（第三元素后是否真突破）
- `find_revert_fx()` 处理反抽确认（第二类情况）
- `can_be_end()` 区分 gap/非gap 两种线段结束方式

### 短板2：背驰判定极简

`_stroke_strength()` = amplitude / kline_count，无 MACD、无量能。

**chan.py 的正确实现（Bi.cal_macd_metric，12种度量）：**

| 度量 | 算法 |
|---|---|
| AREA | 进出中枢笔的 MACD 柱状图面积（半段/全段） |
| HALF | 从中枢出发，MACD 同色柱累加到穿越0轴 |
| PEAK | 笔内 MACD 柱最大值 |
| DIFF | DIF 差值 |
| SLOPE | MACD 斜率 |
| AMP | 笔幅度 |
| VOLUME/AMOUNT/TURNRATE | 量能/成交额/换手率（含 AVG） |
| RSI | RSI 极值 |

**背驰判定核心（ZS.is_divergence）：**
```python
in_metric = 进中枢笔.cal_macd_metric(algo, is_reverse=False)
out_metric = 出中枢笔.cal_macd_metric(algo, is_reverse=True)
is_diver = out_metric <= divergence_rate * in_metric  # 默认 0.9
```

## 六、改进路线（按价值排序）

| 优先级 | 改进 | 来源 | 工作量 |
|---|---|---|---|
| P0 | **补 MACD 面积背驰**：进出中枢笔的 MACD 柱面积比 | chan.py ZS.is_divergence + Bi.Cal_MACD_area | 中 |
| P1 | **补线段划分**：特征序列分型 + 线段破坏 | chan.py SegListChan + EigenFX | 大 |
| P2 | **借鉴 czsc Signal→Event→Position 决策链** | czsc 四层递进 | 中（~200行Python） |
| P3 | 多级别联立（30min+日线联合） | chan.py 数据对齐方案 | 大 |

## 七、czsc 最核心借鉴：Signal→Event→Position→Operate

```
Signal（原子信号，7段式字符串：freq_params_name_v1_v2_v3_score）
  → Event（信号组合：signals_all/any/not = AND/OR/NOT）
    → Position（FSM仓位状态机，opens/exits事件驱动）
      → Operate（HL/HS/HO/LO/LE/SO/SE = 持多/持空/持币/开多/平多/开空/平空）
```

用纯 Python 实现只需 ~200 行代码，但能支撑任意复杂的量化策略定义。
czsc 有 250 个信号函数（Rust 实现），分 7 个子模块：bar/cvolp/cxt/obv/pressure/tas/vol。

## 八、chan.py 架构要点

- 核心容器 `CKLine_List`：持有 bi_list / seg_list / zs_list / bs_point_lst
- 泛型复用：`CSegListComm[SUB_LINE_TYPE]` 实现"线段的线段"递归
- 虚笔机制：最后一笔标记 is_sure=False，实时更新
- 买卖点配置：CBSPointConfig 可按买/卖方向独立配置
- 多级别：各级别独立计算，通过 sup_kl/sub_kl_list 维护父子关系
- 缓存：@make_cache 装饰器 + clean_cache() 联动

## 九、克隆仓库位置（临时）

- chan.py: ~/Downloads/_research/chan.py/
- czsc: ~/Downloads/_research/czsc/
