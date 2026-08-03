from collections import OrderedDict

MAX_ROUNDS = 7


class SessionStore:
    def __init__(self, max_rounds=MAX_ROUNDS):
        self.max_rounds = max_rounds
        self._rounds = OrderedDict()

    def add_round(self, chat_id: str, turns: list) -> None:
        if chat_id not in self._rounds:
            self._rounds[chat_id] = []
        self._rounds[chat_id].append(turns)
        self._rounds[chat_id] = self._rounds[chat_id][-self.max_rounds:]
        self._rounds.move_to_end(chat_id)

    def history(self, chat_id: str) -> list:
        return list(self._rounds.get(chat_id, []))

    def clear(self) -> None:
        self._rounds.clear()


store = SessionStore()


def render_history(rounds: list, per_line: int = 200) -> str:
    lines = []
    for rnd in rounds:
        for turn in rnd:
            text = turn["text"].replace("\n", " ").strip()
            if len(text) > per_line:
                text = text[:per_line] + "…"
            lines.append(f"【{turn['speaker']}】{text}")
    return "\n".join(lines)
