import unittest, tempfile, os
from brain import circuit

class TestCircuit(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
        tmp.close()
        self.path = tmp.name
        self._orig = circuit._LEDGER_PATH
        circuit._LEDGER_PATH = self.path

    def tearDown(self):
        circuit._LEDGER_PATH = self._orig
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_under_limit(self):
        c = circuit.CircuitBreaker(limit=1000)
        self.assertFalse(c.record(600))
        self.assertFalse(c.is_tripped())

    def test_over_limit_trips(self):
        c = circuit.CircuitBreaker(limit=1000)
        self.assertFalse(c.record(600))
        self.assertTrue(c.record(600))
        self.assertTrue(c.is_tripped())

    def test_summary(self):
        c = circuit.CircuitBreaker(limit=1000)
        c.record(600)
        self.assertIn("600", c.summary())

    def test_loads_ledger_on_init(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("累计token: 5000\n熔断阈值: 1000\n")
        c = circuit.CircuitBreaker(limit=1000)
        self.assertEqual(c.spent, 5000)
        self.assertTrue(c.record(600))

    def test_missing_ledger_starts_zero(self):
        os.unlink(self.path)
        c = circuit.CircuitBreaker(limit=1000)
        self.assertEqual(c.spent, 0)

if __name__ == "__main__":
    unittest.main()
