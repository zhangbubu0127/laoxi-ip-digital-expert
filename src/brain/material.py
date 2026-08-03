import os, re
from datetime import datetime

_MATERIAL_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "workspace", "素材")

def material_dir() -> str:
    os.makedirs(_MATERIAL_DIR, exist_ok=True)
    return _MATERIAL_DIR

def save_material(content: str, source: str = "群聊") -> str:
    content = content.strip()
    if not content:
        raise ValueError("素材内容为空")
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    name = _next_filename(stamp)
    path = os.path.join(material_dir(), name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# 素材 {name}\n\n- 来源：{source}\n- 时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n{content}\n")
    return name

def _next_filename(stamp: str) -> str:
    seq = 0
    while True:
        suffix = f"-{seq}" if seq else ""
        name = f"{stamp}{suffix}.md"
        if not os.path.exists(os.path.join(material_dir(), name)):
            return name
        seq += 1

def recent_materials(n: int = 5, per_file_chars: int = 800) -> str:
    if not os.path.isdir(_MATERIAL_DIR):
        return "（暂无素材）"
    names = [f for f in os.listdir(_MATERIAL_DIR) if f.endswith(".md")]
    files = sorted(
        names, key=lambda f: os.path.getmtime(os.path.join(_MATERIAL_DIR, f)), reverse=True
    )[:n]
    if not files:
        return "（暂无素材）"
    blocks = []
    for name in files:
        try:
            with open(os.path.join(_MATERIAL_DIR, name), "r", encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        body = re.sub(r"^# .*\n", "", text, count=1).strip()
        body = body.replace("\n", " ")
        if len(body) > per_file_chars:
            body = body[:per_file_chars] + "…"
        blocks.append(f"【素材·{name}】{body}")
    return "\n".join(blocks)
