import os

_LEDGER_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "memory", "token账本.md")

class CircuitBreaker:
    def __init__(self, limit: int = 100_000_000):
        self.limit = limit
        self.spent = 0

    def record(self, tokens: int) -> bool:
        self.spent += tokens
        self._persist()
        return self.spent >= self.limit

    def is_tripped(self) -> bool:
        return self.spent >= self.limit

    def _persist(self) -> None:
        with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
            f.write(f"累计token: {self.spent}\n熔断阈值: {self.limit}\n")

    def summary(self) -> str:
        return f"累计token {self.spent}，阈值 {self.limit}"
