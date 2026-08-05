#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把新加坡热点雷达（radar/）当日产出同步进席小题情报库 knowledge/竞对分析/。

用法：
  python3 scripts/sync_radar.py --dry-run    只打印不写
  python3 scripts/sync_radar.py              写 热点话题.md（+ 中新教育对比.md 种子若缺）
  python3 scripts/sync_radar.py --radar-output <目录>  覆盖雷达输出目录
"""
import glob
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_RADAR_OUT = os.path.join(ROOT, "radar", "output")
INTEL_DIR = os.path.join(ROOT, "knowledge", "竞对分析")
HOT_FILE = os.path.join(INTEL_DIR, "热点话题.md")
COMPARE_FILE = os.path.join(INTEL_DIR, "中新教育对比.md")


def latest_radar(radar_out: str) -> str | None:
    files = glob.glob(os.path.join(radar_out, "radar_*.md"))
    if not files:
        return None
    return sorted(files)[-1]


def build_hot_markdown(radar_md_path: str, today: str) -> str | None:
    try:
        with open(radar_md_path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError as e:
        print(f"[sync] 读雷达失败 {radar_md_path}: {e}", file=sys.stderr)
        return None
    date = os.path.basename(radar_md_path).replace("radar_", "").replace(".md", "")
    # 只保留表格（表头行起）
    start = next((i for i, ln in enumerate(lines) if ln.startswith("| 热度")), None)
    if start is None:
        print(f"[sync] 雷达文件无表格结构，跳过 {radar_md_path}", file=sys.stderr)
        return None
    table = lines[start:]
    return ("# 新加坡留学热点雷达\n"
            f"> 来源：radar/output/{os.path.basename(radar_md_path)} · 同步于 {today}\n"
            "> 用途：席小题出选题的市场热点原料；机器覆盖更新，勿手改\n\n"
            f"## 当日热点（{date}）\n\n"
            + "\n".join(table) + "\n")


def seed_compare_from_json(radar_out: str, today: str) -> str | None:
    path = os.path.join(radar_out, "edu_compare.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
    except Exception as e:
        print(f"[sync] edu_compare.json 读取失败: {e}", file=sys.stderr)
        return None
    if not isinstance(rows, list) or not rows:
        return None
    lines = ["# 中新教育对比（差异化卖点素材）",
             f"> 来源：radar/output/edu_compare.json · 同步于 {today}（原始数据，可人工润色）",
             "> 用途：席小题出选题的「反常识」原料", ""]
    for r in rows:
        lines.append(f"## {r.get('主题', '')}")
        lines.append(f"- 类别：{r.get('类别', '')}")
        lines.append(f"- 新加坡怎么做：{r.get('新加坡怎么做', '')}")
        lines.append(f"- 对比国内：{r.get('对比国内', '')}")
        lines.append(f"- 家长关注/原话：{r.get('家长关注/原话', '')}")
        url = r.get("出处链接", "")
        lines.append(f"- 出处：{url}")
        lines.append("")
    return "\n".join(lines)


def sync(radar_out: str = DEFAULT_RADAR_OUT, dry: bool = False) -> dict:
    os.makedirs(INTEL_DIR, exist_ok=True)
    today = time.strftime("%Y-%m-%d")
    report = {"hot": False, "compare_seed": False}

    path = latest_radar(radar_out)
    if path:
        md = build_hot_markdown(path, today)
        if md:
            report["hot"] = True
            print(f"[sync] 同步热点话题：{path} → 热点话题.md")
            if not dry:
                with open(HOT_FILE, "w", encoding="utf-8") as f:
                    f.write(md)
    else:
        print(f"[sync] 未找到 radar_*.md（{radar_out}），热点话题保持现状", file=sys.stderr)

    if not os.path.exists(COMPARE_FILE):
        seed = seed_compare_from_json(radar_out, today)
        if seed:
            report["compare_seed"] = True
            print(f"[sync] 生成中新教育对比种子：edu_compare.json → 中新教育对比.md")
            if not dry:
                with open(COMPARE_FILE, "w", encoding="utf-8") as f:
                    f.write(seed)
    return report


def main():
    dry = "--dry-run" in sys.argv
    radar_out = DEFAULT_RADAR_OUT
    if "--radar-output" in sys.argv:
        radar_out = sys.argv[sys.argv.index("--radar-output") + 1]
    report = sync(radar_out, dry=dry)
    mode = "（dry-run 未写盘）" if dry else ""
    print(f"[sync] 完成{mode}：热点话题 {'已更新' if report['hot'] else '未变'}、"
          f"中新教育对比种子 {'已生成' if report['compare_seed'] else '已存在/无来源'}")


if __name__ == "__main__":
    main()
