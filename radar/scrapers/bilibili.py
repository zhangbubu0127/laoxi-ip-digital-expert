#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
B站采集：按关键词搜索视频，按播放量排序，返回话题（标题+播放量+UP+链接）。
纯 urllib，无第三方依赖。wbi 签名 + buvid cookie。
只输出真实抓到的数据，抓不到就报错返回空，绝不编造。
"""
import sys
import json
import time
import hashlib
import urllib.request
import urllib.parse
from functools import reduce
from html import unescape
import re

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

# wbi 混淆密钥重排表（B站固定）
MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
    27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
    37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
    22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
]


def _http_get(url, cookies=None, timeout=15):
    req = urllib.request.Request(url)
    req.add_header("User-Agent", UA)
    req.add_header("Referer", "https://www.bilibili.com/")
    req.add_header("Accept", "application/json, text/plain, */*")
    if cookies:
        req.add_header("Cookie", "; ".join(f"{k}={v}" for k, v in cookies.items()))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def get_buvid():
    """获取 buvid3/buvid4 cookie，搜索接口需要。"""
    try:
        raw = _http_get("https://api.bilibili.com/x/frontend/finger/spi")
        d = json.loads(raw)
        data = d.get("data", {})
        return {"buvid3": data.get("b_3", ""), "buvid4": data.get("b_4", "")}
    except Exception as e:
        print(f"[bilibili] 获取 buvid 失败: {e}", file=sys.stderr)
        return {}


def get_wbi_keys(cookies):
    """从 nav 接口拿 img_key/sub_key。"""
    raw = _http_get("https://api.bilibili.com/x/web-interface/nav", cookies=cookies)
    d = json.loads(raw)
    wbi = d.get("data", {}).get("wbi_img", {})
    img_url = wbi.get("img_url", "")
    sub_url = wbi.get("sub_url", "")
    img_key = img_url.rsplit("/", 1)[-1].split(".")[0]
    sub_key = sub_url.rsplit("/", 1)[-1].split(".")[0]
    if not img_key or not sub_key:
        raise RuntimeError("未能获取 wbi keys")
    return img_key, sub_key


def get_mixin_key(orig):
    return reduce(lambda s, i: s + orig[i], MIXIN_KEY_ENC_TAB, "")[:32]


def enc_wbi(params, img_key, sub_key):
    mixin_key = get_mixin_key(img_key + sub_key)
    params["wts"] = int(time.time())
    params = dict(sorted(params.items()))
    # 过滤 value 中的 !'()* 字符
    params = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in params.items()}
    query = urllib.parse.urlencode(params)
    wbi_sign = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = wbi_sign
    return params


def clean_title(t):
    t = re.sub(r"<[^>]+>", "", t or "")
    return unescape(t).strip()


def search_keyword(keyword, img_key, sub_key, cookies, order="click", page=1, page_size=10):
    """order: click=播放量 / pubdate=最新 / dm=弹幕 / stow=收藏"""
    params = {
        "search_type": "video",
        "keyword": keyword,
        "order": order,
        "page": page,
        "page_size": page_size,
    }
    signed = enc_wbi(dict(params), img_key, sub_key)
    url = "https://api.bilibili.com/x/web-interface/wbi/search/type?" + urllib.parse.urlencode(signed)
    raw = _http_get(url, cookies=cookies)
    d = json.loads(raw)
    if d.get("code") != 0:
        print(f"[bilibili] 搜索 '{keyword}' 返回 code={d.get('code')} msg={d.get('message')}", file=sys.stderr)
        return []
    results = d.get("data", {}).get("result", []) or []
    out = []
    for r in results:
        if r.get("result_type") and r.get("result_type") != "video":
            continue
        title = clean_title(r.get("title", ""))
        if not title:
            continue
        out.append({
            "title": title,
            "play": r.get("play", 0),
            "danmaku": r.get("danmaku", 0),
            "author": r.get("author", ""),
            "bvid": r.get("bvid", ""),
            "url": "https://www.bilibili.com/video/" + r.get("bvid", "") if r.get("bvid") else (r.get("arcurl", "")),
            "pubdate": r.get("pubdate", 0),
        })
    return out


def collect(keywords, per_keyword=8, order="click", sleep=0.3):
    """对多个关键词搜索，合并去重（按 bvid），返回按播放量降序。"""
    cookies = get_buvid()
    if not cookies.get("buvid3"):
        print("[bilibili] 警告：无 buvid3，接口可能被拦截", file=sys.stderr)
    img_key, sub_key = get_wbi_keys(cookies)
    seen = set()
    merged = []
    for kw in keywords:
        try:
            items = search_keyword(kw, img_key, sub_key, cookies, order=order, page_size=per_keyword)
        except Exception as e:
            print(f"[bilibili] 关键词 '{kw}' 失败: {e}", file=sys.stderr)
            items = []
        for it in items:
            key = it.get("bvid") or it.get("title")
            if key in seen:
                continue
            seen.add(key)
            it["keyword"] = kw
            merged.append(it)
        time.sleep(sleep)
    merged.sort(key=lambda x: x.get("play", 0), reverse=True)
    return merged


def main():
    kw_file = sys.argv[1] if len(sys.argv) > 1 else "keywords.txt"
    try:
        with open(kw_file, encoding="utf-8") as f:
            keywords = [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    except FileNotFoundError:
        keywords = ["新加坡留学", "新加坡低龄留学"]
    data = collect(keywords)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
