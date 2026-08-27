import json, os, time, urllib.request

from log import get_logger

_SECRETS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "secrets.json")
_API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
_log = get_logger("llm")

def load_secrets() -> dict:
    with open(_SECRETS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def generate(system: str, user: str, max_tokens: int = 8000, temperature: float = 0.8) -> str:
    secrets = load_secrets()

    def _make_request(mt: int, api_url: str, api_key: str, model: str, user_msg: str = None) -> urllib.request.Request:
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg or user},
            ],
            "max_tokens": mt,
            "temperature": temperature,
        }
        return urllib.request.Request(
            api_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + api_key,
            },
        )

    def _call_with_retry(req: urllib.request.Request, label: str) -> dict:
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=180) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as e:
                if attempt == 2:
                    _log.error("%s 请求失败: %s", label, e)
                    raise
                _log.warning("%s 请求异常（第%d次），重试: %s", label, attempt + 1, e)
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(f"{label} 请求连续失败")

    start = time.monotonic()

    primary_model = secrets["model"]
    primary_key = secrets["mimo_api_key"]

    data = None
    active_model = primary_model

    req = _make_request(max_tokens, _API_URL, primary_key, primary_model)
    data = _call_with_retry(req, "MiMo")

    choice = data["choices"][0]
    content = choice["message"]["content"]
    usage = data.get("usage", {})
    finish = choice.get("finish_reason")
    _log.info("LLM 调用 model=%s finish=%s usage=%s 耗时=%.1fs",
              active_model, finish, usage, time.monotonic() - start)
    if finish == "content_filter":
        raise RuntimeError("MiMo 内容安全过滤（content_filter），请求被拒绝")
    if content and finish != "length":
        return content

    # 输出为空或被截断：带「直接输出」提示、加大预算重试，最多 2 次
    nudged = user + ("\n\n【系统】上一次回答因推理过程占满输出预算或输出长度被截断，正文没给全。"
                     "请立刻直接输出最终答案正文，不要再展开推理；内容偏长就精简到要点，务必一次给全。")
    _log.warning("LLM 输出为空或截断（finish=%s usage=%s），带提示重试", finish, usage)

    retry_url, retry_key, retry_label = _API_URL, primary_key, "MiMo"

    for attempt in range(2):
        time.sleep(2 * (attempt + 1))
        mt = min(max_tokens + 4000 * (attempt + 1), 16000)
        try:
            req = _make_request(mt, retry_url, retry_key, active_model, nudged)
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            choice = data["choices"][0]
            content = choice["message"]["content"]
            usage = data.get("usage", {})
            _log.info("LLM 重试 model=%s finish=%s usage=%s 耗时=%.1fs",
                      active_model, choice.get("finish_reason"), usage, time.monotonic() - start)
            if content:
                return content
        except Exception as e:
            _log.warning("LLM 重试异常: %s", e)
    raise RuntimeError(f"LLM 输出为空或截断（finish={finish}, usage={usage}）")
