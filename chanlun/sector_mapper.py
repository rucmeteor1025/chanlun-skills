# -*- coding: utf-8 -*-
"""
板块 → 成分股映射（新浪行业板块体系）

背景：通达信(mootdx/tdxpy)在本网络环境不可用（透明TCP拦截），
东财接口被反爬，故板块体系采用新浪行业板块（约50个）。

接口：
- 板块列表: vip.stock.finance.sina.com.cn/q/view/newSinaHy.php
- 成分股:   vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData

注意（幸存者偏差）：新浪只有当前成分快照，无历史时点成分。
缓解措施：
1. 每次运行把当日成分快照落盘（本地数据/chan_funnel/sector_members/），为实盘积累时点历史；
2. 回测时调用方应剔除回测起点之后上市的个股（filter_by_list_date）。
"""
import json
import os
import re
import time
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import requests

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CURRENT_DIR, "../../../.."))
CACHE_DIR = os.path.join(PROJECT_ROOT, "投研系统/本地数据/chan_funnel")
MEMBER_SNAPSHOT_DIR = os.path.join(CACHE_DIR, "sector_members")

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"}
_TIMEOUT = 15


def _to_ts_code(sina_symbol: str) -> str:
    """sh600176 -> 600176.SH ; sz000001 -> 000001.SZ"""
    m = re.match(r"(sh|sz)(\d{6})", sina_symbol)
    if not m:
        return sina_symbol
    return f"{m.group(2)}.{m.group(1).upper()}"


def _to_sina_code(ts_code: str) -> str:
    """600176.SH -> sh600176"""
    code, _, exch = ts_code.partition(".")
    return f"{exch.lower()}{code}"


class SectorMapper:
    """新浪行业板块 → 成分股映射"""

    def __init__(self, cache_dir: str = CACHE_DIR):
        self.cache_dir = cache_dir
        self.snapshot_dir = MEMBER_SNAPSHOT_DIR
        self._sw_cache: Optional[pd.DataFrame] = None
        os.makedirs(self.snapshot_dir, exist_ok=True)

    # ------------------------------------------------------------------
    def get_industries(self) -> List[Dict]:
        """新浪行业板块列表。返回 [{'code': 'new_blhy', 'name': '玻璃行业', 'count': 19}]"""
        url = "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"
        r = requests.get(url, headers=_UA, timeout=_TIMEOUT)
        r.raise_for_status()
        text = r.content.decode("gbk", errors="ignore")
        m = re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.DOTALL)
        if not m:
            raise RuntimeError("新浪行业板块列表解析失败")
        data = json.loads(m.group(1))
        rows = []
        for key, val in data.items():
            parts = val.split(",")
            if len(parts) < 3:
                continue
            rows.append({
                "code": parts[0],
                "name": parts[1],
                "count": int(parts[2]) if parts[2].isdigit() else None,
            })
        rows.sort(key=lambda x: x["code"])
        return rows

    # ------------------------------------------------------------------
    def get_constituents(self, sector_code: str, retry: int = 2) -> List[Dict]:
        """板块成分股（当前快照）。返回 [{'code': '600176.SH', 'name': '中国巨石'}]"""
        out: List[Dict] = []
        page = 1
        while True:
            url = (
                "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
                f"Market_Center.getHQNodeData?page={page}&num=80&sort=symbol&asc=1"
                f"&node={sector_code}&symbol=&_s_r_a=page"
            )
            last_err: Optional[Exception] = None
            for attempt in range(retry + 1):
                try:
                    r = requests.get(url, headers=_UA, timeout=_TIMEOUT)
                    r.raise_for_status()
                    text = r.text.strip()
                    data = json.loads(text) if text and text != "null" else []
                    break
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    time.sleep(1 + attempt)
                    data = None
            if data is None:
                raise RuntimeError(f"成分股拉取失败 {sector_code} page={page}: {last_err}")
            if not data:
                break
            for item in data:
                sym = item.get("symbol", "")
                out.append({
                    "code": _to_ts_code(sym),
                    "name": item.get("name", ""),
                })
            if len(data) < 80:
                break
            page += 1
            time.sleep(0.3)
        # 去重
        seen, dedup = set(), []
        for it in out:
            if it["code"] not in seen:
                seen.add(it["code"])
                dedup.append(it)
        return dedup

    # ------------------------------------------------------------------
    def get_all_sector_members(self, sectors: Optional[List[Dict]] = None,
                               sleep_sec: float = 0.5) -> Dict[str, List[Dict]]:
        """批量拉取所有板块成分。返回 {sector_code: [members]}"""
        if sectors is None:
            sectors = self.get_industries()
        result: Dict[str, List[Dict]] = {}
        for s in sectors:
            try:
                result[s["code"]] = self.get_constituents(s["code"])
            except Exception as e:  # noqa: BLE001
                print(f"⚠️ 板块 {s['name']}({s['code']}) 成分拉取失败: {e}")
                result[s["code"]] = []
            time.sleep(sleep_sec)
        return result

    # ------------------------------------------------------------------
    def save_snapshot(self, members_map: Dict[str, List[Dict]],
                      sector_names: Optional[Dict[str, str]] = None,
                      date: Optional[str] = None) -> str:
        """把当日成分快照落盘 parquet，返回文件路径。"""
        date = date or datetime.now().strftime("%Y%m%d")
        rows = []
        for sector_code, members in members_map.items():
            for m in members:
                rows.append({
                    "date": date,
                    "sector_code": sector_code,
                    "sector_name": (sector_names or {}).get(sector_code, ""),
                    "stock_code": m["code"],
                    "stock_name": m.get("name", ""),
                })
        df = pd.DataFrame(rows)
        path = os.path.join(self.snapshot_dir, f"{date}.parquet")
        df.to_parquet(path, index=False)
        return path

    def load_snapshot(self, date: str) -> Optional[pd.DataFrame]:
        """读取指定日期快照；没有则返回 None。"""
        path = os.path.join(self.snapshot_dir, f"{date}.parquet")
        if not os.path.exists(path):
            return None
        return pd.read_parquet(path)

    def latest_snapshot_date(self) -> Optional[str]:
        files = sorted(f for f in os.listdir(self.snapshot_dir) if f.endswith(".parquet"))
        return files[-1][:-8] if files else None

    # ------------------------------------------------------------------
    @staticmethod
    def filter_by_list_date(members: List[Dict], min_list_date: str,
                            list_dates: Dict[str, str]) -> List[Dict]:
        """剔除在 min_list_date(YYYYMMDD) 之后上市的个股（回测幸存者偏差缓解）。

        list_dates: {ts_code: 'YYYYMMDD'}（如来自 tushare stock_basic）
        缺失上市日期的个股默认保留。
        """
        out = []
        for m in members:
            ld = list_dates.get(m["code"])
            if ld and ld > min_list_date:
                continue
            out.append(m)
        return out

    # ---- 申万行业（tushare，历史时点成分）----
    def get_industries_sw(self) -> List[Dict]:
        return get_sw_industries()

    def get_constituents_pit(self, l1_code: str, date: str) -> List[Dict]:
        """指定日期的时点成分（in_date<=date 且未调出）"""
        if self._sw_cache is None:
            self._sw_cache = get_sw_memberships()
        return get_sw_constituents_pit(l1_code, date, self._sw_cache)

    def sw_union_members(self, dates: List[str]) -> Dict[str, List[Dict]]:
        """多个日期成分的并集（按板块分组），用于K线缓存预取。"""
        if self._sw_cache is None:
            self._sw_cache = get_sw_memberships()
        result: Dict[str, List[Dict]] = {}
        for s in get_sw_industries():
            seen, acc = set(), []
            for d in dates:
                for m in get_sw_constituents_pit(s["code"], d, self._sw_cache):
                    if m["code"] not in seen:
                        seen.add(m["code"])
                        acc.append(m)
            result[s["code"]] = acc
        return result


def get_list_dates(cache_days: int = 30) -> Dict[str, str]:
    """全市场上市日期（tushare stock_basic），带本地缓存。返回 {ts_code: 'YYYYMMDD'}"""
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, "stock_list_dates.parquet")
    if os.path.exists(cache_path):
        age_days = (time.time() - os.path.getmtime(cache_path)) / 86400
        if age_days < cache_days:
            df = pd.read_parquet(cache_path)
            return dict(zip(df["ts_code"], df["list_date"]))
    token = None
    for env_path in ["~/.config/ai-keys.env", "~/.hermes/ai-keys.env"]:
        p = os.path.expanduser(env_path)
        if os.path.exists(p):
            for line in open(p):
                if line.startswith("TUSHARE_TOKEN="):
                    token = line.strip().split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if token:
            break
    if not token:
        raise RuntimeError("未找到 TUSHARE_TOKEN")
    import tushare as ts
    pro = ts.pro_api(token)
    df = pro.stock_basic(exchange="", list_status="L",
                         fields="ts_code,list_date")
    df = df.dropna(subset=["list_date"])
    df.to_parquet(cache_path, index=False)
    return dict(zip(df["ts_code"], df["list_date"]))


# ---------------------------------------------------------------------------
# 申万行业成分（tushare index_member_all，含 in/out 日期 = 历史时点成分）
# ---------------------------------------------------------------------------
def _get_tushare_token() -> str:
    for env_path in ["~/.config/ai-keys.env", "~/.hermes/ai-keys.env"]:
        p = os.path.expanduser(env_path)
        if os.path.exists(p):
            for line in open(p):
                if line.startswith("TUSHARE_TOKEN="):
                    return line.strip().split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("未找到 TUSHARE_TOKEN")


def get_sw_memberships(cache_days: int = 30) -> pd.DataFrame:
    """申万行业全量成分（当前+历史进出），列:
    l1_code/l1_name/ts_code/name/in_date/out_date/is_new。带30天缓存。

    is_new=Y → 当前成员（out_date为空）；is_new=N → 历史已调出成员。
    两者合并即可还原任意时点的成分（in_date<=date 且 out_date为空或>=date）。
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, "sw_memberships.parquet")
    if os.path.exists(cache_path):
        age_days = (time.time() - os.path.getmtime(cache_path)) / 86400
        if age_days < cache_days:
            return pd.read_parquet(cache_path)
    import tushare as ts
    pro = ts.pro_api(_get_tushare_token())
    frames = []
    for is_new in ("Y", "N"):
        off = 0
        while True:
            df = pro.index_member_all(is_new=is_new, offset=off, limit=5000)
            if df is None or len(df) == 0:
                break
            frames.append(df)
            if len(df) < 5000:
                break
            off += 5000
            time.sleep(1)
        time.sleep(1)
    all_df = pd.concat(frames, ignore_index=True)
    all_df = all_df.drop_duplicates(subset=["l1_code", "ts_code", "in_date"])
    all_df.to_parquet(cache_path, index=False)
    return all_df


def get_sw_industries() -> List[Dict]:
    """申万一级行业列表（31个）: [{'code': '801010.SI', 'name': '农林牧渔'}]"""
    df = get_sw_memberships()
    rows = (df[["l1_code", "l1_name"]].drop_duplicates()
            .rename(columns={"l1_code": "code", "l1_name": "name"})
            .sort_values("code"))
    return rows.to_dict("records")


def get_sw_constituents_pit(l1_code: str, date: str,
                            memberships: Optional[pd.DataFrame] = None) -> List[Dict]:
    """申万一级行业在 date(YYYYMMDD 或 YYYY-MM-DD) 的时点成分。

    规则：in_date <= date 且（out_date 为空 或 out_date >= date）。
    """
    date = str(date).replace("-", "")[:8]
    if memberships is None:
        memberships = get_sw_memberships()
    sub = memberships[memberships["l1_code"] == l1_code]
    pit = sub[(sub["in_date"] <= date) &
              (sub["out_date"].isna() | (sub["out_date"] >= date))]
    return [{"code": r["ts_code"], "name": r["name"]} for _, r in pit.iterrows()]


if __name__ == "__main__":
    mapper = SectorMapper()
    sectors = mapper.get_industries()
    print(f"行业板块数: {len(sectors)}")
    for s in sectors[:5]:
        print(" ", s)
    demo = mapper.get_constituents(sectors[0]["code"])
    print(f"\n{sectors[0]['name']} 成分 {len(demo)} 只, 前5: {[m['code'] for m in demo[:5]]}")
