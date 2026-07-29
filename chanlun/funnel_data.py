# -*- coding: utf-8 -*-
"""
30min K线数据层（缠论漏斗策略专用）

数据源：
- 主源：新浪（akshare stock_zh_a_minute），个股/指数 30min，单次返回最近 ~1970 根（约1年）
- 通达信/东财/iFinD 分钟线在本机网络环境均不可用（TCP拦截/反爬/权限），见对拍报告

缓存策略（重要）：
- 每股一个 parquet：本地数据/chan_funnel/kline_30min/{code}.parquet
- 新浪只返回最近1970根，所以缓存**只增不缩**：每次拉取后与存量 merge 去重，
  历史深度随每日运行逐渐累积（这是突破1970根限制的唯一办法）
- 板块指数 30min 没有现成行情，由成分股等权合成（build_sector_index）

复权：个股用前复权(qfq)；指数不复权。
"""
import os
import re
import time
from typing import Dict, List, Optional

import pandas as pd

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../../.."))
CACHE_DIR = os.path.join(PROJECT_ROOT, "投研系统/本地数据/chan_funnel")
KLINE_DIR = os.path.join(CACHE_DIR, "kline_30min")

_COLS = ["datetime", "open", "high", "low", "close", "volume"]


def to_sina_code(ts_code: str) -> str:
    """600176.SH -> sh600176 ; 000300.SH -> sh000300 ; 399006.SZ -> sz399006"""
    m = re.match(r"(\d{6})\.(SH|SZ|sh|sz)", ts_code)
    if not m:
        return ts_code
    return f"{m.group(2).lower()}{m.group(1)}"


def is_index_code(ts_code: str) -> bool:
    code = ts_code.split(".")[0]
    exch = ts_code.split(".")[1].upper() if "." in ts_code else ""
    return (exch == "SH" and code.startswith("000")) or (exch == "SZ" and code.startswith("399"))


class FunnelData:
    """30min K线获取与缓存"""

    def __init__(self, kline_dir: str = KLINE_DIR, sleep_sec: float = 0.35):
        self.kline_dir = kline_dir
        self.sleep_sec = sleep_sec
        os.makedirs(kline_dir, exist_ok=True)

    # ------------------------------------------------------------------
    def _cache_path(self, ts_code: str) -> str:
        return os.path.join(self.kline_dir, f"{ts_code.replace('.', '_')}.parquet")

    def _fetch_sina(self, ts_code: str, adjust: Optional[str] = None) -> pd.DataFrame:
        """从新浪拉最近 ~1970 根 30min。"""
        import akshare as ak
        sina = to_sina_code(ts_code)
        if adjust is None:
            adjust = "" if is_index_code(ts_code) else "qfq"
        df = ak.stock_zh_a_minute(symbol=sina, period="30", adjust=adjust)
        if df is None or df.empty:
            return pd.DataFrame(columns=_COLS)
        out = df.rename(columns={"day": "datetime"})[_COLS].copy()
        out["datetime"] = pd.to_datetime(out["datetime"])
        for c in ["open", "high", "low", "close", "volume"]:
            out[c] = pd.to_numeric(out[c], errors="coerce")
        out = out.dropna(subset=["open", "high", "low", "close"])
        return out.sort_values("datetime").reset_index(drop=True)

    # ------------------------------------------------------------------
    def update(self, ts_code: str) -> pd.DataFrame:
        """拉取并与本地缓存 merge（只增不缩），返回全量缓存。
        网络失败时回退到本地缓存（限流/断网不阻断扫描）。"""
        path = self._cache_path(ts_code)
        try:
            new = self._fetch_sina(ts_code)
        except Exception as e:  # noqa: BLE001
            if os.path.exists(path):
                print(f"⚠️ {ts_code} 网络更新失败({type(e).__name__})，使用本地缓存")
                new = pd.DataFrame(columns=_COLS)
            else:
                raise
        if new.empty and os.path.exists(path):
            df = pd.read_parquet(path)
            df["datetime"] = pd.to_datetime(df["datetime"])
            return df
        if os.path.exists(path):
            old = pd.read_parquet(path)
            old["datetime"] = pd.to_datetime(old["datetime"])
            df = pd.concat([old, new], ignore_index=True)
        else:
            df = new
        if df.empty:
            return df
        df = df.drop_duplicates(subset=["datetime"], keep="last")
        df = df.sort_values("datetime").reset_index(drop=True)
        df.to_parquet(path, index=False)
        return df

    def get(self, ts_code: str, end_date: Optional[str] = None,
            lookback_bars: Optional[int] = None, refresh: bool = True) -> pd.DataFrame:
        """读取 30min K线（默认先增量更新）。

        Args:
            end_date: 截断到该日期(含)之前，'YYYY-MM-DD' 或 'YYYY-MM-DD HH:MM'
            lookback_bars: 只保留最后 N 根
            refresh: False 时只读本地缓存（回测批量阶段用）
        """
        if refresh or not os.path.exists(self._cache_path(ts_code)):
            df = self.update(ts_code)
        else:
            df = pd.read_parquet(self._cache_path(ts_code))
            df["datetime"] = pd.to_datetime(df["datetime"])
        if df.empty:
            return df
        if end_date:
            cutoff = pd.Timestamp(end_date)
            if cutoff.hour == 0 and cutoff.minute == 0:
                cutoff = cutoff + pd.Timedelta(hours=15)
            df = df[df["datetime"] <= cutoff]
        if lookback_bars:
            df = df.tail(lookback_bars)
        return df.reset_index(drop=True)

    # ------------------------------------------------------------------
    def batch_update(self, codes: List[str], workers: int = 1) -> Dict[str, int]:
        """批量增量更新。返回 {code: 缓存总根数}（失败为 -1）。

        workers>1 时多线程并发（sina 可承受 ~8 并发），忽略 sleep_sec。
        """
        stats: Dict[str, int] = {}
        if workers <= 1:
            for i, code in enumerate(codes):
                try:
                    df = self.update(code)
                    stats[code] = len(df)
                except Exception as e:  # noqa: BLE001
                    print(f"⚠️ {code} 更新失败: {e}")
                    stats[code] = -1
                if i < len(codes) - 1:
                    time.sleep(self.sleep_sec)
            return stats

        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading
        lock = threading.Lock()
        done = [0]

        def _one(code: str) -> int:
            try:
                df = self.update(code)
                n = len(df)
            except Exception as e:  # noqa: BLE001
                with lock:
                    print(f"⚠️ {code} 更新失败: {e}")
                n = -1
            with lock:
                done[0] += 1
                if done[0] % 100 == 0:
                    print(f"  进度 {done[0]}/{len(codes)}", flush=True)
            return n

        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_one, c): c for c in codes}
            for fut in as_completed(futs):
                stats[futs[fut]] = fut.result()
        return stats

    # ------------------------------------------------------------------
    def build_sector_index(self, members: List[str], end_date: Optional[str] = None,
                           refresh: bool = False, base: float = 1000.0) -> pd.DataFrame:
        """由成分股 30min 等权合成板块指数 OHLC。

        方法：对每只成分股计算归一化价格序列 price_t / price_t0（t0=该股首根），
        板块指数 = 各股归一化值的等权平均 × base。O/H/L 同理（用各自归一化因子）。
        仅统计当时有数据的成分股（至少3只有数据才出点）。

        Args:
            members: 成分股 ts_code 列表
            refresh: 是否先更新各成分股缓存（批量场景先统一 batch_update，这里 False）
        """
        series: List[pd.DataFrame] = []
        for code in members:
            try:
                df = self.get(code, end_date=end_date, refresh=refresh)
            except Exception:  # noqa: BLE001
                continue
            if len(df) < 30:
                continue
            base_close = df["close"].iloc[0]
            if not base_close or base_close <= 0:
                continue
            f = base / base_close
            tmp = df[["datetime"]].copy()
            for c in ["open", "high", "low", "close"]:
                tmp[c] = df[c] * f
            series.append(tmp)
        if len(series) < 3:
            return pd.DataFrame(columns=_COLS)
        merged = series[0].copy()
        for tmp in series[1:]:
            merged = merged.merge(tmp, on="datetime", how="outer", suffixes=("", "_r"))
            for c in ["open", "high", "low", "close"]:
                merged[c] = merged[[c, c + "_r"]].mean(axis=1, skipna=True)
                merged = merged.drop(columns=[c + "_r"])
        # 有效点要求：合成时至少有3只成分在该时刻有数据
        counts = series[0][["datetime"]].assign(n=1)
        for tmp in series[1:]:
            counts = counts.merge(tmp[["datetime"]].assign(n=1), on="datetime", how="outer", suffixes=("_a", "_b"))
            counts["n"] = counts[["n_a", "n_b"]].sum(axis=1, skipna=True) if "n_a" in counts else counts["n"]
            counts = counts.drop(columns=[c for c in counts.columns if c.endswith(("_a", "_b"))])
        merged = merged.merge(counts, on="datetime", how="left")
        merged = merged[merged["n"] >= 3]
        merged["volume"] = 0.0
        merged = merged.drop(columns=["n"])
        return merged.sort_values("datetime").reset_index(drop=True)[_COLS]


if __name__ == "__main__":
    fd = FunnelData()
    df = fd.update("000300.SH")
    print(f"000300.SH 缓存 {len(df)} 根, {df['datetime'].min()} ~ {df['datetime'].max()}")
    df2 = fd.get("600519.SH")
    print(f"600519.SH 缓存 {len(df2)} 根, {df2['datetime'].min()} ~ {df2['datetime'].max()}")
