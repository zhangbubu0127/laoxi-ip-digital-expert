import os

_KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "knowledge")

_STYLE_DIR = os.path.join(_KNOWLEDGE_DIR, "风格")
_DEFAULT_STYLE_USER = "老席"
_LEGACY_PERSONA = "人设/老席创作要求会纪要.md"

_QNA_CATS = ["路径与升学", "学术与能力", "学历与认证", "费用与价值",
             "安全与监管", "学生管理与适应性", "就业与发展", "信任与案例"]
_QNA_KEYWORDS = {
    "费用与价值": ["费用", "预算", "花多少钱", "多少钱", "贵", "学费", "价格", "划算", "值不值", "涨价", "定金", "钱"],
    "路径与升学": ["学校", "升学", "预科", "路径", "留学", "入学", "转轨", "报名", "北大", "北语", "北交", "校区", "初中", "高中", "专科"],
    "学术与能力": ["英语", "成绩", "听不懂", "跟不上", "雅思", "基础", "学习", "考试", "内测", "语言", "课"],
    "学历与认证": ["毕业", "学历", "认证", "文凭", "证书", "考公", "考编", "中留服", "第一学历"],
    "安全与监管": ["安全", "监管", "放心", "人身", "学坏", "独自"],
    "学生管理与适应性": ["适应", "想家", "管得住", "自律", "宿舍", "生活", "吃饭"],
    "就业与发展": ["就业", "工作", "起薪", "薪资", "工资", "硕士", "深造", "回国"],
    "信任与案例": ["信任", "案例", "靠谱", "真假", "骗子", "公司", "机构", "真实"],
}

def style_users() -> list[str]:
    # 可用风格用户：扫描 knowledge/风格/ 子目录，老席兜底永在
    if not os.path.isdir(_STYLE_DIR):
        return [_DEFAULT_STYLE_USER]
    users = [d for d in os.listdir(_STYLE_DIR)
             if os.path.isdir(os.path.join(_STYLE_DIR, d)) and not d.startswith(".")]
    return sorted(set(users + [_DEFAULT_STYLE_USER]))


def load_style(user: str = _DEFAULT_STYLE_USER) -> str:
    # 说话风格库：knowledge/风格/<用户>/创作要求.md；缺失依次回落老席 → 老人设档案，绝不空
    user = (user or _DEFAULT_STYLE_USER).strip()
    for cand in (os.path.join(_STYLE_DIR, user, "创作要求.md"),
                 os.path.join(_STYLE_DIR, _DEFAULT_STYLE_USER, "创作要求.md")):
        if os.path.isfile(cand):
            with open(cand, "r", encoding="utf-8") as f:
                return f.read()
    return load_file(_LEGACY_PERSONA)


def load_file(rel_path: str) -> str:
    with open(os.path.join(_KNOWLEDGE_DIR, rel_path), "r", encoding="utf-8") as f:
        return f.read()

def load_qna(topic: str) -> str:
    cat = _match_cat(topic or "")
    if cat:
        return load_file(f"家长常问与成单解答/{cat}.md")
    parts = []
    for c in _QNA_CATS:
        try:
            parts.append(load_file(f"家长常问与成单解答/{c}.md"))
        except OSError:
            continue
    return "\n\n".join(parts)

def _match_cat(topic: str) -> str:
    best, best_score = "", 0
    for cat, kws in _QNA_KEYWORDS.items():
        score = sum(1 for k in kws if k in topic)
        if score > best_score:
            best, best_score = cat, score
    return best if best_score else ""
