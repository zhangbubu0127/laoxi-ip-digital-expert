# -*- coding: utf-8 -*-
"""把《常见提问》CSV + 清洗工具知识库.csv 转换成 knowledge/家长常问与成单解答/ 的 8 个分类 md + 典型问题清单.md。"""
import csv, os

SRC_A = "/Users/User/Downloads/常见提问 - Sheet1.csv"
SRC_B = "/Users/User/Desktop/结构化数据清洗工具/产出/知识库.csv"
OUT = os.path.join(os.path.dirname(__file__), "..", "knowledge", "家长常问与成单解答")

CATS = ["路径与升学", "学术与能力", "学历与认证", "费用与价值",
        "安全与监管", "学生管理与适应性", "就业与发展", "信任与案例"]

_CAT_PREFIX = {"一": "路径与升学", "二": "学术与能力", "三": "学历与认证", "四": "费用与价值",
               "五": "安全与监管", "六": "学生管理与适应性", "七": "就业与发展", "八": "信任与案例"}


def norm_cat(name: str) -> str:
    name = name.strip()
    if name in CATS:
        return name
    if "、" in name:
        key = name.split("、", 1)[0].strip()
        if key in _CAT_PREFIX:
            return _CAT_PREFIX[key]
    return name


def read_questions() -> dict:
    qs = {c: [] for c in CATS}
    cur = None
    with open(SRC_A, encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if not r or not any(r):
                continue
            if r[0] and r[0].strip():
                cur = norm_cat(r[0]) if norm_cat(r[0]) in CATS else cur
            q = r[2].strip() if len(r) > 2 else ""
            if cur in qs and q:
                qs[cur].append(q)
    return qs


def read_qas() -> dict:
    qas = {c: [] for c in CATS}
    other = []
    with open(SRC_B, encoding="utf-8-sig") as f:
        for r in csv.reader(f):
            if len(r) < 3 or not r[0].strip():
                continue
            q, a, tags = r[0].strip(), r[1].strip(), r[2]
            source = r[5].strip() if len(r) > 5 and r[5].strip() else ""
            adv = r[8].strip() if len(r) > 8 and r[8].strip() else ""
            primary = None
            for t in tags.replace("；", ";").replace("、", ";").split(";"):
                t = t.strip().split("/")[0].strip()
                if t in CATS:
                    primary = t
                    break
            if primary:
                qas[primary].append((q, a, adv, source))
            else:
                other.append(q[:40])
    return qas, other


def render_cat_md(cat, questions, qas) -> str:
    lines = [f"# {cat}", "",
             f"> 家长常问与成单解答 · 分类：{cat}",
             f"> 典型问题：来自《常见提问》分类清单（选题线索）",
             f"> 成单解答：已成单顾问电话录音口径（结构化清洗工具知识库，{len(qas)} 条）",
             "> 用途：选题来源 + 写作/审核事实口径；费用数字以「业务事实/费用数据.md」为准", ""]
    lines += ["## 典型问题（选题线索）", ""]
    for i, q in enumerate(questions, 1):
        lines.append(f"{i}. {q}")
    lines += ["", "## 成单解答（顾问口径）", ""]
    for q, a, adv, source in qas:
        lines.append(f"### Q：{q}")
        lines.append("")
        lines.append(f"A：{a}")
        lines.append("")
        lines.append(f"（来源：{'顾问 ' + adv if adv else '顾问'} · {source if source else '电话录音'}）")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_question_list_md(questions) -> str:
    lines = ["# 家长典型问题清单", "",
             "> 来源：《常见提问 - Sheet1.csv》典型问题，按 8 大分类组织",
             "> 用途：席小题出选题时的选题原料——家长真正在意什么", ""]
    for cat in CATS:
        lines.append(f"## {cat}")
        lines.append("")
        for i, q in enumerate(questions[cat], 1):
            lines.append(f"{i}. {q}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    questions = read_questions()
    qas, other = read_qas()
    for cat in CATS:
        md = render_cat_md(cat, questions[cat], qas[cat])
        with open(os.path.join(OUT, f"{cat}.md"), "w", encoding="utf-8") as f:
            f.write(md)
    with open(os.path.join(OUT, "典型问题清单.md"), "w", encoding="utf-8") as f:
        f.write(render_question_list_md(questions))
    print("=== 生成完成 ===")
    for cat in CATS:
        print(f"{cat}: 典型问题{len(questions[cat])}条 / 成单解答{len(qas[cat])}条")
    total = sum(len(v) for v in qas.values())
    print(f"成单解答合计: {total}")
    if other:
        print("未归入8分类的解答(前10):", other[:10])


if __name__ == "__main__":
    main()
