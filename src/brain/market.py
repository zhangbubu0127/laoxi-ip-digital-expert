import os
import re
import time
import xml.etree.ElementTree as ET

import requests

from log import get_logger
from brain.llm import load_secrets

_log = get_logger("market")

_KNOWLEDGE = os.path.join(os.path.dirname(__file__), "..", "..", "knowledge")
_INTEL_DIR = os.path.join(_KNOWLEDGE, "竞对分析")
_STALE_DAYS = 3
_TIMEOUT = 10
_FILE_CAP = 4000
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

_SG_WORDS = ("新加坡", "狮城", "留学", "NUS", "NTU", "南洋", "国立", "AEIS", "PSLE",
             "KET", "PET", "SPERS", "低龄", "陪读", "政府中学", "国际学校", "SMU", "SUTD",
             "SIT", "SIM", "Kaplan", "楷博", "PSB", "JCU", "詹姆斯库克", "理工学院", "Poly",
             "学费", "签证", "准证", "COE", "EP", "PR", "回国", "学历", "认证", "中介")

_FILES = {
    "图谱": "竞对图谱.md",
    "对比": "中新教育对比.md",
    "热点": "热点话题.md",
    "线索": "竞对线索.md",
}

_HOT_QUERIES = ["新加坡留学 2026 热点", "新加坡 低龄留学 政策 2026", "新加坡留学 家长 关心"]
_LEAD_QUERIES = ["新加坡留学 中介 机构", "新加坡留学中介 避坑"]


def search_web(query: str, top: int = 5, sg_filter: bool = True) -> list[dict]:
    """尽力而为的实时搜索：优先 Tavily（配了 key 时），否则回落 Bing RSS。
    风控/超时/无关结果一律回落 []。
    sg_filter=False 时跳过新加坡关键词过滤（调研非新加坡限定主题时用，如中本贯通）。"""
    items = _tavily_search(query, top) or _bing_rss(query, top)
    return [i for i in items if not sg_filter or _sg_relevant(i)]


def _tavily_search(query: str, top: int) -> list[dict]:
    key = _tavily_key()
    if not key:
        return []
    try:
        r = requests.post("https://api.tavily.com/search",
                          headers={"Authorization": f"Bearer {key}",
                                   "Content-Type": "application/json"},
                          json={"query": query, "max_results": max(top, 5),
                                "search_depth": "basic"},
                          timeout=_TIMEOUT)
        if r.status_code != 200:
            _log.warning("Tavily 搜索失败 status=%s", r.status_code)
            return []
        data = r.json()
        out = []
        for it in data.get("results", []):
            title = (it.get("title") or "").strip()
            url = (it.get("url") or "").strip()
            if not title or not url:
                continue
            out.append({"title": title[:120], "url": url,
                        "snippet": (it.get("content") or "")[:180]})
        return out[:top]
    except Exception as e:
        _log.warning("Tavily 搜索异常 %r: %s", query[:30], e)
        return []


def _tavily_key() -> str:
    try:
        return load_secrets().get("tavily_api_key") or ""
    except Exception:
        return ""


def _bing_rss(query: str, top: int) -> list[dict]:
    try:
        r = requests.get("https://www.bing.com/search",
                         params={"format": "rss", "count": str(max(top, 5)), "q": query},
                         headers=_HEADERS, timeout=_TIMEOUT)
        if r.status_code != 200:
            return []
        return _parse_rss(r.text, top)
    except Exception as e:
        _log.warning("Bing RSS 搜索失败 %r: %s", query[:30], e)
        return []


def hot_topics(top: int = 8) -> list[dict]:
    return _multi_search(_HOT_QUERIES, top)


def competitor_leads(top: int = 8) -> list[dict]:
    return _multi_search(_LEAD_QUERIES, top)


def intel() -> str:
    """拼【竞对情报】段：读知识库竞对分析文件；热点过期/缺失时追加实时搜索（尽力而为）。永不 raise。"""
    try:
        parts = []
        for key in ("图谱", "对比"):
            text = _read(key)
            if text:
                parts.append(f"【{key}】\n{text}")
        hot = _read("热点")
        if not hot or _stale("热点"):
            live = hot_topics(8)
            if live:
                block = "\n".join(f"- {i['title']}（{i['snippet'][:60]}）{i['url']}" for i in live)
                parts.append(f"【实时热点（搜索·尽力而为，可能有风控）】\n{block}")
        elif hot:
            parts.append(f"【热点话题】\n{hot}")
        leads = _read("线索")
        if leads:
            parts.append(f"【竞对线索（实时搜索原文）】\n{leads}")
        return "\n\n".join(parts) if parts else "（暂无市场情报，尚未同步雷达/竞对库）"
    except Exception as e:
        _log.error("市场情报读取失败: %s", e)
        return "（暂无市场情报，尚未同步雷达/竞对库）"


def refresh() -> dict:
    """实时搜索并写回情报库：盖热点话题、存竞对线索。竞对图谱/中新对比是人工润色，不覆盖。"""
    hot = hot_topics(8)
    leads = competitor_leads(8)
    if hot:
        _write_hot(hot)
    if leads:
        _write_leads(leads)
    _log.info("市场情报刷新：热点 %d 条、竞对线索 %d 条", len(hot), len(leads))
    return {"hot": len(hot), "leads": len(leads)}


def _parse_rss(body: str, top: int) -> list[dict]:
    root = ET.fromstring(body)
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        desc = re.sub(r"<[^>]+>", "", it.findtext("description") or "").strip()
        date = (it.findtext("pubDate") or "").strip()
        if title and link:
            items.append({"title": title[:120], "url": link, "snippet": desc[:180], "date": date})
        if len(items) >= top:
            break
    return items


def _sg_relevant(item: dict) -> bool:
    text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
    return any(w.lower() in text for w in _SG_WORDS)


def _multi_search(queries: list[str], top: int) -> list[dict]:
    out, seen = [], set()
    for q in queries:
        for it in search_web(q, top=max(top, 6)):
            k = it["title"]
            if k in seen:
                continue
            seen.add(k)
            out.append(it)
        time.sleep(0.3)
    return out[:top]


def _path(key: str) -> str:
    return os.path.join(_INTEL_DIR, _FILES[key])


def _read(key: str) -> str:
    try:
        with open(_path(key), "r", encoding="utf-8") as f:
            text = f.read().strip()
        return text[:_FILE_CAP] if len(text) > _FILE_CAP else text
    except OSError:
        return ""


def _stale(key: str) -> bool:
    try:
        age = time.time() - os.path.getmtime(_path(key))
        return age > _STALE_DAYS * 86400
    except OSError:
        return True


def _write_hot(items: list[dict]) -> None:
    today = time.strftime("%Y-%m-%d")
    lines = ["# 新加坡留学热点雷达",
             f"> 来源：market.refresh() 实时搜索 · 更新于 {today}",
             "> 用途：席小题出选题的市场热点原料；机器覆盖更新，勿手改",
             "", f"## 实时热点（{today}）", "", "| 话题 | 摘要 | 出处 |",
             "|:--|:--|:--|"]
    for it in items:
        lines.append(f"| {it['title']} | {it['snippet'][:40]} | [链接]({it['url']}) |")
    _write("热点", "\n".join(lines) + "\n")


def _write_leads(items: list[dict]) -> None:
    today = time.strftime("%Y-%m-%d")
    old = _read("线索")
    body = f"## 竞对/机构线索（{today}）\n"
    for it in items:
        body += f"- {it['title']} | {it['snippet'][:60]} | {it['url']}\n"
    content = ("# 竞对线索（实时搜索原文，供人工挑着润色进竞对图谱）\n"
               f"> 来源：market.refresh() 实时搜索 · 更新于 {today}\n\n")
    content += body if not old else old + "\n" + body
    _write("线索", content)


def _write(key: str, content: str) -> None:
    os.makedirs(_INTEL_DIR, exist_ok=True)
    with open(_path(key), "w", encoding="utf-8") as f:
        f.write(content)
