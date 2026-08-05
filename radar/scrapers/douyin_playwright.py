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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROFILE_DIR = os.path.join(ROOT, "scrapers", ".douyin_profile")
OUT_FILE = os.path.join(ROOT, "output", "evergreen_input.json")
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


def _extract_videos(page):
    """从当前搜索页 DOM 抓 /video/ 卡片：返回 [(url, title)]。"""
    js = """
    () => {
      const seen = {};
      document.querySelectorAll('a[href*="/video/"]').forEach(a => {
        const m = a.href.match(/\\/video\\/(\\d+)/);
        if (!m) return;
        const id = m[1];
        // 标题优先级：aria-label > 内部img的alt > 链接文本
        let t = a.getAttribute('aria-label') || '';
        if (!t) { const img = a.querySelector('img[alt]'); if (img) t = img.alt; }
        if (!t) t = (a.innerText || '').trim();
        t = (t || '').replace(/\\s+/g, ' ').trim();
        if (!seen[id] || (t && t.length > (seen[id]||'').length)) seen[id] = t;
      });
      return Object.keys(seen).map(id => [id, seen[id]]);
    }
    """
    rows = []
    for vid, title in page.evaluate(js):
        url = f"https://www.douyin.com/video/{vid}"
        rows.append((url, title))
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
    """打开一个关键词的搜索页，滚动加载，返回 [(url,title)]。"""
    url = "https://www.douyin.com/search/" + kw + "?type=video"
    page.goto(url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3500)
    for _ in range(SCROLLS):
        page.mouse.wheel(0, 2400)
        page.wait_for_timeout(1500)
    return _extract_videos(page)


def cmd_search(keywords):
    from playwright.sync_api import sync_playwright
    if not os.path.isdir(PROFILE_DIR):
        print("[search] 还没登录。先跑：.venv/bin/python scrapers/douyin_playwright.py login")
        sys.exit(1)
    collected = {}  # url -> title
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
                # 抓到 0 条多半是撞验证码：轮询最多 60s 等你在窗口手动滑动验证，
                # 一旦检测到视频卡片出现（说明滑过了）就重新滚动抓取
                if not rows and _has_captcha(page):
                    print(f"[search] 「{kw}」撞验证码 → 请在弹出的窗口里手动滑动验证，最多等 60s…")
                    solved = False
                    for _ in range(20):  # 20 × 3s = 60s
                        time.sleep(3)
                        if page.query_selector('a[href*="/video/"]'):
                            solved = True
                            break
                    if solved:
                        page.wait_for_timeout(2000)
                        for _ in range(SCROLLS):
                            page.mouse.wheel(0, 2400)
                            page.wait_for_timeout(1500)
                        rows = _extract_videos(page)
                        print(f"[search] 「{kw}」验证通过，继续抓取。")
                    else:
                        print(f"[search] 「{kw}」60s 内未解除验证，跳过。")
            except Exception as e:
                print(f"[search] 「{kw}」失败：{e}")
            hit = 0
            for u, t in rows:
                if u in collected:
                    continue
                if t and not is_sg(t):
                    continue  # 有标题但不含新加坡锚点 → 跳过；无标题的保留待人工核实
                collected[u] = t
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


def _merge_output(collected):
    """把新抓到的视频去重合并进 evergreen_input.json（按URL和标题双重去重）。"""
    existing = []
    if os.path.exists(OUT_FILE):
        with open(OUT_FILE, encoding="utf-8") as f:
            existing = json.load(f)
    have_url = {r.get("出处链接") for r in existing}
    have_title = {_norm(r.get("话题")) for r in existing}
    added = 0
    for url, title in collected.items():
        disp = title or "（抖音视频·标题待人工核实）"
        if url in have_url or (_norm(disp) in have_title and title):
            continue
        existing.append({
            "话题": disp,
            "类别": classify(disp),
            "平台": "抖音",
            "热度": "中",
            "热度数据": "扫码登录抓取·公开视频·需人工核实",
            "出处链接": url,
        })
        have_url.add(url)
        have_title.add(_norm(disp))
        added += 1
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)
    print(f"[search] 新增 {added} 条 → {OUT_FILE}（去重后共 {len(existing)} 条）")
    print("[search] 下一步跑 python3 run.py，把它们写进飞书长期热点库。")


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
