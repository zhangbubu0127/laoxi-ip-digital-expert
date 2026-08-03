import json, os, time, uuid

_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "workspace", "上下文")


def _ensure_dir():
    os.makedirs(_DIR, exist_ok=True)


def put(chat_id: str, role: str, task: str, context: str) -> str:
    token = uuid.uuid4().hex[:12]
    _ensure_dir()
    path = os.path.join(_DIR, f"{chat_id}_{role}_{token}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"chat_id": chat_id, "role": role, "task": task,
                   "context": context, "ts": time.time()}, f, ensure_ascii=False)
    return token


def take(chat_id: str, role: str):
    _ensure_dir()
    prefix = f"{chat_id}_{role}_"
    candidates = [os.path.join(_DIR, n) for n in os.listdir(_DIR)
                  if n.startswith(prefix) and n.endswith(".json")]
    if not candidates:
        return None
    path = max(candidates, key=os.path.getmtime)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        os.unlink(path)
        return data.get("task", ""), data.get("context", "")
    except (OSError, json.JSONDecodeError):
        return None


def sweep(older_than: float = 600.0) -> None:
    _ensure_dir()
    now = time.time()
    for n in os.listdir(_DIR):
        path = os.path.join(_DIR, n)
        try:
            if now - os.path.getmtime(path) > older_than:
                os.unlink(path)
        except OSError:
            pass


def save_basis(chat_id: str, role: str, topics: str, basis: str) -> None:
    _ensure_dir()
    with open(os.path.join(_DIR, f"{chat_id}_{role}_basis.json"), "w", encoding="utf-8") as f:
        json.dump({"topics": topics, "basis": basis, "ts": time.time()}, f, ensure_ascii=False)


def load_basis(chat_id: str, role: str):
    path = os.path.join(_DIR, f"{chat_id}_{role}_basis.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("topics", ""), data.get("basis", "")
    except (OSError, json.JSONDecodeError):
        return "", ""
