class Expert:
    name: str

    def handle(self, task: str) -> str:
        raise NotImplementedError
