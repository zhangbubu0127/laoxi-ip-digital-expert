class Expert:
    name: str

    def handle(self, task: str, context: str = "") -> str:
        raise NotImplementedError

    def explain(self, question: str, context: str = "") -> str:
        return self.handle(question, context)
