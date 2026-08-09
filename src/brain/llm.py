import json, os, time, urllib.request

from log import get_logger

_SECRETS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "secrets.json")
_API_URL = "https://api.deepseek.com/chat/completions"
_log = get_logger("llm")

def load_secrets() -> dict:
    with open(_SECRETS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def generate(system: str, user: str, max_tokens: int = 8000, temperature: float = 0.8) -> str:
    secrets = load_secrets()

    def _request(mt: int, user_msg: str = None) -> urllib.request.Request:
        payload = {
            "model": secrets["model"],
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg or user},
            ],
            "max_tokens": mt,
            "temperature": temperature,
        }
        return urllib.request.Request(
            _API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + secrets["deepseek_api_key"],
            },
        )

    start = time.monotonic()
    req = _request(max_tokens)
    data = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            break
        except Exception as e:
            if attempt == 2:
                _log.error("LLM 请求失败: %s", e)
                raise
            _log.warning("LLM 请求异常（第%d次），重试: %s", attempt + 1, e)
            time.sleep(2 * (attempt + 1))
    if data is None:
        raise RuntimeError("LLM 请求连续失败")
    choice = data["choices"][0]
    content = choice["message"]["content"]
    usage = data.get("usage", {})
    finish = choice.get("finish_reason")
    _log.info("LLM 调用 model=%s finish=%s usage=%s 耗时=%.1fs",
              secrets["model"], finish, usage, time.monotonic() - start)
    if content and finish != "length":
        return content
    # 输出为空或被截断（推理模型 thinking 吃满预算只剩推理 / 回答超 max_tokens）：带「直接输出」提示、加大预算重试，最多 2 次
    nudged = user + ("\n\n【系统】上一次回答因推理过程占满输出预算或输出长度被截断，正文没给全。"
                     "请立刻直接输出最终答案正文，不要再展开推理；内容偏长就精简到要点，务必一次给全。")
    _log.warning("LLM 输出为空或截断（finish=%s usage=%s），带提示重试", finish, usage)
    for attempt in range(2):
        time.sleep(2 * (attempt + 1))
        mt = min(max_tokens + 4000 * (attempt + 1), 16000)
        try:
            with urllib.request.urlopen(_request(mt, nudged), timeout=120) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            choice = data["choices"][0]
            content = choice["message"]["content"]
            usage = data.get("usage", {})
            _log.info("LLM 重试 model=%s finish=%s usage=%s 耗时=%.1fs",
                      secrets["model"], choice.get("finish_reason"), usage, time.monotonic() - start)
            if content:
                return content
        except Exception as e:
            _log.warning("LLM 重试异常: %s", e)
    raise RuntimeError(f"LLM 输出为空或截断（finish={finish}, usage={usage}）")
