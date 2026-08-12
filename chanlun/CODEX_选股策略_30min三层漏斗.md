# Codex 任务：30分钟级别三层漏斗选股策略

## 目标

在现有缠论引擎 `chan_core_v5t.py` 基础上，实现一个**三层漏斗选股策略**：

> **30分钟级别：指数出现一买 → 行业/概念板块出现二买 → 这些板块内出现三买的个股**

---

## 现有代码位置

```
../
├── chan_core_v5t.py          # 核心引擎（1521行，单类 ChanCoreV5T）
├── 缠论/
│   ├── 缠论分析_v5.py         # 多周期分析入口
│   ├── backtest_v5t_light.py  # 轻量回测
│   └── walkforward_backtest_v5t.py
└── 缠论开发/
    └── 历史核心/
```

**约束**：`chan_core_v5t.py` 是受控文件，增量修改，不重构。新功能写成独立模块。

---

## 策略逻辑（严格定义）

### 第一层：指数一买（方向确认）

**缠论定义**：某级别下跌趋势中，最后一个中枢之后的次级别走势与进入段相比力度衰减（背驰），形成趋势转折点。

**判定条件**（必须全部满足）：
1. 存在**至少2个同向（向下）中枢**（构成下跌趋势）
2. 离开最后一个中枢的走势段（c段）与进入中枢间的走势段（b段）相比：
   - MACD 红/绿柱面积：c段面积 < b段面积 × 0.9（衰减≥10%）
   - 或 MACD DIF 峰值：c段峰值 < b段峰值
3. c段创了新低（价格维度确认）
4. MACD 在 0 轴之下（一买的 MACD 定律）

**扫描标的**：沪深300、上证50、创业板指、中证500、中证1000（可配置）

### 第二层：板块二买（赛道确认）

**缠论定义**：第一类买点出现后，次级别回调的低点不跌破第一类买点。

**判定条件**：
1. 该板块在指数一买时间窗口附近（±5个交易日）存在一买
2. 一买后出现一次回调（次级别下跌）
3. 回调低点 > 一买低点（不破前低）
4. 回调幅度 < 突破幅度的 61.8%（斐波那契约束，可配置）

**扫描标的**：申万一级行业（31个）+ 常用概念板块（可配置列表）

### 第三层：个股三买（择时入场）

**缠论定义**：一个次级别走势向上离开中枢后，一个次级别走势回试，其低点不跌破中枢 ZG。

**判定条件**：
1. 个股属于第二层筛出的板块成分股
2. 存在一个已完成的30分钟中枢（至少3笔重叠）
3. 有一笔向上离开该中枢（高点 > ZG）
4. 随后一笔回试，低点 > ZG（不回中枢）
5. 这是离开后的**第一次**回试（不是第二次、第三次）
6. 回试笔的 MACD 面积 < 离开笔的 MACD 面积（力度确认，可选）

**输出**：满足条件的个股列表 + 买入参考价（回试低点）+ 止损价（ZG 下方）

---

## 实现要求

### 模块结构（新建文件，不改 chan_core_v5t.py）

```
./
├── strategy_funnel_30min.py    # 三层漏斗主逻辑
├── macd_divergence.py          # MACD背驰计算模块（P0）
├── sector_mapper.py            # 板块→成分股映射
└── funnel_config.yaml          # 策略参数配置
```

### macd_divergence.py（P0，核心前置）

```python
class MACDDivergence:
    """MACD背驰计算，供 chan_core_v5t 的 B1/S1 判定调用"""
    
    def __init__(self, fast=12, slow=26, signal=9):
        ...
    
    def compute(self, df: pd.DataFrame) -> pd.DataFrame:
        """计算 DIF/DEA/MACD柱，返回带 macd_dif/macd_dea/macd_bar 列的 df"""
    
    def stroke_macd_area(self, df, stroke_start_idx, stroke_end_idx, direction) -> float:
        """计算一笔内同色MACD柱的累计面积"""
        # direction='down' → 累加绿柱（macd_bar<0）
        # direction='up' → 累加红柱（macd_bar>0）
    
    def stroke_macd_peak(self, df, stroke_start_idx, stroke_end_idx, direction) -> float:
        """一笔内MACD柱的最大绝对值"""
    
    def is_divergence(self, df, in_stroke, out_stroke, rate=0.9) -> Tuple[bool, float]:
        """判断背驰：out_stroke力度 < in_stroke力度 × rate"""
        # 返回 (是否背驰, 衰减比例)
```

### strategy_funnel_30min.py（主逻辑）

```python
class FunnelStrategy30Min:
    """30分钟三层漏斗选股"""
    
    def __init__(self, config_path='funnel_config.yaml'):
        self.macd = MACDDivergence()
        self.chan = ChanCoreV5T  # 复用现有引擎
    
    def scan_index_buy1(self, index_codes: List[str], end_date: str) -> List[dict]:
        """第一层：扫描指数一买"""
        # 返回 [{'code': '000300.SH', 'buy1_date': ..., 'buy1_price': ..., 'divergence_rate': ...}]
    
    def scan_sector_buy2(self, index_buy1: dict, sector_codes: List[str]) -> List[dict]:
        """第二层：在指数一买窗口内扫描板块二买"""
        # 返回 [{'code': '801010.SI', 'name': '农林牧渔', 'buy2_date': ..., 'buy1_price': ...}]
    
    def scan_stock_buy3(self, sectors: List[dict], date: str) -> List[dict]:
        """第三层：在二买板块成分股中扫描三买"""
        # 返回 [{'code': '600000.SH', 'name': ..., 'sector': ..., 
        #         'buy3_price': ..., 'stop_loss': ZG, 'zs_range': (ZD, ZG)}]
    
    def run(self, date: str) -> dict:
        """执行完整漏斗，返回最终选股结果"""
    
    def backtest(self, start_date, end_date, initial_capital=1000000) -> dict:
        """回测：历史信号 → 模拟交易 → 收益/胜率/最大回撤"""
```

### sector_mapper.py

```python
class SectorMapper:
    """板块→成分股映射（数据源：iFinD/Wind/Tushare）"""
    
    def get_sw_industries(self) -> List[dict]:
        """申万一级行业列表"""
    
    def get_concept_sectors(self) -> List[dict]:
        """常用概念板块列表（可配置）"""
    
    def get_constituents(self, sector_code: str, date: str) -> List[str]:
        """获取板块在指定日期的成分股（注意成分股动态变化）"""
```

### funnel_config.yaml

```yaml
# 策略参数
index_codes:
  - "000300.SH"   # 沪深300
  - "000905.SH"   # 中证500
  - "000852.SH"   # 中证1000
  - "399006.SZ"   # 创业板指

# 一买参数
buy1:
  min_pivots: 2              # 至少2个同向中枢（趋势）
  divergence_rate: 0.9       # MACD面积衰减阈值
  macd_below_zero: true      # 要求在0轴之下
  lookback_bars: 200         # 回看K线数

# 二买参数
buy2:
  window_days: 5             # 指数一买后±N天内寻找板块一买
  max_retrace_ratio: 0.618   # 回调不超过突破幅度的61.8%
  min_breakout_bars: 3       # 突破后至少N根K线

# 三买参数
buy3:
  min_strokes_in_zs: 3       # 中枢内至少3笔
  first_pullback_only: true  # 只看第一次回试
  macd_confirm: true         # 回试MACD面积 < 离开MACD面积
  min_zs_amplitude: 0.02     # 中枢最小振幅2%

# 交易参数
trade:
  hold_days_max: 10          # 最大持仓天数（30min级别）
  stop_loss_below_zg: 0.005  # 止损：ZG下方0.5%
  position_per_stock: 0.1    # 单票仓位10%
  max_positions: 5           # 最多同时持有5只

# 数据源
data_source: "tushare"       # tushare / ifind / wind
freq: "30min"
```

---

## 关键技术约束

1. **不改 `chan_core_v5t.py` 主体**。MACD背驰模块独立，通过 monkey-patch 或参数注入方式接入 B1/S1 判定。如果必须改，只在 `_find_b1_candidate` / `_find_s1_candidate` 中增加 MACD 校验分支。

2. **数据获取**：30分钟K线优先用 Tushare（`pro.stk_mins(ts_code, freq='30min')`），token 在 `~/.hermes/ai-keys.env` 的 `TUSHARE_TOKEN`。备选 iFinD。

3. **板块成分股**：用 Tushare `index_member` 或 iFinD 板块接口。注意成分股是**时点数据**，必须用信号当天的成分股，不能用当前成分股回测。

4. **性能**：全市场5000+股票 × 200根30min K线，需要批量拉取 + 缓存。建议：
   - 第一层（指数）：5个标的，实时算
   - 第二层（板块）：~50个板块，实时算
   - 第三层（个股）：只在二买板块成分股内扫描（~200-500只），不扫全市场

5. **输出格式**：最终结果输出为 DataFrame + 终端表格，包含：
   ```
   代码 | 名称 | 板块 | 三买价格 | 止损价(ZG) | 中枢区间 | MACD衰减比 | 信号日期 | 可执行日期
   ```

6. **可执行日期**：复用 v5t 的 `actionable_date` 概念——信号出现后下一根K线开盘才可执行。

---

## 验收标准

1. `python strategy_funnel_30min.py --date 2026-07-18` 能跑出结果（或明确报"当日无信号"）
2. 对 2024-01-01 至 2026-07-18 做回测，输出：总收益率、胜率、最大回撤、平均持仓天数
3. 背驰判定必须用 MACD 面积，不能只用价格幅度
4. 三买必须检查"回试不破ZG"且是"第一次回试"
5. 板块成分股必须用历史时点数据

---

## 参考实现（已克隆在本地）

- **chan.py 背驰**：`~/Downloads/_research/chan.py/Bi/Bi.py` L189-278（12种MACD度量）
- **chan.py 中枢背驰**：`~/Downloads/_research/chan.py/ZS/ZS.py` L162-174（`is_divergence`）
- **chan.py 三买**：`~/Downloads/_research/chan.py/BuySellPoint/BSPointList.py` L297-399（`bsp3_back2zs` 检查回试）
- **chan.py 线段**：`~/Downloads/_research/chan.py/Seg/EigenFX.py`（特征序列法，本次不需要实现线段，但中枢构建参考）
- **czsc 信号体系**：`~/Downloads/_research/czsc/crates/czsc-signals/`（Signal→Event→Position 设计模式参考）
- **理论文档**：`~/缠论原始理论框架_严格定义.md`（买卖点严格定义）

---

## 执行顺序

1. 先实现 `macd_divergence.py`（P0）
2. 再实现 `sector_mapper.py`（数据管道）
3. 再实现 `strategy_funnel_30min.py`（三层漏斗）
4. 最后写回测逻辑
5. 每步完成后跑一次验证，不要一次性全写完再测
