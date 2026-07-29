# Codex 任务：缠论分析脚本多周期支持 + 数据自检补齐

## 背景

`缠论分析_v5.py` 目前只支持日线分析，且不检查数据是否最新。
每次 Claude 需要分析都要自己加载数据、调引擎，浪费 token。

**目标：** 脚本自己负责数据检查 + 补齐，Claude 只调脚本、读输出。

---

## 改动要求

### 1. 新增 `--freq` 参数

```
python 缠论分析_v5.py 002371 --freq daily    # 日线（默认）
python 缠论分析_v5.py 002371 --freq 30min    # 30分钟
python 缠论分析_v5.py 002371 --freq daily 30min   # 同时分析两个周期
```

- `daily`：从 `stock_daily.parquet` 取数
- `30min`：从 `stock_30min.parquet` 取数（字段：stockcode/datetime/open/high/low/close/volume/amt）

### 2. 启动时自动检查数据新鲜度

**日线** (`stock_daily.parquet`)：
- 检查该 symbol 最新 tradingdate
- 若不是最近交易日（允许1个自然日延迟），提示数据过期 + 建议命令

**30min** (`stock_30min.parquet`)：
- 检查该 symbol 是否存在
- 若不存在或最新 datetime 距今超过2个交易日：进入补齐流程（见下）

### 3. 数据补齐流程（仅30min需要，日线由 update_market 每日自动更新）

```
优先级1：尝试 JUCC（内网 192.168.1.104）
  → 若连接成功：自动拉取，追加写入 stock_30min.parquet

优先级2：JUCC 不可用时（非内网）
  → 打印提示：
    "JUCC 不可用（非内网环境）"
    "请选择补齐方式："
    "  1. iFinD（输入 1）- 消耗月度额度，请谨慎"
    "  2. 跳过，使用现有数据（输入 2）"
    "  3. 退出（输入 3）"
  → 根据用户输入决定后续行为
  → 选 1：调用 iFinD 接口拉取该 symbol 近500根30min K线，写入 parquet
```

### 4. 30min 数据写入规范

与现有 `stock_30min.parquet` 格式对齐：
- 列名：`stockcode` / `datetime` / `open` / `high` / `low` / `close` / `volume` / `amt`
- 增量追加（不重复），按 `stockcode + datetime` 去重
- 原子写入（.tmp → rename）

### 5. 多周期输出

同时分析两个周期时，HTML 报告中包含两张图（日线 + 30min），或分别生成两个 HTML 文件（命名区分 `_daily` / `_30min`）。

控制台输出格式参考（简洁即可）：
```
【日线】合并K线=370 笔=20 中枢=4 走势=盘整延伸
  最新中枢：2025-08-05~2026-03-09  ZG=473.66 ZD=386.62
  最新买卖点：B2* 2025-11-21 386.62

【30分钟】合并K线=323 笔=13 中枢=2 走势=盘整延伸
  最新中枢：2026-01-16~2026-03-11  ZG=496.65 ZD=461.01
  最新买卖点：S2* 2026-02-13 496.65
```

---

## 不需要改动的部分

- `chan_core_v5.py` 引擎本身不动
- `data_api.py` 不动
- 日线数据由 Win 端 `update_market.bat` 每日自动更新，脚本只读不写 `stock_daily.parquet`
- `JUCC内部数据库管理系统/` 不可修改

---

## 验证

```bash
# 日线（有数据，直接跑）
python3 缠论分析_v5.py 002371 --freq daily

# 30min（002371 在库，直接跑）
python3 缠论分析_v5.py 002371 --freq 30min

# 双周期
python3 缠论分析_v5.py 002371 --freq daily 30min

# 30min 不在库的股票（触发补齐流程）
python3 缠论分析_v5.py 600036 --freq 30min
```
