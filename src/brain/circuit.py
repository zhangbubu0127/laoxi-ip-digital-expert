import os

from log import get_logger

_LEDGER_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "memory", "token账本.md")
_log = get_logger("circuit")

class CircuitBreaker:
    def __init__(self, limit: int = 100_000_000):
        self.limit = limit
        self.spent = self._load()

    def _load(self) -> int:
        try:
            with open(_LEDGER_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("累计token:"):
                        return int(line.split(":", 1)[1].strip())
        except (OSError, ValueError):
            pass
        return 0

    def record(self, tokens: int) -> bool:
        self.spent += tokens
        self._persist()
        if self.spent >= self.limit:
            _log.warning("token 累计 %s 达阈值 %s，熔断", self.spent, self.limit)
        return self.spent >= self.limit

    def is_tripped(self) -> bool:
        return self.spent >= self.limit

    def _persist(self) -> None:
        with open(_LEDGER_PATH, "w", encoding="utf-8") as f:
            f.write(f"累计token: {self.spent}\n熔断阈值: {self.limit}\n")

    def summary(self) -> str:
        return f"累计token {self.spent}，阈值 {self.limit}"
