#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音采集器 · 真实浏览器 + 扫码登录

为什么用真实浏览器：抖音搜索接口带 a_bogus/msToken 签名，纯代码抓不到；
真实 Chromium 自己渲染页面、自己算签名，我们只读渲染后的 DOM，绕开签名死结。

两个模式：
  login   打开真实 Chromium，你用抖音App扫码登录一次；登录态存本地
          scrapers/.douyin_profile/，之后自动复用，不用反复扫。
  search  用已登录的浏览器搜关键词，抓「视频标题 + 真实URL」，SG锚点过滤、
          去重后补进 output/evergreen_input.json（→ run.py 读进长期热点库）。

用法（必须用项目 venv 的 python）：
  .venv/bin/python scrapers/douyin_playwright.py login
  .venv/bin/python scrapers/douyin_playwright.py search                # 用 keywords.txt
  .venv/bin/python scrapers/douyin_playwright.py search 新加坡留学 陪读妈妈

安全红线：
  - .douyin_profile/ 含登录cookie，只存本地，切勿提交/外传。
  - 只用小号，禁用主号（自动化有封号风险）。
  - 每个关键词之间停顿，慢跑更像人、更安全。
"""
import os
import re
import sys
import json
import time
import random
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DIR = os.path.join(ROOT, "scrapers", ".douyin_profile")
OUT_FILE = os.path.join(ROOT, "output", "evergreen_input.json")
SEEN_FILE = os.path.join(ROOT, "output", "seen_urls.json")
KEYWORDS_FILE = os.path.join(ROOT, "keywords.txt")

# 搜索结果每关键词最多取几条
PER_KEYWORD = 12
# 关键词间基础停顿 + 随机抖动（秒）：连搜太快会触发抖音验证码中间页，慢跑更稳
THROTTLE_MIN = 8.0
THROTTLE_MAX = 14.0
# 每个搜索页向下滚动几次，加载更多卡片
SCROLLS = 4

SG_ANCHORS = [
    "新加坡", "狮城", "南洋", "南大", "国大", "国立大学", "nus", "ntu",
    "aeis", "psle", "陪读", "政府中学", "直通", "初中毕业", "sm1", "jcu",
    "kaplan", "楷博", "psb", "ip直通", "莱佛士", "华中",
]


def is_sg(title):
    t = (title or "").lower()
    return any(a in t for a in SG_ANCHORS)


def classify(title):
    t = title or ""
    if any(k in t for k in ["陪读", "低龄", "初中", "初三", "小学", "中学", "AEIS", "插班", "国际学校"]):
        return "低龄留学"
    if any(k in t for k in ["费用", "学费", "多少钱", "签证", "准证", "花费", "成本", "卖房", "万"]):
        return "费用签证"
    if any(k in t for k in ["政策", "新规", "门槛", "PSB", "认证", "收紧"]):
        return "留学政策"
    return "综合话题"


def load_keywords():
    if not os.path.exists(KEYWORDS_FILE):
        return ["新加坡留学"]
    with open(KEYWORDS_FILE, encoding="utf-8") as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]


def _new_context(p, headless):
    """打开带本地登录态的持久化浏览器上下文（真实 Chromium 窗口）。
    用系统已装的 Google Chrome（channel='chrome'）：免下载、指纹更像真人。"""
    return p.chromium.launch_persistent_context(
        PROFILE_DIR,
        headless=headless,
        channel="chrome",
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )


def _logged_in(ctx):
    """抖音登录后会写 sessionid cookie，以此判断是否已登录。"""
    for c in ctx.cookies():
        if c.get("name", "").startswith("sessionid") and c.get("value"):
            return True
    return False


def cmd_login():
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with sync_playwright() as p:
        ctx = _new_context(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.douyin.com/", wait_until="domcontentloaded")
        if _logged_in(ctx):
            print("[login] 已是登录态，无需重复扫码。")
            ctx.close()
            return
        print("=" * 56)
        print("[login] 浏览器已弹出。请在窗口里点『登录』，用抖音App扫码。")
        print("[login] 登录成功后本脚本会自动识别并保存，最多等 180 秒…")
        print("=" * 56)
        deadline = time.time() + 180
        while time.time() < deadline:
            if _logged_in(ctx):
                print("[login] 登录成功，登录态已保存到 .douyin_profile/，以后免扫码。")
                time.sleep(1.5)  # 让 storage 落盘
                ctx.close()
                return
            time.sleep(2)
        print("[login] 超时未检测到登录。请重试 login。")
        ctx.close()


def _parse_abbrev_num(s):
    """"1.2w" -> 12000, "3456" -> 3456, "" -> 0."""
    s = (s or "").strip().lower().replace(",", "")
    if not s:
        return 0
    if s.endswith("w") or s.endswith("万"):
        return int(float(s.rstrip("w万")) * 10000)
    try:
        return int(float(s))
    except ValueError:
        return 0


def heat_douyin(likes):
    likes = int(likes or 0)
    if likes >= 100000:
        return "高"
    if likes >= 30000:
        return "中"
    return "低"


def _extract_videos(page):
    """从当前搜索页 DOM 抓 /video/ 卡片：返回 [{url, title, author, likes, comments, collects, shares}]."""
    js = """
    () => {
      const results = {};
      document.querySelectorAll('a[href*="/video/"]').forEach(a => {
        const m = a.href.match(/\\/video\\/(\\d+)/);
        if (!m) return;
        const id = m[1];
        if (results[id]) return;
        const text = (a.innerText || '').trim();
        const lines = text.split('\\n').map(s => s.trim()).filter(Boolean);
        if (lines.length < 2) return;
        // 第一行: 时长(00:27), 第二行: 点赞数(28.4万), 第三行: 标题, @开头的行: 作者
        let duration = lines[0];
        let likes = 0;
        if (lines.length >= 2) {
          const likeStr = lines[1];
          if (/^\\d+(\\.\\d+)?[万w]?$/.test(likeStr)) {
            const n = parseFloat(likeStr.replace(/[万w]/g, ''));
            likes = Math.round((likeStr.includes('万') || likeStr.includes('w') ? n * 10000 : n));
          }
        }
        let title = lines.length >= 3 ? lines[2] : lines[1];
        let author = '';
        for (const line of lines) {
          if (line.startsWith('@')) {
            author = line.substring(1).trim();
            break;
          }
        }
        results[id] = {title, author, likes, duration};
      });
      return Object.entries(results).map(([id, d]) => [id, d.title, d.author, d.likes, d.duration]);
    }
    """
    rows = []
    for item in page.evaluate(js):
        vid, title, author, likes, duration = item
        url = f"https://www.douyin.com/video/{vid}"
        rows.append({"url": url, "title": title, "author": author or "", "likes": int(likes or 0)})
    return rows


def _has_captcha(page):
    """搜索页被验证码中间页拦截时，页面几乎没有 /video/ 链接、且含验证字样。"""
    try:
        if page.query_selector('a[href*="/video/"]'):
            return False
        txt = (page.inner_text("body") or "")[:800]
        return any(k in txt for k in ("验证", "captcha", "滑动", "拖动"))
    except Exception:
        return False


def _load_and_extract(page, kw):
    """打开一个关键词的搜索页，按点赞排序，滚动加载，返回 [{url,title,author,likes}]."""
    url = "https://www.douyin.com/search/" + kw + "?type=video&sort_type=1"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_selector('a[href*="/video/"]', timeout=10000)
    for _ in range(SCROLLS):
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(800 + random.randint(200, 800))
    return _extract_videos(page)


def cmd_search(keywords):
    from playwright.sync_api import sync_playwright
    if not os.path.isdir(PROFILE_DIR):
        print("[search] 还没登录。先跑：.venv/bin/python scrapers/douyin_playwright.py login")
        sys.exit(1)
    collected = {}  # url -> {title, author, likes}
    with sync_playwright() as p:
        ctx = _new_context(p, headless=False)
        if not _logged_in(ctx):
            print("[search] 登录态失效，请重新 login。")
            ctx.close()
            sys.exit(1)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        for idx, kw in enumerate(keywords):
            rows = []
            try:
                rows = _load_and_extract(page, kw)
                if not rows and _has_captcha(page):
                    print(f"[search] 「{kw}」撞验证码 → 请在弹出的窗口里手动滑动验证，最多等 60s…")
                    solved = False
                    for _ in range(20):
                        time.sleep(3)
                        if page.query_selector('a[href*="/video/"]'):
                            solved = True
                            break
                    if solved:
                        page.wait_for_timeout(2000)
                        for _ in range(SCROLLS):
                            page.mouse.wheel(0, 2400)
                            page.wait_for_timeout(800 + random.randint(200, 800))
                        rows = _extract_videos(page)
                        print(f"[search] 「{kw}」验证通过，继续抓取。")
                    else:
                        print(f"[search] 「{kw}」60s 内未解除验证，跳过。")
            except Exception as e:
                print(f"[search] 「{kw}」失败：{e}")
            hit = 0
            for item in rows:
                url = item["url"]
                title = item["title"]
                if url in collected:
                    continue
                if title and not is_sg(title):
                    continue
                collected[url] = item
                hit += 1
                if hit >= PER_KEYWORD:
                    break
            print(f"[search] 「{kw}」抓到 {hit} 条")
            if idx < len(keywords) - 1:
                time.sleep(random.uniform(THROTTLE_MIN, THROTTLE_MAX))
        ctx.close()
    _merge_output(collected)


def _norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


def _fmt_likes(n):
    n = int(n or 0)
    if n >= 10000:
        return f"{n/10000:.1f}万"
    return str(n) if n else ""


def _merge_output(collected):
    """把新抓到的视频去重合并进 evergreen_input.json（按URL和标题双重去重）。"""
    existing = []
    if os.path.exists(OUT_FILE):
        with open(OUT_FILE, encoding="utf-8") as f:
            existing = json.load(f)
    have_url = {r.get("出处链接") for r in existing}
    have_title = {_norm(r.get("话题")) for r in existing}
    seen_urls = set()
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, encoding="utf-8") as f:
            seen_urls = set(json.load(f))
    added = 0
    for url, item in collected.items():
        title = item.get("title", "") or "（抖音视频·标题待人工核实）"
        if url in have_url or url in seen_urls or (_norm(title) in have_title and item.get("title")):
            continue
        likes = item.get("likes", 0)
        author = item.get("author", "")
        likes_str = _fmt_likes(likes)
        heat_data_parts = []
        if likes_str:
            heat_data_parts.append(f"点赞{likes_str}")
        if author:
            heat_data_parts.append(author)
        heat_data = "·".join(heat_data_parts) if heat_data_parts else "扫码登录抓取·公开视频"
        existing.append({
            "话题": title,
            "类别": classify(title),
            "平台": "抖音",
            "热度": heat_douyin(likes),
            "热度数据": heat_data,
            "出处链接": url,
            "点赞数": likes,
            "作者": author,
            "采集日期": date.today().isoformat(),
        })
        have_url.add(url)
        have_title.add(_norm(title))
        seen_urls.add(url)
        added += 1
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen_urls), f, ensure_ascii=False)
    print(f"[search] 新增 {added} 条 → {OUT_FILE}（去重后共 {len(existing)} 条）")
    print("[search] 下一步跑 python3 run.py，把它们写进飞书。")


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in ("login", "search"):
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "login":
        cmd_login()
    else:
        kws = sys.argv[2:] or load_keywords()
        cmd_search(kws)


if __name__ == "__main__":
    main()
