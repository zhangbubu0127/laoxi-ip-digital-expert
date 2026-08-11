import re

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def match_index(task: str) -> int | None:
    # 老板说「第2个/选题三」等指代 → 取序号，返回 int；没有返回 None
    m = re.search(r"(?:第|选题)\s*([\d一二两三四五六七八九十]+)\s*[个条只]?", task or "")
    if not m:
        return None
    raw = m.group(1)
    return int(raw) if raw.isdigit() else _CN_NUM.get(raw, 0)


def numbered_topics(text: str) -> dict:
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"^\s*(\d+)\s*[.、.．]\s*(.*?)\s*$", line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def basis_map(basis: str) -> dict:
    # 兼容两种依据格式：旧「N. 依据」单行；新「（选题N依据，来源：…）\n依据段落」块状（LLM 自然飘出，未锁死行格式）
    if not basis:
        return {}
    out = {}
    blocks = re.split(r"（\s*选题\s*(\d+)\s*依据[^）]*）", basis)
    if len(blocks) > 1:
        for i in range(1, len(blocks), 2):
            if i + 1 < len(blocks):
                out[int(blocks[i])] = blocks[i + 1].strip()
        if out:
            return out
    for line in (basis or "").splitlines():
        m = re.match(r"^\s*(\d+)\s*[.、.．]\s*(.*?)\s*$", line)
        if m:
            out[int(m.group(1))] = m.group(2).strip()
    return out


def strip_type(text: str) -> str:
    return re.sub(r"^【[^】]*】\s*", "", text or "")


def paired_topics(topics: str, basis: str) -> list[tuple[int, str, str]]:
    tmap = numbered_topics(topics)
    bmap = basis_map(basis)
    return [(k, tmap.get(k, "（未给出题目）"), bmap.get(k, "（未给出依据）"))
            for k in sorted(set(tmap) | set(bmap))]


def item_reply(num: int, topic: str, reason: str) -> str:
    # 选题名与依据分两行，避免一长句糊在一起
    return f"选题{num}「{strip_type(topic)}」\n依据：{reason}"
