import json, os, urllib.request

_SECRETS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "secrets.json")
_API_URL = "https://api.deepseek.com/chat/completions"

def load_secrets() -> dict:
    with open(_SECRETS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def generate(system: str, user: str, max_tokens: int = 2000, temperature: float = 0.8) -> str:
    secrets = load_secrets()
    payload = {
        "model": secrets["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    req = urllib.request.Request(
        _API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + secrets["deepseek_api_key"],
        },
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data["choices"][0]["message"]["content"]
