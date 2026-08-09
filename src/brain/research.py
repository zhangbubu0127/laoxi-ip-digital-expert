import re

from log import get_logger
from brain import market
from brain.llm import generate as _default_generate

_log = get_logger("research")

# 老板口语前缀，剥掉后留核心查询词（长词在前，避免「调研一下X」只剥掉「调研」）
_PREFIX_RE = re.compile(r"^(帮我调研一下|帮我调研|调研一下|调研|帮我查查|帮我查|查一下|查查|查证|核实|了解一下|了解下)[：:，,\s]*")

_RESEARCH_SYSTEM = (
    "你是【席小题】，老席留学IP团队的选题+调研专家。老板要你针对一个问题做市场调研。\n"
    "下面是实时搜索抓到的网页结果（标题/摘要/链接）。请基于这些结果给调研回答：\n"
    "- 结论先行，大白话，老板/家长听得懂；\n"
    "- 每条关键事实后标注来源链接；\n"
    "- 明确区分「搜到的」和「无法确认的」；搜不到或结果矛盾就说清楚，禁止编造；\n"
    "- 回答 400 字以内，分点清晰，不要长篇大论，不要带【依据】或选题格式；\n"
    "- 若问题涉及国际中本贯通或新加坡留学，各说各的：两者都是老席同时推的项目，不得比较谁好谁坏、不得暗示二选一，打竞品只针对其他留学项目和普通国内中本贯通。"
)


def _queries(question: str) -> list[str]:
    q = _PREFIX_RE.sub("", (question or "").strip())
    if not q:
        return []
    out = [q[:60]]
    for sep in ("和", "与", "、", "，"):
        if sep in q:
            parts = [p.strip()[:40] for p in q.split(sep) if p.strip()]
            if len(parts) > 1:
                out.append(" ".join(parts[:2]))
                break
    return out


def research(question: str, top: int = 5, generate=_default_generate) -> str:
    try:
        return _research(question, top, generate)
    except Exception as e:
        _log.error("调研流程异常 %r: %s", question, e)
        return "调研出错了（我这边异常）。你再说一遍要调研什么，我重试。"


def _research(question: str, top: int, generate) -> str:
    queries = _queries(question)
    if not queries:
        return "没听懂要调研什么，说具体点（比如「调研一下2026新加坡低龄留学政策」）。"
    seen, hits = set(), []
    for q in queries:
        for it in market.search_web(q, top=max(top, 6), sg_filter=False):
            if it.get("title") in seen:
                continue
            seen.add(it["title"])
            hits.append(it)
        if hits:
            break
    if not hits:
        return (f"我试着搜了一圈，暂时没抓到「{question}」的可靠信息（可能被风控或词太冷）。"
                "你把更具体的角度或关键词给我，我再试；也可以让我先凭知识库给你初步看法。")
    block = "\n".join(f"- {i['title']}（{i['snippet'][:120]}）{i['url']}" for i in hits[:top])
    user = (f"【调研问题】\n{question}\n\n【实时搜索结果】\n{block}\n\n请按你的风格给出调研回答，带来源。")
    try:
        return generate(_RESEARCH_SYSTEM, user, max_tokens=3000, temperature=0.4).strip()
    except Exception as e:
        _log.error("调研生成失败: %s", e)
        # LLM 没答出来（截断重试仍失败/连不上）但搜索结果是真实抓到的 → 直接把一手信息给老板，不静默不丢
        return f"调研回答生成没成，先给你搜到的一手信息：\n{block}"
