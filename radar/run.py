#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
新加坡热点雷达 · 聚合与输出
- 全网搜索层：读 output/search_input.json（每天由 Claude 用 WebSearch 填充）
- B站层：调 scrapers/bilibili.py 实时抓取，补真实播放量
- 合并去重 → 写本地 md/csv → 写飞书多维表格

用法：
  python3 run.py --dry-run     只打印+落本地，不写飞书
  python3 run.py               落本地 + 写飞书表
"""
import os
import sys
import json
import csv
import time
import subprocess
from datetime import date

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "scrapers"))

FIELDS = ["话题", "类别", "平台", "热度", "热度数据", "出处链接", "采集日期"]
TODAY = date.today().isoformat()


def load_config():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def load_keywords():
    path = os.path.join(ROOT, "keywords.txt")
    with open(path, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def norm(s):
    """标题归一化用于去重。"""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def fmt_play(n):
    n = int(n or 0)
    if n >= 10000:
        return f"播放{n/10000:.1f}万"
    return f"播放{n}"


def heat_by_play(n):
    n = int(n or 0)
    if n >= 10000:
        return "高"
    if n >= 3000:
        return "中"
    return "低"


def classify(title):
    t = title or ""
    if any(k in t for k in ["陪读", "低龄", "初中", "初三", "小学", "中学", "AEIS", "插班", "国际学校"]):
        return "低龄留学"
    if any(k in t for k in ["费用", "学费", "多少钱", "签证", "准证", "花费", "成本"]):
        return "费用签证"
    if any(k in t for k in ["政策", "新规", "门槛", "PSB", "认证", "中留服", "收紧"]):
        return "留学政策"
    return "综合话题"


SG_ANCHORS = [
    "新加坡", "狮城", "南洋理工", "南大", "国立大学", "新国立", "国大",
    "nus", "ntu", "smu", "sutd", "sim", "kaplan", "楷博", "psb", "aeis",
    "詹姆斯库克", "jcu", "psle", "ip直通", "政府中学", "spers",
]


def is_sg_relevant(title):
    """B站模糊搜索会拉进只沾一个词的无关视频，用锚点词过滤，只留真新加坡话题。"""
    t = (title or "").lower()
    return any(a in t for a in SG_ANCHORS)


FRESH_DAYS = 120  # B站"最新"窗口：该细分领域投稿稀疏，14天内几乎为0，放宽到近~4个月


def fmt_pubdate(ts):
    ts = int(ts or 0)
    if ts <= 0:
        return ""
    return time.strftime("%m-%d", time.localtime(ts))


def _bili_row(it, fresh=False):
    play = it.get("play", 0)
    tail = f" · {it['author']}" if it.get("author") else ""
    if fresh and it.get("pubdate"):
        tail += f" · {fmt_pubdate(it['pubdate'])}发布"
    return {
        "话题": it["title"],
        "类别": classify(it["title"]),
        "平台": "B站",
        "热度": heat_by_play(play),
        "热度数据": fmt_play(play) + tail,
        "出处链接": it.get("url", ""),
        "采集日期": TODAY + " 00:00:00",
    }


def collect_bilibili(keywords, fresh_limit=15, ever_limit=20):
    """返回 (最新, 常青) 两组 B站话题。
    最新：按发布时间取近 FRESH_DAYS 天内的相关视频，发布时间倒序（最新在前）。
    常青：历史高播放的相关视频（老热点沉淀用）。
    两次搜索之间停顿，避免 B站 限流。
    """
    try:
        import bilibili
    except Exception as e:
        print(f"[run] 无法导入 bilibili 采集模块: {e}", file=sys.stderr)
        return [], []
    cutoff = time.time() - FRESH_DAYS * 86400
    # 最新：按发布时间搜
    try:
        newest = bilibili.collect(keywords, per_keyword=8, order="pubdate")
    except Exception as e:
        print(f"[run] B站(最新)采集失败: {e}", file=sys.stderr)
        newest = []
    newest = [it for it in newest
              if is_sg_relevant(it.get("title", "")) and int(it.get("pubdate", 0)) >= cutoff]
    newest.sort(key=lambda x: x.get("pubdate", 0), reverse=True)  # 最新发布在前
    fresh_rows = [_bili_row(it, fresh=True) for it in newest[:fresh_limit]]
    time.sleep(2)  # 两轮搜索之间停顿，避免限流
    # 常青：按历史播放量搜
    try:
        top = bilibili.collect(keywords, per_keyword=6, order="click")
    except Exception as e:
        print(f"[run] B站(常青)采集失败: {e}", file=sys.stderr)
        top = []
    top = [it for it in top if is_sg_relevant(it.get("title", ""))]
    ever_rows = [_bili_row(it, fresh=False) for it in top[:ever_limit]]
    return fresh_rows, ever_rows


PLATFORM_OPTIONS = {"全网", "知乎", "微博", "虎扑", "B站", "抖音", "小红书"}


def norm_platform(p):
    """把来源名归一到 select 选项，未知来源归入'全网'（具体来源保留在热度数据里）。"""
    p = (p or "").strip()
    if p in PLATFORM_OPTIONS:
        return p
    if "知乎" in p:
        return "知乎"
    if "微博" in p:
        return "微博"
    if "虎扑" in p or "hupu" in p.lower():
        return "虎扑"
    if "小红书" in p or "小红薯" in p:
        return "小红书"
    if "抖音" in p:
        return "抖音"
    if "b站" in p.lower() or "哔哩" in p:
        return "B站"
    return "全网"


def _load_tier(fname):
    """读一层输入 json（search_input / evergreen_input），归一平台、盖当日采集日期。"""
    path = os.path.join(ROOT, "output", fname)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    rows = []
    for r in raw:
        src = r.get("平台", "全网")
        plat = norm_platform(src)
        heat_data = r.get("热度数据", "")
        # 具体来源名（如"联合早报"）被归入全网时，补进热度数据不丢失
        if plat == "全网" and src and src != "全网" and src not in heat_data:
            heat_data = f"{src}·{heat_data}" if heat_data else src
        rows.append({
            "话题": r.get("话题", ""),
            "类别": r.get("类别", "综合话题"),
            "平台": plat,
            "热度": r.get("热度", "中"),
            "热度数据": heat_data,
            "出处链接": r.get("出处链接", ""),
            "采集日期": TODAY + " 00:00:00",
        })
    return rows


def load_search_tier():
    """全网当天新鲜话题（→ 每日日期表）。"""
    rows = _load_tier("search_input.json")
    if not rows:
        print("[run] 无 search_input.json，跳过全网搜索层", file=sys.stderr)
    return rows


def load_evergreen_tier():
    """抖音/小红书等公开话题池（→ 长期热点库，供人工再搜）。"""
    return _load_tier("evergreen_input.json")


def dedup(rows):
    seen = set()
    out = []
    for r in rows:
        k = norm(r["话题"])
        if not k or k in seen:
            continue
        seen.add(k)
        out.append(r)
    return out


def write_local(rows):
    md_path = os.path.join(ROOT, "output", f"radar_{TODAY}.md")
    csv_path = os.path.join(ROOT, "output", f"radar_{TODAY}.csv")
    heat_icon = {"高": "🔥🔥🔥", "中": "🔥🔥", "低": "🔥"}
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# 新加坡热点雷达 · {TODAY}\n\n")
        f.write("> 只列话题+出处，不扒正文\n\n")
        f.write("| 热度 | 平台 | 类别 | 话题 | 数据 | 出处 |\n")
        f.write("|:--:|:--:|:--:|:--|:--|:--|\n")
        for r in rows:
            f.write(f"| {heat_icon.get(r['热度'], '')} | {r['平台']} | {r['类别']} | "
                    f"{r['话题']} | {r['热度数据']} | [链接]({r['出处链接']}) |\n")
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return md_path, csv_path


# 每日新表的字段结构（与首张表一致）
TABLE_SCHEMA = [
    {"name": "话题", "type": "text"},
    {"name": "类别", "type": "select",
     "options": [{"name": "留学政策"}, {"name": "低龄留学"}, {"name": "费用签证"}, {"name": "综合话题"}]},
    {"name": "平台", "type": "select",
     "options": [{"name": "全网"}, {"name": "知乎"}, {"name": "微博"}, {"name": "虎扑"}, {"name": "B站"}, {"name": "抖音"}, {"name": "小红书"}]},
    {"name": "热度", "type": "select",
     "options": [{"name": "高"}, {"name": "中"}, {"name": "低"}]},
    {"name": "热度数据", "type": "text"},
    {"name": "出处链接", "type": "text"},
    {"name": "采集日期", "type": "datetime"},
]


def _lark(args):
    """跑一条 lark-cli 命令，返回解析后的 dict。失败返回 {'ok': False,...}。"""
    res = subprocess.run(["lark-cli"] + args, capture_output=True, text=True)
    try:
        return json.loads("\n".join(l for l in res.stdout.splitlines()
                                    if not l.strip().startswith(("█", "▀"))))
    except Exception:
        return {"ok": False, "raw": res.stdout[-300:], "err": res.stderr[-300:]}


def _table_names(base_token):
    d = _lark(["base", "+table-list", "--base-token", base_token, "--as", "user", "--json"])
    data = d.get("data", {})
    items = data.get("items") or data.get("tables") or data.get("data") or []
    return [t.get("name") for t in items if isinstance(t, dict)]


def ensure_table(base_token, table):
    """确保指定名字的表存在，不存在就按 schema 新建。"""
    if table in _table_names(base_token):
        return True
    d = _lark(["base", "+table-create", "--base-token", base_token,
               "--name", table, "--fields", json.dumps(TABLE_SCHEMA, ensure_ascii=False),
               "--as", "user", "--json"])
    if not d.get("ok"):
        print(f"[run] 建表 {table} 失败: {d}", file=sys.stderr)
        return False
    return True


def clear_table(base_token, table):
    """清空当天表的所有记录，实现当天反复跑不重复。"""
    d = _lark(["base", "+record-list", "--base-token", base_token,
               "--table-id", table, "--limit", "200", "--as", "user", "--json"])
    ids = d.get("data", {}).get("record_id_list", []) or []
    if not ids:
        return True
    for i in range(0, len(ids), 100):
        args = ["base", "+record-delete", "--base-token", base_token,
                "--table-id", table]
        for rid in ids[i:i+100]:
            args += ["--record-id", rid]
        args += ["--yes", "--as", "user", "--format", "json"]
        r = _lark(args)
        if not r.get("ok"):
            print(f"[run] 清空 {table} 失败: {r}", file=sys.stderr)
            return False
    return True


EVERGREEN_TABLE = "长期热点库"  # 老/常青热点单独沉淀，追加去重，不清空


def _batch_create(base_token, table, rows):
    batch = [[r[k] for k in FIELDS] for r in rows]
    for i in range(0, len(batch), 200):
        chunk = {"fields": FIELDS, "rows": batch[i:i+200]}
        d = _lark(["base", "+record-batch-create", "--base-token", base_token,
                   "--table-id", table, "--as", "user",
                   "--json", json.dumps(chunk, ensure_ascii=False)])
        if not d.get("ok"):
            print(f"[run] 写入 {table} 失败: {d}", file=sys.stderr)
            return False
        time.sleep(0.3)
    return True


def write_daily(rows, cfg):
    """当天日期表：清空重写，当天反复跑不重复。"""
    base_token = cfg["feishu"]["base_token"]
    table = TODAY
    if not ensure_table(base_token, table):
        return False
    if not clear_table(base_token, table):
        return False
    return _batch_create(base_token, table, rows)


def _existing_topics(base_token, table):
    """读长期库已有话题（归一化），用于追加去重。翻页读全。"""
    have = set()
    offset = 0
    while True:
        d = _lark(["base", "+record-list", "--base-token", base_token, "--table-id", table,
                   "--field-id", "话题", "--limit", "200", "--offset", str(offset),
                   "--as", "user", "--json"])
        data = d.get("data", {})
        rows = data.get("data", []) or []
        for r in rows:
            v = r[0] if isinstance(r, list) and r else r
            if isinstance(v, list):
                v = v[0] if v else ""
            have.add(norm(v))
        if not data.get("has_more") or not rows:
            break
        offset += len(rows)
    return have


def write_evergreen(rows, cfg):
    """长期热点库：只追加不在库里的新话题，不清空。返回 (ok, 新增数)。"""
    base_token = cfg["feishu"]["base_token"]
    table = EVERGREEN_TABLE
    if not ensure_table(base_token, table):
        return False, 0
    have = _existing_topics(base_token, table)
    new = [r for r in rows if norm(r["话题"]) not in have]
    if not new:
        return True, 0
    if not _batch_create(base_token, table, new):
        return False, 0
    return True, len(new)


def main():
    dry = "--dry-run" in sys.argv
    cfg = load_config()
    keywords = load_keywords()

    heat_rank = {"高": 0, "中": 1, "低": 2}
    plat_rank = {"全网": 0, "微博": 1, "知乎": 2, "虎扑": 3, "小红书": 4, "抖音": 5, "B站": 6}

    # 当天看板：只放能确认时效的（全网新闻带日期 + 微博带日期 + B站近120天带发布日期）。
    # 抖音/小红书是 WebSearch 索引的存量内容、无法判定时效，只进长期库当人工搜索池，不污染当天表。
    pool = load_evergreen_tier()
    search_rows = load_search_tier()
    fresh_bili, ever_bili = collect_bilibili(keywords)
    daily_rows = dedup(search_rows + fresh_bili)
    daily_rows.sort(key=lambda r: (heat_rank.get(r["热度"], 9), plat_rank.get(r["平台"], 9)))

    # 长期库：B站历史高播放 + 抖音/小红书公开话题池（追加去重、不清空）
    evergreen_rows = dedup(ever_bili + pool)
    evergreen_rows.sort(key=lambda r: (heat_rank.get(r["热度"], 9), plat_rank.get(r["平台"], 9)))

    md_path, csv_path = write_local(daily_rows)
    from collections import Counter
    pc = Counter(r["平台"] for r in daily_rows)
    plat_brief = " ".join(f"{k}{v}" for k, v in sorted(pc.items(), key=lambda x: plat_rank.get(x[0], 9)))
    print(f"【当天看板】{len(daily_rows)} 条（{plat_brief}）")
    print(f"【常青层】{len(evergreen_rows)} 条（→「{EVERGREEN_TABLE}」）")
    print(f"本地已写：{md_path}")
    print(f"          {csv_path}")

    if dry:
        print(f"\n--dry-run：未写飞书。")
        print(f"当天看板预览（→「{TODAY}」表）：")
        for r in daily_rows[:10]:
            print(f"  [{r['热度']}][{r['平台']}][{r['类别']}] {r['话题'][:32]} | {r['热度数据']}")
        return

    ok = write_daily(daily_rows, cfg)
    ev_ok, ev_new = write_evergreen(evergreen_rows, cfg)
    base_url = cfg["feishu"]["base_url"]
    if ok:
        print(f"\n已写飞书「{TODAY}」表（新鲜 {len(daily_rows)} 条）：{base_url}")
    else:
        print("\n新鲜层写入未完成，见上方错误。本地文件已保存。")
    if ev_ok:
        print(f"已写飞书「{EVERGREEN_TABLE}」表（本次新增 {ev_new} 条）")
    else:
        print("长期库写入未完成，见上方错误。")


if __name__ == "__main__":
    main()
