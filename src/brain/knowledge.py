import os

_KNOWLEDGE_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "knowledge")

def load_file(rel_path: str) -> str:
    with open(os.path.join(_KNOWLEDGE_DIR, rel_path), "r", encoding="utf-8") as f:
        return f.read()
