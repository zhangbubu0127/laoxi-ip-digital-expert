import json, os, subprocess

from log import get_logger

_log = get_logger("base_bridge")
_BASE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "base.json")
_PROFILE = "小席"

_FIELD_MAP = [
    ("date", "日期"), ("publish_time", "发布时间"), ("content_type", "内容类型"),
    ("topic", "选题"), ("goal", "目标"), ("status", "状态"),
    ("owner", "负责人"), ("data", "数据回流"),
]


def base_url() -> str:
    try:
        ref = _load_ref()
    except (OSError, ValueError):
        return ""
    return ref.get("base_url", "")


def _load_ref() -> dict:
    with open(_BASE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _run(args: list) -> dict:
    proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    out = proc.stdout.strip()
    return json.loads(out) if out else {}


def _records_to_keymap(data) -> dict:
    d = data.get("data") if isinstance(data, dict) else data
    if not isinstance(d, dict):
        d = {}
    items = d.get("items") or d.get("records")
    if isinstance(items, list) and items and isinstance(items[0], dict):
        keymap = {}
        for it in items:
            rid = it.get("record_id") or it.get("id")
            fields = it.get("fields") or it.get("data") or {}
            date, topic = fields.get("日期", ""), fields.get("选题", "")
            if rid and (date or topic):
                keymap[(str(date), str(topic))] = rid
        return keymap
    values = d.get("data") or []
    fields = d.get("fields") or []
    rids = d.get("record_id_list") or []
    keymap = {}
    for i, row in enumerate(values):
        if not isinstance(row, list):
            continue
        field_map = {fields[j]: row[j] for j in range(min(len(fields), len(row)))}
        rid = rids[i] if i < len(rids) else ""
        date, topic = field_map.get("日期", ""), field_map.get("选题", "")
        if rid and (date or topic):
            keymap[(str(date), str(topic))] = rid
    return keymap


def sync_rows(rows: list) -> None:
    ref = _load_ref()
    token, table = ref["base_token"], ref["table_id"]
    listed = _run(["lark-cli", "base", "+record-list", "--base-token", token,
                   "--table-id", table, "--format", "json", "--profile", _PROFILE])
    keymap = _records_to_keymap(listed)
    for row in rows:
        payload = {cn: str(row.get(ek, "")) for ek, cn in _FIELD_MAP}
        rid = keymap.get((payload["日期"], payload["选题"]))
        args = ["lark-cli", "base", "+record-upsert", "--base-token", token,
                "--table-id", table, "--json", json.dumps(payload, ensure_ascii=False),
                "--profile", _PROFILE]
        if rid:
            args += ["--record-id", rid]
        _run(args)
    _log.info("多维表格同步 %d 行 → %s", len(rows), ref.get("base_url", ""))
