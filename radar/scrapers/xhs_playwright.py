#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小红书采集器 · 真实浏览器 + 扫码登录

小红书站内笔记对搜索引擎屏蔽，必须走 Playwright + 小号登录才能抓到原生笔记。

两个模式：
  login   打开真实 Chromium，你用小红书App扫码登录一次；登录态存本地
          scrapers/.xhs_profile/，之后自动复用，不用反复扫。
  search  用已登录的浏览器搜关键词，抓「笔记标题 + 真实URL + 点赞/收藏/评论」，
          SG锚点过滤、去重后补进 output/evergreen_input.json（→ run.py 读进飞书）。

用法（必须用项目 venv 的 python）：
  .venv/bin/python scrapers/xhs_playwright.py login
  .venv/bin/python scrapers/xhs_playwright.py search              # 用 keywords.txt
  .venv/bin/python scrapers/xhs_playwright.py search 新加坡留学 陪读妈妈

安全红线：
  - .xhs_profile/ 含登录cookie，只存本地，切勿提交/外传。
  - 只用小号，禁用主号（自动化有封号风险）。
  - 每个关键词之间停顿，慢跑更像人、更安全。
"""
import os
import sys
import json
import time
import random
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DIR = os.path.join(ROOT, "scrapers", ".xhs_profile")
OUT_FILE = os.path.join(ROOT, "output", "evergreen_input.json")
SEEN_FILE = os.path.join(ROOT, "output", "seen_urls.json")
KEYWORDS_FILE = os.path.join(ROOT, "keywords.txt")

PER_KEYWORD = 12
THROTTLE_MIN = 12.0
THROTTLE_MAX = 20.0
SCROLLS = 3

SG_ANCHORS = [
    "新加坡", "狮城", "南洋", "南大", "国大", "国立大学", "nus", "ntu",
    "aeis", "psle", "陪读", "政府中学", "直通", "初中毕业", "sm1", "jcu",
    "kaplan", "楷博", "psb", "ip直通", "莱佛士", "华中",
    "坡县", "坡坡", "sg留学",
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


def _parse_abbrev_num(s):
    s = (s or "").strip().lower().replace(",", "")
    if not s:
        return 0
    if s.endswith("w") or s.endswith("万"):
        return int(float(s.rstrip("w万")) * 10000)
    try:
        return int(float(s))
    except ValueError:
        return 0


def heat_xhs(likes, collects):
    total = int(likes or 0) + int(collects or 0)
    if total >= 5000:
        return "高"
    if total >= 1000:
        return "中"
    return "低"


def _new_context(p, headless):
    return p.chromium.launch_persistent_context(
        PROFILE_DIR,
        headless=headless,
        channel="chrome",
        viewport={"width": 1280, "height": 900},
        args=["--disable-blink-features=AutomationControlled"],
    )


def _logged_in(ctx):
    for c in ctx.cookies():
        if c.get("name", "") in ("customer-sso-sid", "xsecappid") and c.get("value"):
            return True
    return False


def cmd_login():
    from playwright.sync_api import sync_playwright
    os.makedirs(PROFILE_DIR, exist_ok=True)
    with sync_playwright() as p:
        ctx = _new_context(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.xiaohongshu.com/", wait_until="domcontentloaded")
        if _logged_in(ctx):
            print("[login] 已是登录态。窗口保持打开，你可以手动退出登录再扫新号。")
            print("[login] 换号完成后请手动关闭浏览器窗口，登录态会自动保存。")
            try:
                page.wait_for_event("close", timeout=0)
            except Exception:
                pass
            time.sleep(1.5)
            ctx.close()
            print("[login] 窗口已关闭，登录态已保存。")
            return
        print("=" * 56)
        print("[login] 浏览器已弹出。请在窗口里点『登录』，用小红书App扫码。")
        print("[login] 登录成功后窗口保持打开，你可以继续操作。")
        print("[login] 操作完成后请手动关闭浏览器窗口，登录态会自动保存。")
        print("=" * 56)
        logged = False
        while True:
            try:
                page.wait_for_event("close", timeout=2000)
                break
            except Exception:
                pass
            if not logged and _logged_in(ctx):
                logged = True
                print("[login] 检测到登录成功！窗口保持打开，你可以继续操作或手动关闭。")
        time.sleep(1.5)
        ctx.close()
        if logged:
            print("[login] 窗口已关闭，新登录态已保存到 .xhs_profile/。")
        else:
            print("[login] 窗口已关闭。如果已登录则登录态已保存。")


def _extract_notes(page):
    """从搜索结果页 DOM 抓笔记卡片：返回 [{url, title, author, likes, collects}]."""
    js = """
    () => {
      const results = [];
      const seen = new Set();
      const links = document.querySelectorAll('a[href*="/explore/"], a[href*="/discovery/item/"]');
      links.forEach(a => {
        const href = a.href || '';
        if (!href || seen.has(href)) return;
        seen.add(href);
        // 往上找卡片容器（SECTION 或有多个子元素的祖先）
        let card = a;
        for (let i = 0; i < 8; i++) {
          if (!card.parentElement) break;
          card = card.parentElement;
          if (card.tagName === 'SECTION' || card.children.length >= 4) break;
        }
        // 从 card.innerText 解析：格式是 "标题\\n作者\\n日期\\n数字"
        const fullText = (card.innerText || '').trim();
        if (!fullText) return;
        const lines = fullText.split('\\n').map(s => s.trim()).filter(Boolean);
        if (lines.length < 2) return;
        // 最后一行是数字（点赞数），倒数第二行是日期，倒数第三行是作者，其余是标题
        let likes = 0;
        const lastLine = lines[lines.length - 1];
        if (/^\\d+(\\.\\d+)?[万w]?$/.test(lastLine)) {
          const n = parseFloat(lastLine.replace(/[万w]/g, ''));
          likes = Math.round(lastLine.includes('万') || lastLine.includes('w') ? n * 10000 : n);
        }
        let author = '';
        if (lines.length >= 3) {
          author = lines[lines.length - 3];
        }
        // 标题是第一行（去掉可能的前缀emoji/标签）
        let title = lines[0];
        if (!title || title.length < 2) return;
        results.push({url: href, title: title, author: author, likes: likes, collects: likes});
      });
      return results;
    }
    """
    return page.evaluate(js)


def _has_captcha(page):
    try:
        txt = (page.inner_text("body") or "")[:800]
        return any(k in txt for k in ("验证", "captcha", "滑动", "拖动", "安全验证"))
    except Exception:
        return False


def _load_and_extract(page, kw):
    print(f"[search] 正在搜索「{kw}」...")
    # 先到主页（直接跳搜索页会被小红书拦截）
    if "xiaohongshu.com" not in (page.url or ""):
        page.goto("https://www.xiaohongshu.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)
    # 用搜索框输入关键词
    try:
        inp = page.wait_for_selector('#search-input, input[placeholder*="搜索"], input[name="searchValue"], input.search-input', timeout=8000)
        inp.click()
        page.wait_for_timeout(500)
        inp.fill("")
        page.wait_for_timeout(300)
        inp.type(kw, delay=80)
        page.wait_for_timeout(500)
        page.keyboard.press("Enter")
        print(f"[search] 已输入关键词并搜索")
    except Exception as e:
        print(f"[search] 搜索框操作失败: {e}")
        # fallback: 直接跳转
        url = "https://www.xiaohongshu.com/search_result?keyword=" + kw + "&source=web_search_result_notes"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception:
            pass
    page.wait_for_timeout(5000)
    try:
        page.wait_for_selector('a[href*="/explore/"], a[href*="/discovery/item/"]', timeout=15000)
        links = page.query_selector_all('a[href*="/explore/"], a[href*="/discovery/item/"]')
        print(f"[search] 找到 {len(links)} 个笔记链接")
    except Exception:
        links = page.query_selector_all('a[href*="/explore/"], a[href*="/discovery/item/"]')
        print(f"[search] 等待超时，当前链接数: {len(links)}")
    for _ in range(SCROLLS):
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(1200 + random.randint(300, 1000))
    return _extract_notes(page)


def cmd_search(keywords):
    from playwright.sync_api import sync_playwright
    if not os.path.isdir(PROFILE_DIR):
        print("[search] 还没登录。先跑：.venv/bin/python scrapers/xhs_playwright.py login")
        sys.exit(1)
    collected = {}
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
                    print(f"[search] 「{kw}」撞验证码 → 请在窗口里手动完成验证，完成后会自动继续…")
                    while True:
                        time.sleep(3)
                        links = page.query_selector_all('a[href*="/explore/"], a[href*="/discovery/item/"]')
                        if links:
                            break
                        if not _has_captcha(page):
                            break
                    page.wait_for_timeout(2000)
                    for _ in range(SCROLLS):
                        page.mouse.wheel(0, 2400)
                        page.wait_for_timeout(1200 + random.randint(300, 1000))
                    rows = _extract_notes(page)
                    print(f"[search] 「{kw}」验证通过，继续抓取。")
            except Exception as e:
                print(f"[search] 「{kw}」失败：{e}")
            hit = 0
            for item in rows:
                url = item["url"]
                if url in collected:
                    continue
                item = _clean_note(item)
                title = item.get("title", "")
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


def _fmt_num(n):
    n = int(n or 0)
    if n >= 10000:
        return f"{n/10000:.1f}万"
    return str(n) if n else ""


def _clean_note(item):
    """清洗单条笔记：标题去掉尾部作者/日期/数字，作者只取第一行。"""
    title = (item.get("title") or "").strip()
    author = (item.get("author") or "").strip()
    # 标题可能是 "真实标题\n作者名\n日期\n数字"，只取第一行
    parts = title.split("\n")
    title = parts[0].strip()
    # 如果标题太短，可能第一行不是标题，取最长的一行
    if len(title) < 3 and len(parts) > 1:
        title = max(parts, key=len).strip()
    # 作者只取第一行（去掉日期等）
    if author:
        author = author.split("\n")[0].strip()
    item["title"] = title
    item["author"] = author
    return item


def _load_seen_urls():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def _save_seen_urls(urls):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(urls), f, ensure_ascii=False)


def _merge_output(collected):
    existing = []
    if os.path.exists(OUT_FILE):
        with open(OUT_FILE, encoding="utf-8") as f:
            existing = json.load(f)
    have_url = {r.get("出处链接") for r in existing}
    have_title = {_norm(r.get("话题")) for r in existing}
    seen_urls = _load_seen_urls()
    added = 0
    for url, item in collected.items():
        title = item.get("title", "") or "（小红书笔记·标题待人工核实）"
        if url in have_url or url in seen_urls or (_norm(title) in have_title and item.get("title")):
            continue
        likes = item.get("likes", 0)
        collects = item.get("collects", 0)
        author = item.get("author", "")
        parts = []
        if likes:
            parts.append(f"点赞{_fmt_num(likes)}")
        if collects:
            parts.append(f"收藏{_fmt_num(collects)}")
        if author:
            parts.append(author)
        heat_data = "·".join(parts) if parts else "Playwright抓取·需人工核实"
        existing.append({
            "话题": title,
            "类别": classify(title),
            "平台": "小红书",
            "热度": heat_xhs(likes, collects),
            "热度数据": heat_data,
            "出处链接": url,
            "点赞数": likes,
            "收藏数": collects,
            "作者": author,
            "采集日期": date.today().isoformat(),
        })
        have_url.add(url)
        have_title.add(_norm(title))
        seen_urls.add(url)
        added += 1
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    _save_seen_urls(seen_urls)
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
