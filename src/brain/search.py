import json, re, urllib.parse, urllib.request

from log import get_logger

_API = "https://zh.wikipedia.org/w/api.php"
_HEADERS = {"User-Agent": "laoxi-agent/1.0"}
_log = get_logger("search")

def _http(url: str) -> str:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.read().decode("utf-8")

def verify(query: str, top: int = 2) -> list[dict]:
    try:
        params = urllib.parse.urlencode({
            "action": "query", "list": "search", "srsearch": query,
            "format": "json", "utf8": "1", "srlimit": str(top),
        })
        data = json.loads(_http(f"{_API}?{params}"))
        hits = data.get("query", {}).get("search", [])
        return [{
            "title": h.get("title", ""),
            "snippet": _strip_html(h.get("snippet", "")),
            "url": "https://zh.wikipedia.org/wiki/" + urllib.parse.quote(h.get("title", "")),
        } for h in hits[:top]]
    except Exception as e:
        _log.error("外搜失败 %r: %s", query, e)
        return []

def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)
